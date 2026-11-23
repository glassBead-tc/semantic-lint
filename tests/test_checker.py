import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from semanticlint.checker import analyze
from semanticlint.ir import Package
from oracle import active_rules, compliant, possible_contexts

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name / "ir.json").read_text())


class FormalCheckerTests(unittest.TestCase):
    def check_oracle(self, ir, expected):
        report = analyze(Package.model_validate(ir))
        impossible = [
            c for c in possible_contexts(ir) if not compliant(ir, active_rules(ir, c))
        ]
        self.assertEqual(report["status"], "complete")
        self.assertEqual(bool(impossible), expected)
        self.assertEqual(bool(report["findings"]), expected)
        for finding in report["findings"]:
            self.assertTrue(
                any(
                    all(c[k] == v for k, v in finding["context"].items())
                    for c in impossible
                )
            )
            context = next(
                c
                for c in impossible
                if all(c[k] == v for k, v in finding["context"].items())
            )
            active = active_rules(ir, context)
            core = [
                next(
                    (r, b)
                    for r, b in active
                    if r["id"] == row["rule"] and b == row["bindings"]
                )
                for row in finding["core"]
            ]
            self.assertFalse(compliant(ir, core))
            for index in range(len(core)):
                self.assertTrue(compliant(ir, core[:index] + core[index + 1 :]))
        return report

    def test_handwritten_cases_and_cli(self):
        for name, size in [
            ("retry", 2),
            ("retry-exception", 0),
            ("verification", 3),
            ("verification-alternative", 0),
        ]:
            with self.subTest(name=name):
                ir = fixture(name)
                report = self.check_oracle(ir, bool(size))
                self.assertEqual(
                    [len(f["core"]) for f in report["findings"]], [size] if size else []
                )
                path = FIXTURES / name / "ir.json"
                result = subprocess.run(
                    [
                        Path(sys.executable).parent / "semanticlint",
                        "analyze-ir",
                        path,
                        "--format",
                        "json",
                    ],
                    capture_output=True,
                    check=True,
                )
                self.assertEqual(
                    json.loads(result.stdout)["findings"], report["findings"]
                )
                for r in ir["rules"]:
                    src = r["source"]
                    self.assertEqual(
                        (path.parent / src["path"])
                        .read_text()
                        .splitlines()[src["line"] - 1],
                        src["quote"],
                    )

    def test_global_profile_constraint_rules_out_triple(self):
        ir = fixture("verification")
        ir["profile"]["constraints"] = [
            {"not": {"all": [{"visible": u} for u in ir["profile"]["units"]]}}
        ]
        self.check_oracle(ir, False)

    def test_remove_any_link_or_progress_requirement(self):
        for i in range(3):
            ir = fixture("verification")
            del ir["rules"][i]
            self.check_oracle(ir, False)

    def test_priority_and_optional_override(self):
        ir = fixture("retry")
        ir["rules"][1]["overrides"] = [ir["rules"][0]["id"]]
        self.check_oracle(ir, False)
        override = copy.deepcopy(ir["rules"][1])
        override.update(id="waiver", unit="waiver")
        ir["rules"][1].pop("overrides")
        ir["rules"].append(override)
        ir["profile"]["units"].append("waiver")
        report = self.check_oracle(ir, True)
        self.assertFalse(report["findings"][0]["context"]["visible:waiver"])
        ir["profile"]["constraints"] = [{"visible": "waiver"}]
        self.check_oracle(ir, False)

    def test_arguments_and_independent_components(self):
        ir = fixture("retry")
        ir["domains"]["operation"].append("op2")
        report = self.check_oracle(ir, True)
        self.assertEqual(
            {f["core"][0]["bindings"]["x"] for f in report["findings"]}, {"op1", "op2"}
        )
        for rule, obj in zip(ir["rules"], ["op1", "op2"]):
            next(iter(rule["effect"].values()))["args"][1] = obj
        self.check_oracle(ir, False)

    def test_unrelated_units_do_not_consume_context_budget(self):
        ir = fixture("retry")
        ir["profile"]["units"].extend(f"extra{i}" for i in range(100))
        report = analyze(Package.model_validate(ir), limit=16)
        self.assertEqual(
            (report["status"], report["checked_contexts"], len(report["findings"])),
            ("complete", 16, 1),
        )

    def test_failed_and_partial_checks(self):
        ir = fixture("retry")
        ir["profile"]["constraints"] = [False]
        with self.assertRaisesRegex(ValueError, "no admissible"):
            analyze(Package.model_validate(ir))
        path = FIXTURES / "retry" / "ir.json"
        cmd = [
            Path(sys.executable).parent / "semanticlint",
            "analyze-ir",
            path,
            "--format",
            "json",
            "--max-contexts",
            "1",
        ]
        result = subprocess.run(cmd, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "incomplete")

    def test_invalid_ir(self):
        for case in (
            "arity",
            "sort",
            "duplicate",
            "cycle",
            "predicate",
            "unit",
            "version",
        ):
            with self.subTest(case=case), self.assertRaises(ValueError):
                ir = fixture("retry")
                r, s = ir["rules"]
                if case == "arity":
                    r["effect"]["must"]["args"].pop()
                if case == "sort":
                    r["effect"]["must"]["args"][0] = "$x"
                if case == "duplicate":
                    s["id"] = r["id"]
                if case == "cycle":
                    r["overrides"], s["overrides"] = [s["id"]], [r["id"]]
                if case == "predicate":
                    r["guard"]["predicate"] = "missing"
                if case == "unit":
                    r["unit"] = "missing"
                if case == "version":
                    ir["version"] = "0.2"
                Package.model_validate(ir)
