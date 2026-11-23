"""Discover instruction units and retain byte-exact Markdown evidence."""

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt

from .ir import Guard, Name, Record
from pydantic import Field, field_validator
from typing import Literal


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Manifest(Record):
    version: Literal[1] = 1
    name: Name = "directory"
    window: str = "shared decision"
    receiver: Name = "agent"
    evidence: str = "assumed"
    skills: list[str] | None = None
    always_visible: list[str] = Field(default_factory=list)
    catalog: Literal["names-and-descriptions-visible"] = (
        "names-and-descriptions-visible"
    )
    bodies: Literal["any-subset", "all-visible"] = "any-subset"
    domains: dict[Name, list[Name]] = Field(default_factory=dict)
    glossary: list[str] = Field(default_factory=list)
    constraints: list[Guard] = Field(default_factory=list)

    @field_validator("constraints", mode="before")
    @classmethod
    def expand_visibility_paths(cls, constraints):
        def convert(tree):
            if not isinstance(tree, dict):
                return tree
            if (
                "visible" in tree
                and isinstance(tree["visible"], str)
                and ":" in tree["visible"]
            ):
                part, path = tree["visible"].split(":", 1)
                if part not in ("body", "catalog", "reference"):
                    raise ValueError("unknown disclosure kind")
                return {"visible": unit_id(path, part)}
            if "not" in tree:
                return {**tree, "not": convert(tree["not"])}
            for key in ("all", "any"):
                if key in tree:
                    return {**tree, key: [convert(child) for child in tree[key]]}
            return tree

        return [convert(tree) for tree in constraints]


def unit_id(path, part):
    return part + "_" + digest(path.encode())[:16]


def modality(text):
    # Recognize a single operative construction. Compound or scoped modal
    # constructions go to extraction, where disagreement remains reviewable.
    text = text.strip().lower().rstrip(".")
    if re.search(r'["“”`]|\b(and|or|unless|except|however)\b|[;.!?]', text):
        return None
    if re.search(
        r"\b(must have|must be|required to be|not required to|requires? no|not only|not necessarily|need not)\b",
        text,
    ):
        return None
    verb = r"[a-z]+\b"
    if re.fullmatch(
        r"(?:if [^,]+, )?(?:(?:the|an?) agent|you) must not " + verb + r".*", text
    ):
        return "forbid"
    if re.fullmatch(
        r"(?:if [^,]+, )?(?:(?:the|an?) agent|you) must " + verb + r".*", text
    ):
        return "must"
    if re.fullmatch(r"never [a-z]+ .+", text):
        return "forbid"
    if re.fullmatch(r"(?:this|the) policy (?:forbids|prohibits) .+", text):
        return "forbid"
    if re.fullmatch(r"(?:this|the) policy requires (?:the agent|you) to .+", text):
        return "must"
    if re.fullmatch(r"[a-z]+ing .+ requires [a-z]+ing .+", text):
        return "requires"
    return None


def scan(root, manifest):
    root = root.resolve(strict=True)
    pending = (
        [root / f for f in manifest.skills]
        if manifest.skills is not None
        else sorted(root.rglob("SKILL.md"))
    )
    if not pending:
        raise ValueError("no SKILL.md files selected")
    pending.extend(root / name for name in manifest.always_visible)
    documents, spans, links, anchors = {}, [], [], {}
    parser = MarkdownIt("commonmark").enable("table")
    while pending:
        path = pending.pop(0).resolve(strict=True)
        rel = path.relative_to(root).as_posix()
        if rel in documents:
            continue
        data = path.read_bytes()
        text = data.decode("utf-8")
        lines = text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line.encode()))
        is_skill = path.name == "SKILL.md"
        body = unit_id(rel, "body" if is_skill else "reference")
        docs = documents[rel] = {"digest": digest(data), "body": body, "text": text}
        anchors[rel] = set()
        heading = []

        def add(start, end, kind, unit, content=None, example=False):
            quote = "".join(lines[start:end]).rstrip("\r\n")
            value = content if content is not None else quote
            identifier = unit_id(rel, "s") + "_" + str(start + 1)
            spans.append(
                dict(
                    id=identifier,
                    path=rel,
                    line=start + 1,
                    start=offsets[start],
                    end=offsets[start] + len(quote.encode()),
                    quote=quote,
                    text=value,
                    unit=unit,
                    kind=kind,
                    heading=list(heading),
                    fixed=modality(value) if not example else None,
                    example=example,
                )
            )

        start = 0
        if is_skill:
            if not lines or lines[0].strip() != "---":
                raise ValueError(f"{rel}: missing YAML frontmatter")
            start = next(
                (i + 1 for i, line in enumerate(lines[1:], 1) if line.strip() == "---"),
                0,
            )
            if not start:
                raise ValueError(f"{rel}: unterminated YAML frontmatter")
            front = "".join(lines[1 : start - 1])
            meta = yaml.safe_load(front)
            if not isinstance(meta, dict) or not all(
                isinstance(meta.get(k), str) and meta[k].strip()
                for k in ("name", "description")
            ):
                raise ValueError(f"{rel}: invalid skill name or description")
            docs.update(
                name=meta["name"],
                description=meta["description"],
                catalog=unit_id(rel, "catalog"),
            )
            for key, value in yaml.compose(front).value:
                if key.value == "description":
                    first = key.start_mark.line + 1
                    last = value.end_mark.line + 1 + (value.end_mark.column > 0)
                    add(
                        first, last, "description", docs["catalog"], meta["description"]
                    )
        tokens = parser.parse("".join(lines[start:]))
        covered = set()
        quoted = 0
        for i, token in enumerate(tokens):
            if token.type == "blockquote_open":
                quoted += 1
            if token.type == "blockquote_close":
                quoted -= 1
            if token.type == "heading_open":
                level = int(token.tag[1:])
                heading = heading[: level - 1] + [tokens[i + 1].content]
                anchors[rel].add(
                    re.sub(r"[^\w -]", "", heading[-1].lower()).replace(" ", "-")
                )
            if token.type == "inline":
                for child in token.children or []:
                    if child.type != "link_open":
                        continue
                    href = child.attrGet("href") or ""
                    uri = urlsplit(href)
                    link = dict(source=rel, href=href, status="inventory")
                    links.append(link)
                    if uri.scheme or uri.netloc:
                        continue
                    target = (
                        (path.parent / unquote(uri.path)).resolve()
                        if uri.path
                        else path
                    )
                    if not target.is_relative_to(root) or not target.is_file():
                        link["status"] = "unavailable"
                    elif target.suffix.lower() == ".md":
                        link.update(
                            target=target.relative_to(root).as_posix(),
                            anchor=unquote(uri.fragment),
                            status="resolved",
                        )
                        pending.append(target)
            if (
                token.type
                not in ("inline", "fence", "code_block", "html_block", "tr_open")
                or not token.map
            ):
                continue
            a, b = (n + start for n in token.map)
            if a in covered:
                continue
            covered.update(range(a, b))
            example = (
                bool(quoted)
                or token.type in ("fence", "code_block")
                or any(re.search(r"\bexamples?\b", h, re.I) for h in heading)
            )
            kind = "heading" if i and tokens[i - 1].type == "heading_open" else "body"
            add(
                a,
                b,
                kind,
                body,
                token.content if token.type == "inline" else None,
                example,
            )
        for n in range(start, len(lines)):
            if n not in covered and lines[n].strip():
                add(n, n + 1, "body", body)
    for link in links:
        if link.get("anchor") and link["anchor"] not in anchors[link["target"]]:
            link["status"] = "missing-anchor"
    return dict(documents=documents, spans=spans, links=links)
