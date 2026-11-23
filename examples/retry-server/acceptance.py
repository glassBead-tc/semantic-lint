"""Exercise the baseline, a one-word regression, and its repair on actual served files."""

from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import argparse

from check import HERE, check


def acceptance(cache, offline=False):
    with TemporaryDirectory() as temp:
        skills = copytree(HERE / "skills", Path(temp) / "skills")
        recovery = skills / "recovery" / "SKILL.md"
        original = recovery.read_bytes()
        clause = b"Retry every failed idempotent operation."
        if original.count(clause) != 1:
            raise ValueError(
                "acceptance fixture no longer contains the baseline instruction"
            )
        changed = original.replace(clause, b"Retry every failed operation.")
        for label, data, expected in [
            ("baseline", original, 0),
            ("regression", changed, 1),
            ("repair", original, 0),
        ]:
            recovery.write_bytes(data)
            print(label, flush=True)
            code = check(skills, cache, offline)
            if code != expected:
                raise ValueError(f"{label}: expected exit {expected}, got {code}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    opts = parser.parse_args()
    acceptance(opts.cache, opts.offline)
