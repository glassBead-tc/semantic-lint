"""Exercise published bytes, then evaluate the real SemanticLint JSON contract."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp_types import Request, RequestParams
import yaml

from server import GetParams, PublishedSkills, SkillEntry, SkillList

HERE = Path(__file__).resolve().parent


async def check_publication(root):
    transport = StdioServerParameters(
        command=sys.executable, args=[str(HERE / "server.py"), str(root)]
    )
    async with stdio_client(transport) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            discovery = await client.discover()
            if PublishedSkills.identifier not in (
                discovery.capabilities.extensions or {}
            ):
                raise ValueError("server did not advertise the Skills extension")
            listed = await client.send_request(
                Request(method="skills/list", params=RequestParams()), SkillList
            )
            expected = {
                f"skill://retry-server/{p.parent.name}/SKILL.md": p
                for p in root.glob("*/SKILL.md")
            }
            if (
                len(listed.skills) != 2
                or {s["uri"] for s in listed.skills} != expected.keys()
            ):
                raise ValueError("published catalog differs from the two demo sources")
            for entry in listed.skills:
                uri = entry["uri"]
                got = await client.send_request(
                    Request(method="skills/get", params=GetParams(uri=uri)), SkillEntry
                )
                response = await client.read_resource(uri)
                raw = expected[uri].read_bytes()
                meta = yaml.safe_load(raw.decode().split("---", 2)[1])
                manifest = [
                    dict(
                        uri=uri,
                        size=len(raw),
                        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
                    )
                ]
                if (
                    got.skill != entry
                    or entry["frontmatter"] != meta
                    or entry["resources"] != manifest
                    or len(response.contents) != 1
                    or response.contents[0].text.encode() != raw
                ):
                    raise ValueError(
                        f"published bytes, digest, or metadata differ for {uri}"
                    )


def check_retry_witness(finding):
    rows = finding["core"]
    expected = {"recovery/SKILL.md": "must", "payments/SKILL.md": "forbid"}
    if len(rows) != 2 or {r["source"]["path"] for r in rows} != expected.keys():
        raise ValueError("conflict does not cite both demo skills")
    grounded = []
    for row in rows:
        mode = expected[row["source"]["path"]]
        call = row["effect"].get(mode)
        if not call:
            raise ValueError("expected opposing retry requirements")
        args = [
            row["bindings"].get(a[1:], a) if a.startswith("$") else a
            for a in call["args"]
        ]
        grounded.append((call["action"], args))
        if not finding["context"].get("visible:" + row["unit"]):
            raise ValueError("witness requires both skills visible")
        # Check task conditions via their source wording, independent of generated symbol names.
        wanted = "failed" if mode == "must" else "idempotent"
        reads = [
            m
            for m in row["interpretation"]["bindings"]
            if wanted in m["wording"].lower() and m["symbol"] != call["action"]
        ]
        if not any(
            k.startswith(m["symbol"] + "(") and "op1" in k and (mode != "must" or value)
            for m in reads
            for k, value in finding["context"].items()
        ):
            raise ValueError(f"witness is missing the {wanted} condition on op1")
    if grounded[0] != grounded[1] or grounded[0][1] != ["agent", "op1"]:
        raise ValueError("the conflict does not concern the same agent and operation")


def check(root, cache, offline=False):
    asyncio.run(check_publication(root))
    cli = Path(sys.executable).parent / "semanticlint"
    with TemporaryDirectory() as tmp:
        cmd = [
            str(cli),
            "check",
            str(root),
            "--manifest",
            str(HERE / "profile.yml"),
            "--cache",
            str(cache),
            "--format",
            "json",
            "--save-run",
            tmp,
            "--fail-on",
            "actionable-conflict",
        ]
        if offline:
            cmd.append("--offline")
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode not in (0, 1):
            raise ValueError(run.stderr or run.stdout)
        result = json.loads(run.stdout)
        snapshot = json.loads((Path(tmp) / "run.json").read_text())["run"]
        targets = {s["id"] for s in snapshot["source"]["spans"] if s["kind"] == "body"}
        accounted = {r["source"] for r in snapshot["interpretations"]}
        if (
            not targets
            or targets - accounted
            or result["coverage"]["unresolved"]
            or result["status"] != "complete"
            or any(not f["actionable"] for f in result["findings"])
        ):
            raise ValueError(
                "demo did not resolve and verify all operative passages: "
                + json.dumps(result["unresolved"])
            )
        replay = subprocess.run(
            [str(cli), "replay", tmp, "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
        )
        if json.loads(replay.stdout) != result:
            raise ValueError("offline replay disagrees with check")
        print(
            f"{len(result['findings'])} conflict(s), {len(targets)} operative passages, offline replay matched."
        )
        for f in result["findings"]:
            check_retry_witness(f)
            print(f["context"])
            for r in f["core"]:
                print(r["source"])
        return run.returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", type=Path, default=HERE / "skills")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--publication-only", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.publication_only:
        asyncio.run(check_publication(args.skills))
        print(
            "Both protocol entries, descriptions and served resources match the checkout."
        )
    else:
        try:
            sys.exit(check(args.skills, args.cache, args.offline))
        except (
            ValueError,
            RuntimeError,
            ExceptionGroup,
            subprocess.CalledProcessError,
        ) as exc:
            print(exc, file=sys.stderr)
            sys.exit(2)
