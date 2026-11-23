"""Source-bound formalization, review overlays and deterministic offline replay."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
import re
from pathlib import Path

from .checker import analyze
from .extraction import Extraction, Verification, atomic_json, fingerprint
from .ir import Package
from .sources import Manifest, scan

RUN_VERSION = "markdown-1"


def index(rows, ids, label):
    counts = Counter(r.source for r in rows)
    if set(counts) != set(ids) or any(n != 1 for n in counts.values()):
        raise ValueError(f"{label}: expected exactly one record for every source span")
    return {r.source: r.model_dump() for r in rows}


def review_base(run):
    # Receipts describe an execution. A cache hit on identical source, prompts,
    # model and output must preserve the review anchor.
    return fingerprint(
        {**run, "receipts": [(r["key"], r["model"]) for r in run["receipts"]]}
    )


def extract(root, manifest, provider):
    source = scan(root, manifest)
    spans = {s["id"]: s for s in source["spans"]}
    if len(spans) != len(source["spans"]):
        raise ValueError("source IDs are not unique")
    definitions = {
        s["id"]
        for s in spans.values()
        if not s["example"]
        and (
            any(
                re.search(r"\b(definitions?|glossary|terminology)\b", h, re.I)
                for h in s["heading"]
            )
            or re.search(
                r"\b(means|refers to|is distinct from|is synonymous with|is defined as)\b",
                s["text"],
                re.I,
            )
        )
    }
    rules = []
    dispositions = {}
    for path in source["documents"]:
        relevant = {path}
        while True:
            targets = {
                link["target"]
                for link in source["links"]
                if link["source"] in relevant and link.get("target")
            }
            if targets <= relevant:
                break
            relevant.update(targets)
        items = [s for s in spans.values() if s["path"] == path]
        ids = {s["id"] for s in items}
        if not ids:
            continue
        payload = dict(
            profile=manifest.model_dump(by_alias=True),
            documents={p: source["documents"][p]["digest"] for p in sorted(relevant)},
            targets=items,
            reference_material=[
                s
                for s in spans.values()
                if s["path"] != path
                and (s["path"] in relevant or s["id"] in definitions)
            ],
        )
        result = provider.request("extract", payload, Extraction)
        dispositions.update(index(result.dispositions, ids, "extraction"))
        for fixed, entries in (
            (True, result.fixed_bindings),
            (False, result.inferred_rules),
        ):
            for entry in entries:
                if entry.source not in ids:
                    raise ValueError("rule points outside target document")
                s = spans[entry.source]
                if s["example"] or fixed != bool(s["fixed"]):
                    raise ValueError(
                        f"{entry.source}: model changed protected source classification"
                    )
                fields = entry.model_dump()
                fields.update(
                    modality=s["fixed"] if fixed else entry.modality,
                    origin="deterministic" if fixed else "inferred",
                )
                rules.append(fields)
        for sid in ids:
            has_rule = any(r["source"] == sid for r in rules)
            if has_rule != (dispositions[sid]["kind"] in ("modeled", "partial")):
                raise ValueError(f"{sid}: rule and disposition disagree")
    package, registry, provenance = compile_rules(source, manifest, rules)
    reconstructions = [
        dict(
            id=r.id,
            text=render_rule(r.model_dump(by_alias=True)),
            evidence=provenance[r.id],
        )
        for r in package.rules
    ]
    verification = provider.request(
        "verify",
        dict(
            profile=manifest.model_dump(by_alias=True),
            spans=list(spans.values()),
            dispositions=dispositions,
            rules=rules,
            registry=registry,
            reconstructions=reconstructions,
        ),
        Verification,
    )
    judgments = index(verification.judgments, spans, "verification")
    suspected = [item.model_dump() for item in verification.suspected]
    for item in suspected:
        if (
            not item["sources"]
            or not set(item["sources"]) <= spans.keys()
            or not item["assumption"].strip()
        ):
            raise ValueError(
                "suspected conflict lacks source evidence or an assumption"
            )
    return dict(
        version=RUN_VERSION,
        source=source,
        manifest=manifest.model_dump(by_alias=True),
        interpretations=rules,
        dispositions=dispositions,
        judgments=judgments,
        suspected=suspected,
        receipts=provider.receipts,
        model=provider.model,
        ir=package.model_dump(by_alias=True),
        provenance=provenance,
    )


def compile_rules(source, manifest, interpretations):
    spans = {s["id"]: s for s in source["spans"]}
    docs = source["documents"]
    domains = deepcopy(manifest.domains)
    domains.setdefault("actor", [manifest.receiver])
    registry = {"actions": {}, "predicates": {}}
    rules = []
    provenance = {}
    counter = Counter()
    override_targets = {}
    for fields in interpretations:
        sid = fields["source"]
        if sid not in spans:
            raise ValueError("unknown source ID")
        span = spans[sid]
        counter[sid] += 1
        rid = f"{sid}_{counter[sid]}"
        variables = {}
        evidence = {sid, *fields["supporting"]}
        mapping = []

        def bind(call, kind):
            if not call["evidence"] or not call["wording"].strip():
                raise ValueError(f"{rid}: missing binding evidence")
            new = set(call["evidence"])
            if not new <= spans.keys():
                raise ValueError(f"{rid}: unknown evidence")
            if not any(
                call["wording"].casefold() in spans[k]["text"].casefold() for k in new
            ):
                raise ValueError(
                    f"{rid}: binding wording must quote one of its evidence spans"
                )
            evidence.update(new)
            key = call["symbol"]
            sorts = [a["sort"] for a in call["args"]]
            if kind == "actions" and (not sorts or sorts[0] != "actor"):
                raise ValueError(f"{key}: first action argument must be an actor")
            if registry[kind].setdefault(key, sorts) != sorts:
                raise ValueError(f"{key}: incompatible signatures")
            args = []
            for arg in call["args"]:
                value, sort = arg["value"], arg["sort"]
                if sort not in domains:
                    domains[sort] = [sort + "_1"]
                if value.startswith("$"):
                    if variables.setdefault(value[1:], sort) != sort:
                        raise ValueError("variable sort changed")
                elif value not in domains[sort]:
                    if sort in manifest.domains:
                        raise ValueError(f"{value}: outside declared {sort} domain")
                    domains[sort].append(value)
                args.append(value)
            mapping.append(
                dict(symbol=key, wording=call["wording"], evidence=call["evidence"])
            )
            return {"action" if kind == "actions" else "predicate": key, "args": args}

        def conjunction(group):
            if not set(group["evidence"]) <= spans.keys():
                raise ValueError("unknown guard evidence")
            factors = [{"visible": spans[key]["unit"]} for key in group["evidence"]]
            for item in group["facts"]:
                # Qualification evidence affects this guard only.
                before = evidence.copy()
                fact = bind(item["fact"], "predicates")
                factors.extend(
                    {"visible": spans[key]["unit"]} for key in item["fact"]["evidence"]
                )
                evidence.intersection_update(before)
                factors.append({"not": fact} if item["negated"] else fact)
            return {"all": factors}

        action = bind(fields["action"], "actions")
        mode = fields["modality"]
        if mode == "requires":
            effect = {
                "requires": action,
                "anyOf": [bind(c, "actions") for c in fields["alternatives"]],
            }
        elif mode in ("must", "forbid") and not fields["alternatives"]:
            effect = {mode: action}
        else:
            raise ValueError(f"{rid}: invalid effect")
        when = [conjunction(g) for g in fields["when"]]
        exceptions = [conjunction(g) for g in fields["exceptions"]]
        if not evidence <= spans.keys():
            raise ValueError("unknown supporting evidence")
        guard = {
            "all": [
                *([{"any": when}] if when else []),
                *(
                    {"visible": u}
                    for u in sorted(
                        {spans[k]["unit"] for k in evidence} - {span["unit"]}
                    )
                ),
            ]
        }
        rules.append(
            dict(
                id=rid,
                unit=span["unit"],
                source={k: span[k] for k in ("line", "path", "quote")},
                variables=variables,
                guard=guard,
                exceptions=exceptions,
                effect=effect,
                overrides=[],
            )
        )
        extra = {
            k
            for group in fields["when"] + fields["exceptions"]
            for k in group["evidence"]
        }
        all_evidence = evidence | extra | {k for m in mapping for k in m["evidence"]}
        provenance[rid] = dict(
            source=sid,
            modality=fields["origin"],
            bindings=mapping,
            supporting=sorted(all_evidence),
        )
        override_targets[rid] = fields["overrides"]
    for rule in rules:
        for sid in override_targets[rule["id"]]:
            if counter[sid] != 1:
                raise ValueError("override target missing or ambiguous")
            rule["overrides"].append(f"{sid}_1")
    units = sorted({d[k] for d in docs.values() for k in ("body", "catalog") if k in d})
    constraints = [
        g if isinstance(g, bool) else g.model_dump(by_alias=True)
        for g in manifest.constraints
    ]
    constraints.extend(
        {"visible": docs[path]["body"]} for path in manifest.always_visible
    )
    constraints.extend(
        {"visible": d["catalog"]} for d in docs.values() if "catalog" in d
    )
    if manifest.bodies == "all-visible":
        constraints.extend(
            {"visible": d["body"]} for d in docs.values() if "catalog" in d
        )
    profile = dict(
        name=manifest.name,
        receiver=manifest.receiver,
        window=manifest.window,
        evidence=manifest.evidence,
        units=units,
        constraints=constraints,
    )
    package = Package.model_validate(
        dict(version="0.1", profile=profile, domains=domains, **registry, rules=rules)
    )
    return package, registry, provenance


def expression(tree):
    if isinstance(tree, bool):
        return str(tree)
    for key in ("action", "predicate"):
        if key in tree:
            return tree[key] + "(" + ", ".join(tree["args"]) + ")"
    if "visible" in tree:
        return "visible " + tree["visible"]
    if "not" in tree:
        return "not (" + expression(tree["not"]) + ")"
    key = next(iter(tree))
    return (" and " if key == "all" else " or ").join(
        "(" + expression(x) + ")" for x in tree[key]
    ) or str(key == "all")


def render_rule(rule):
    effect = rule["effect"]
    if "requires" in effect:
        text = (
            expression(effect["requires"])
            + " requires "
            + " or ".join(expression(a) for a in effect["anyOf"])
        )
    else:
        mode = next(iter(effect))
        text = mode + " " + expression(effect[mode])
    scope = ", ".join(f"{v} in {sort}" for v, sort in rule["variables"].items())
    return (
        (("for each " + scope + ": ") if scope else "")
        + text
        + " when "
        + expression(rule["guard"])
        + "".join(" except " + expression(e) for e in rule["exceptions"])
        + (f" overrides {rule['overrides']}" if rule["overrides"] else "")
    )


def save_run(folder, run):
    path = Path(folder) / "run.json"
    if path.exists():
        raise ValueError(f"run already exists: {path}")
    atomic_json(path, {"digest": fingerprint(run), "run": run}, exclusive=True)


def read_run(folder):
    snapshot = json.loads((Path(folder) / "run.json").read_text())
    if (
        snapshot["digest"] != fingerprint(snapshot["run"])
        or snapshot["run"]["version"] != RUN_VERSION
    ):
        raise ValueError("invalid run digest or version")
    return snapshot["run"]


def apply_overlay(run, overlay):
    if overlay["base"] != review_base(run):
        raise ValueError("stale review overlay")
    revised = deepcopy(run)
    for edit in overlay["edits"]:
        sid = edit["source"]
        if sid not in revised["dispositions"] or not edit["reason"].strip():
            raise ValueError("invalid review target or reason")
        replacement = Extraction.model_validate(edit["replacement"])
        if (
            len(replacement.dispositions) != 1
            or replacement.dispositions[0].source != sid
        ):
            raise ValueError("review must cover its target")
        span = next(s for s in run["source"]["spans"] if s["id"] == sid)
        rows = []
        for fixed, entries in (
            (True, replacement.fixed_bindings),
            (False, replacement.inferred_rules),
        ):
            for entry in entries:
                if (
                    entry.source != sid
                    or fixed != bool(span["fixed"])
                    or span["example"]
                ):
                    raise ValueError("invalid review binding")
                rows.append(
                    {
                        **entry.model_dump(),
                        "modality": span["fixed"] if fixed else entry.modality,
                        "origin": "reviewed",
                    }
                )
        if bool(rows) != (replacement.dispositions[0].kind in ("modeled", "partial")):
            raise ValueError("review disposition disagrees with rule count")
        revised["interpretations"] = [
            r for r in revised["interpretations"] if r["source"] != sid
        ] + rows
        revised["dispositions"][sid] = replacement.dispositions[0].model_dump()
        revised["suspected"] = [
            item for item in revised["suspected"] if sid not in item["sources"]
        ]
        revised["judgments"][sid] = dict(
            source=sid, verdict="reviewed", reason=edit["reason"]
        )
    revised_package, _, revised["provenance"] = compile_rules(
        revised["source"],
        Manifest.model_validate(revised["manifest"]),
        revised["interpretations"],
    )
    revised["ir"] = revised_package.model_dump(by_alias=True)
    revised["overlay"] = overlay
    return revised


def report(run, limit=4096, timeout=5000):
    package, registry, provenance = compile_rules(
        run["source"], Manifest.model_validate(run["manifest"]), run["interpretations"]
    )
    if run["ir"] != package.model_dump(by_alias=True):
        raise ValueError("saved IR differs from reconstructed IR")
    result = analyze(package, limit, timeout)
    spans = {s["id"]: s for s in run["source"]["spans"]}
    unresolved = {
        s
        for s, d in run["dispositions"].items()
        if d["kind"] in ("partial", "unresolved")
        or run["judgments"][s]["verdict"] in ("abstain", "disagree")
    }
    unresolved.update(sid for h in run["suspected"] for sid in h["sources"])
    broken = [
        l
        for l in run["source"]["links"]
        if l["status"] in ("missing-anchor", "unavailable")
    ]
    for i, f in enumerate(result["findings"], 1):
        support = set()
        for row in f["core"]:
            meta = provenance[row["rule"]]
            row["interpretation"] = meta
            row["spans"] = [spans[k] for k in meta["supporting"]]
            support.update(meta["supporting"])
        # Unknown qualifications visible in the witness can invalidate a source-level claim.
        uncertain = any(
            f["context"].get("visible:" + spans[k]["unit"]) for k in unresolved
        )
        declared = package.profile.evidence != "assumed"
        f.update(
            id=f"SL-{i:03d}",
            status="established-over-ir",
            actionable=declared and not uncertain and not broken,
            reviewed=all(run["judgments"][k]["verdict"] == "reviewed" for k in support),
        )
    result.update(
        run_id=review_base(run),
        suspected=[
            dict(**h, spans=[spans[sid] for sid in h["sources"]])
            for h in run["suspected"]
        ],
        registry=registry,
        coverage=dict(
            source_units=len(spans),
            dispositions=dict(Counter(d["kind"] for d in run["dispositions"].values())),
            unresolved=len(unresolved),
        ),
        unresolved=[
            dict(
                span=spans[k],
                disposition=run["dispositions"][k],
                verification=run["judgments"][k],
            )
            for k in sorted(unresolved)
        ],
        references=run["source"]["links"],
        receipts=run["receipts"],
    )
    return result
