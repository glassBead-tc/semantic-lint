import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from openai import OpenAIError

from .checker import analyze
from .extraction import Provider
from .ir import Package
from .pipeline import apply_overlay, extract, read_run, report, save_run
from .sources import Manifest


def parser():
    app = argparse.ArgumentParser(prog="semanticlint")
    commands = app.add_subparsers(dest="command", required=True)
    ir = commands.add_parser("analyze-ir")
    ir.add_argument("ir", type=Path)
    check = commands.add_parser("check")
    check.add_argument("root", type=Path)
    check.add_argument("--manifest", type=Path)
    check.add_argument("--profile")
    check.add_argument(
        "--model", default=os.getenv("SEMANTICLINT_MODEL", "gpt-5.4-mini-2026-03-17")
    )
    check.add_argument("--cache", type=Path)
    check.add_argument("--offline", action="store_true")
    replay = commands.add_parser("replay")
    replay.add_argument("run", type=Path)
    for sub in (check, replay):
        sub.add_argument("--save-run", type=Path)
        sub.add_argument("--overlay", type=Path)
        sub.add_argument(
            "--fail-on", choices=["actionable-conflict", "reviewed-conflict"]
        )
    explain = commands.add_parser("explain")
    explain.add_argument("run", type=Path)
    explain.add_argument("finding")
    for cmd in (ir, check, replay, explain):
        cmd.add_argument("--format", choices=("text", "json"), default="text")
        cmd.add_argument(
            "--max-contexts", type=int, default=4096 if cmd is ir else None
        )
        cmd.add_argument("--timeout-ms", type=int, default=5000 if cmd is ir else None)
    return app


def show(result, fmt):
    if fmt == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    profile = result["profile"]
    print(f"{profile['name']} · {profile['receiver']} · {profile['window']}")
    print(
        f"{result['status']}: {len(result['findings'])} conflict(s), {result['ground_rules']} grounded rules, {result['checked_contexts']} component contexts"
    )
    if result["ground_rules"] == 0:
        print("No analyzable hard rules.")
    for f in result["findings"]:
        if "id" in f:
            print(f["id"], "actionable" if f["actionable"] else "conditional")
        print("Witness:", ", ".join(f"{k}={v}" for k, v in f["context"].items()))
        for row in f["core"]:
            spans = row.get("spans", [row["source"]])
            for s in spans:
                print(f"  {s['path']}:{s['line']}  {s['quote']}")
    for h in result.get("suspected", []):
        print("Suspected:", h["assumption"], "—", h["clarification"])
    for u in result.get("unresolved", []):
        s = u["span"]
        print(
            f"Unresolved {s['path']}:{s['line']} — {u['disposition']['reason']} / {u['verification']['reason']}"
        )
    for link in result.get("references", []):
        if link["status"] in ("missing-anchor", "unavailable"):
            print(f"Reference {link['status']}: {link['source']} → {link['href']}")
    for u in result["unfinished"]:
        print("Incomplete:", u)
    if "coverage" in result:
        print("Coverage:", result["coverage"])


def main():
    args = parser().parse_args()
    try:
        if args.command == "analyze-ir":
            result = analyze(
                Package.model_validate_json(args.ir.read_bytes()),
                args.max_contexts,
                args.timeout_ms,
            )
        else:
            if args.command == "check":
                manifest = (
                    Manifest.model_validate(yaml.safe_load(args.manifest.read_text()))
                    if args.manifest
                    else Manifest()
                )
                if args.profile and args.profile != manifest.name:
                    raise ValueError("profile name does not match manifest")
                run = extract(
                    args.root, manifest, Provider(args.model, args.cache, args.offline)
                )
            else:
                run = read_run(args.run)
            previous = run.get("search", {"limit": 4096, "timeout": 5000})
            limit = (
                args.max_contexts
                if args.max_contexts is not None
                else previous["limit"]
            )
            timeout = (
                args.timeout_ms if args.timeout_ms is not None else previous["timeout"]
            )
            run["search"] = dict(limit=limit, timeout=timeout)
            if getattr(args, "overlay", None):
                run = apply_overlay(run, json.loads(args.overlay.read_text()))
            result = report(run, limit, timeout)
            if args.command == "explain":
                result["findings"] = [
                    f for f in result["findings"] if f["id"] == args.finding
                ]
                if not result["findings"]:
                    raise ValueError("unknown finding ID")
            if getattr(args, "save_run", None):
                save_run(args.save_run, run)
        show(result, args.format)
        if result["status"] == "incomplete":
            return 2
        policy = getattr(args, "fail_on", None)
        if policy and any(
            f["actionable"] and (policy == "actionable-conflict" or f["reviewed"])
            for f in result["findings"]
        ):
            return 1
        return 0
    except OpenAIError as exc:
        message = f"OpenAI {type(exc).__name__} (status={getattr(exc,'status_code',None)}, request={getattr(exc,'request_id',None)})"
        print(
            (
                json.dumps({"status": "invalid", "error": message})
                if args.format == "json"
                else message
            ),
            file=sys.stderr,
        )
        return 2
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as error:
        print(
            (
                json.dumps({"status": "invalid", "error": str(error)})
                if args.format == "json"
                else str(error)
            ),
            file=sys.stderr,
        )
        return 2
