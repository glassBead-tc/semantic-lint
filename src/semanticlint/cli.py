import argparse
import json
import sys
from pathlib import Path

from .checker import analyze
from .ir import Package


def main():
    parser = argparse.ArgumentParser(prog="semanticlint")
    command = parser.add_subparsers(dest="command", required=True).add_parser(
        "analyze-ir"
    )
    command.add_argument("ir", type=Path)
    command.add_argument("--format", choices=("text", "json"), default="text")
    command.add_argument("--max-contexts", type=int, default=4096)
    command.add_argument("--timeout-ms", type=int, default=5000)
    args = parser.parse_args()
    try:
        package = Package.model_validate_json(args.ir.read_bytes())
        report = analyze(package, args.max_contexts, args.timeout_ms)
    except (OSError, ValueError) as exc:
        print(
            (
                json.dumps({"status": "invalid", "error": str(exc)})
                if args.format == "json"
                else str(exc)
            ),
            file=sys.stderr,
        )
        return 2
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"{package.profile.name} · {package.profile.receiver} · {package.profile.window}"
        )
        print(
            f"{report['status']}: {len(report['findings'])} conflict(s), "
            f"{report['ground_rules']} grounded rules, {report['checked_contexts']} component contexts"
        )
        for finding in report["findings"]:
            print(
                "Witness:", ", ".join(f"{k}={v}" for k, v in finding["context"].items())
            )
            for row in finding["core"]:
                span = row["source"]
                print(
                    f"  {span['path']}:{span['line']}  {span['quote']}  {row['bindings']}"
                )
        for unfinished in report["unfinished"]:
            print("Incomplete:", unfinished)
    return 2 if report["status"] == "incomplete" else 0
