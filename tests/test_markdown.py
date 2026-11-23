"""Exercise the production CLI with source-keyed synthetic provider recordings."""

from copy import deepcopy
import json
from pathlib import Path
from shutil import copytree
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

import yaml

from semanticlint.extraction import Provider, atomic_json, fingerprint
from semanticlint.pipeline import extract, read_run, report, review_base
from semanticlint.sources import Manifest, modality, scan

DEMO = Path(__file__).resolve().parents[1] / "examples" / "retry-server"
PROFILE = Manifest.model_validate(yaml.safe_load((DEMO / "profile.yml").read_text()))


def scripted_response(shape, data):
    if "judgments" in data:
        slots = {
            r["source"]: {k: v for k, v in r.items() if k != "source"}
            for r in data["judgments"]
        }
        slots["suspected"] = data["suspected"]
    else:
        slots = {
            r["source"]: {k: v for k, v in r.items() if k != "source"}
            for r in data["dispositions"]
        }
        for sid, values in slots.items():
            kind = values.pop("kind")
            values["role"] = (
                "instruction" if kind in ("modeled", "partial", "unresolved") else kind
            )
            values["unmodeled_clauses"] = []
            if "rules" in shape.model_fields[sid].annotation.model_fields:
                values["rules"] = [
                    {k: v for k, v in rule.items() if k != "source"}
                    for rule in data["fixed_bindings"] + data["inferred_rules"]
                    if rule["source"] == sid
                ]
                values["rules"] = deepcopy(values["rules"])
                for rule in values["rules"]:
                    for action in [rule["action"], *rule["alternatives"]]:
                        actor = action["args"].pop(0)
                        action["performer"] = (
                            None if actor["value"] == "agent" else actor["value"]
                        )
    return shape.model_validate(slots)


class RecordedResponses:
    """Synthetic messages for the two documented retry passages only."""

    def __init__(self):
        self.calls = []

    def parse(self, *, input, text_format, model, **kwargs):
        payload = json.loads(input)
        self.calls.append(payload)
        if text_format.__name__ == "Normalization":
            return SimpleNamespace(
                status="completed",
                model=model,
                id="synthetic",
                usage=None,
                output_parsed=text_format(changes=[]),
            )
        if "targets" in payload:
            fixed, inferred, dispositions = [], [], []
            for span in payload["targets"]:
                sid = span["id"]
                if span["kind"] == "description":
                    dispositions.append(
                        dict(source=sid, kind="explanation", reason="catalog purpose")
                    )
                    continue
                phrase = span["text"].lower()

                def bind(symbol, wording, args):
                    return dict(
                        symbol=symbol, wording=wording, args=args, evidence=[sid]
                    )

                obj = dict(sort="operation", value="$x")
                retry = bind("retry", "retry", [dict(sort="actor", value="agent"), obj])
                facts = []
                if "failed" in phrase:
                    facts.append(
                        dict(fact=bind("failed", "failed", [obj]), negated=False)
                    )
                if "idempotent" in phrase:
                    negative = "non-idempotent" in phrase
                    facts.append(
                        dict(
                            fact=bind(
                                "idempotent",
                                "non-idempotent" if negative else "idempotent",
                                [obj],
                            ),
                            negated=negative,
                        )
                    )
                fields = dict(
                    source=sid,
                    action=retry,
                    alternatives=[],
                    when=[dict(facts=facts, evidence=[sid])],
                    exceptions=[],
                    supporting=[],
                    overrides=[],
                )
                if span["fixed"]:
                    fixed.append(fields)
                else:
                    inferred.append(
                        dict(
                            **fields, modality="forbid" if "never" in phrase else "must"
                        )
                    )
                dispositions.append(
                    dict(source=sid, kind="modeled", reason="retry directive")
                )
            out = dict(
                fixed_bindings=fixed, inferred_rules=inferred, dispositions=dispositions
            )
        else:
            out = dict(
                suspected=[],
                judgments=[
                    dict(
                        source=s["id"],
                        verdict="agree",
                        reason="fixture interpretation agrees",
                    )
                    for s in payload["spans"]
                ],
            )
        return SimpleNamespace(
            status="completed",
            model=model,
            id="synthetic",
            usage=None,
            output_parsed=scripted_response(text_format, out),
        )


def command(*args):
    return subprocess.run(
        [str(Path(sys.executable).parent / "semanticlint"), *map(str, args)],
        capture_output=True,
        text=True,
    )


class MarkdownTests(unittest.TestCase):
    def test_modality(self):
        for text, expected in [
            ("The agent must publish the draft.", "must"),
            ("The agent must not publish the draft.", "forbid"),
            ("The policy prohibits publishing drafts.", "forbid"),
            ("This policy forbids releasing drafts.", "forbid"),
            ("The policy requires the agent to review X.", "must"),
            ("Publishing X requires reviewing X.", "requires"),
            ("The agent must have failed.", None),
            ("You are not required to retry.", None),
            ("Only reviewed drafts leave staging.", None),
            ("The example says “you must retry.”", None),
        ]:
            self.assertEqual(modality(text), expected, text)

    def test_scan_spans_links_cycles_and_examples(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "SKILL.md"
            skill.write_bytes(
                "---\r\nname: test\r\ndescription: >\r\n  Instructions for café.\r\n---\r\n# Rules\r\nSee [rule](ref.md#rule) and [missing](no.md).\r\n\r\n~~~\r\nYou must publish X.\r\n~~~\r\n".encode()
            )
            (root / "ref.md").write_text(
                "# Rule\n\nThe agent must publish X.\n\n[back](SKILL.md#oops)\n"
            )
            source = scan(root, Manifest())
            self.assertEqual(len(source["documents"]), 2)
            self.assertEqual(
                {link["status"] for link in source["links"]},
                {"resolved", "unavailable", "missing-anchor"},
            )
            for span in source["spans"]:
                raw = (root / span["path"]).read_bytes()
                self.assertEqual(
                    raw[span["start"] : span["end"]].decode(), span["quote"]
                )
            code = next(s for s in source["spans"] if s["example"])
            self.assertIsNone(code["fixed"])
            self.assertTrue(any(s["fixed"] == "must" for s in source["spans"]))

    def test_real_cli_edit_cache_explain_correction_and_replay(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = copytree(DEMO / "skills", base / "skills")
            cache = base / "cache"
            responses = RecordedResponses()
            provider = Provider(
                "fixture-model", cache, client=SimpleNamespace(responses=responses)
            )
            extract(root, PROFILE, provider)
            self.assertEqual(len(responses.calls), 4)

            def check(saved, *flags):
                return command(
                    "check",
                    root,
                    "--manifest",
                    DEMO / "profile.yml",
                    "--cache",
                    cache,
                    "--offline",
                    "--model",
                    "fixture-model",
                    "--format",
                    "json",
                    "--fail-on",
                    "actionable-conflict",
                    "--save-run",
                    saved,
                    *flags,
                )

            initial = check(base / "green")
            self.assertEqual(initial.returncode, 0, initial.stderr)
            path = root / "recovery" / "SKILL.md"
            path.write_text(
                path.read_text().replace(
                    "Retry every failed idempotent", "The agent must retry every failed"
                )
            )
            self.assertEqual(check(base / "miss").returncode, 2)
            extract(
                root,
                PROFILE,
                Provider(
                    "fixture-model", cache, client=SimpleNamespace(responses=responses)
                ),
            )
            self.assertEqual(len(responses.calls), 7)
            broken = check(base / "red")
            self.assertEqual(broken.returncode, 1, broken.stderr)
            result = json.loads(broken.stdout)
            self.assertEqual(len(result["findings"]), 1)
            found = result["findings"][0]
            self.assertTrue(found["context"]["failed(op1)"])
            self.assertFalse(found["context"]["idempotent(op1)"])
            self.assertEqual(
                command("explain", base / "red", found["id"]).returncode, 0
            )
            replay = command("replay", base / "red", "--format", "json")
            self.assertEqual(result, json.loads(replay.stdout))
            run = read_run(base / "red")
            recovery = next(
                r for r in run["interpretations"] if r["modality"] == "must"
            )
            edit = deepcopy(recovery)
            del edit["origin"], edit["modality"]
            edit["action"]["symbol"] = "retry_local_job"
            sid = edit["source"]
            overlay = dict(
                base=review_base(run),
                edits=[
                    dict(
                        source=sid,
                        reason="reviewer supplied a scoped action mapping",
                        replacement=dict(
                            fixed_bindings=[edit],
                            inferred_rules=[],
                            dispositions=[run["dispositions"][sid]],
                        ),
                    )
                ],
            )
            atomic_json(base / "overlay.json", overlay)
            corrected = command(
                "replay",
                base / "red",
                "--overlay",
                base / "overlay.json",
                "--format",
                "json",
                "--save-run",
                base / "reviewed",
            )
            self.assertEqual(corrected.returncode, 0, corrected.stderr)
            self.assertEqual(json.loads(corrected.stdout)["findings"], [])
            self.assertEqual(
                json.loads(corrected.stdout),
                json.loads(
                    command("replay", base / "reviewed", "--format", "json").stdout
                ),
            )
            self.assertEqual(
                command(
                    "replay", base / "green", "--overlay", base / "overlay.json"
                ).returncode,
                2,
            )

    def test_disputed_interpretation_makes_core_conditional(self):
        with TemporaryDirectory() as tmp:
            skills = copytree(DEMO / "skills", Path(tmp) / "skills")
            path = skills / "recovery" / "SKILL.md"
            path.write_text(path.read_text().replace("failed idempotent", "failed"))
            run = extract(
                skills,
                PROFILE,
                Provider(
                    "fixture", client=SimpleNamespace(responses=RecordedResponses())
                ),
            )
            sid = run["interpretations"][0]["source"]
            run["judgments"][sid].update(
                verdict="abstain", reason="alias could refer to a different action"
            )
            result = report(run)
            self.assertEqual(result["coverage"]["unresolved"], 1)
            self.assertFalse(result["findings"][0]["actionable"])

    def test_manifest_disjoint_bodies(self):
        with TemporaryDirectory() as tmp:
            root = copytree(DEMO / "skills", Path(tmp) / "skills")
            recovery = root / "recovery" / "SKILL.md"
            recovery.write_text(
                recovery.read_text().replace("failed idempotent", "failed")
            )
            profile = Manifest.model_validate(
                {
                    **PROFILE.model_dump(),
                    "constraints": [
                        {
                            "not": {
                                "all": [
                                    {"visible": "body:recovery/SKILL.md"},
                                    {"visible": "body:payments/SKILL.md"},
                                ]
                            }
                        }
                    ],
                }
            )
            provider = Provider(
                "fixture", client=SimpleNamespace(responses=RecordedResponses())
            )
            no_conflict = report(extract(root, profile, provider))
            self.assertEqual(no_conflict["findings"], [])

    def test_full_server_demo_with_synthetic_responses(self):
        try:
            import mcp
        except ImportError:
            self.skipTest("install demo dependency group to exercise server")
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            copy = copytree(DEMO / "skills", base / "skills")
            cache = base / "cache"
            provider = Provider(
                "fixture-model",
                cache,
                client=SimpleNamespace(responses=RecordedResponses()),
            )
            extract(copy, PROFILE, provider)
            path = copy / "recovery" / "SKILL.md"
            path.write_bytes(path.read_bytes().replace(b"failed idempotent", b"failed"))
            extract(copy, PROFILE, provider)
            import os

            result = subprocess.run(
                [
                    sys.executable,
                    str(DEMO / "acceptance.py"),
                    "--offline",
                    "--cache",
                    str(cache),
                ],
                env={**os.environ, "SEMANTICLINT_MODEL": "fixture-model"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("offline replay matched"), 3)

    def test_source_bound_project_terms_and_ambiguous_alias(self):
        """The scripted provider exercises all three interpretations of the same two verbs."""

        class Terminology:
            model = "synthetic-terminology"
            receipts = []

            def request(self, stage, payload, schema):
                if stage == "normalize":
                    return schema(changes=[])
                if stage == "verify":
                    verbs = [s for s in payload["spans"] if s["fixed"]]
                    ambiguous = not any(
                        s["kind"] == "body" and "means" in s["text"]
                        for s in payload["spans"]
                    )
                    return scripted_response(
                        schema,
                        dict(
                            judgments=[
                                dict(
                                    source=s["id"],
                                    verdict="agree",
                                    reason="literal meaning",
                                )
                                for s in payload["spans"]
                            ],
                            suspected=(
                                [
                                    dict(
                                        sources=[s["id"] for s in verbs],
                                        assumption="Release may mean publish.",
                                        clarification="Do these actions have the same target?",
                                    )
                                ]
                                if ambiguous
                                else []
                            ),
                        ),
                    )
                fixed = []
                dispositions = []
                all_spans = payload["targets"] + payload["reference_material"]
                definition = next((s for s in all_spans if "means" in s["text"]), None)
                for s in payload["targets"]:
                    sid = s["id"]
                    if s["fixed"]:
                        verb = "release" if "release" in s["text"] else "publish"
                        merge = (
                            verb == "release"
                            and definition
                            and "means publish" in definition["text"]
                        )
                        fixed.append(
                            dict(
                                source=sid,
                                action=dict(
                                    symbol="publish" if merge else verb,
                                    wording=verb,
                                    args=[
                                        dict(sort="actor", value="agent"),
                                        dict(sort="draft", value="$x"),
                                    ],
                                    evidence=[sid]
                                    + ([definition["id"]] if merge else []),
                                ),
                                alternatives=[],
                                when=[],
                                exceptions=[],
                                supporting=[],
                                overrides=[],
                            )
                        )
                    dispositions.append(
                        dict(
                            source=sid,
                            kind=(
                                "modeled"
                                if s["fixed"]
                                else (
                                    "definition"
                                    if "means" in s["text"]
                                    else "explanation"
                                )
                            ),
                            reason="source classification",
                        )
                    )
                return scripted_response(
                    schema,
                    dict(
                        fixed_bindings=fixed,
                        inferred_rules=[],
                        dispositions=dispositions,
                    ),
                )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder, body in [
                ("writer", "The agent must publish the draft."),
                ("owner", "The agent must not release the draft."),
            ]:
                path = root / folder / "SKILL.md"
                path.parent.mkdir()
                path.write_text(
                    f"---\nname: {folder}\ndescription: Project instructions.\n---\n{body}\n"
                )
            profile = Manifest(evidence="declared")
            path = root / "writer" / "SKILL.md"
            original = path.read_text()
            for definition, confirmed, ambiguous in [
                ("Release means publish.", True, False),
                ("Release means deploy to production.", False, False),
                ("", False, True),
            ]:
                path.write_text(original + "\n" + definition)
                result = report(extract(root, profile, Terminology()))
                self.assertEqual(
                    any(f["actionable"] for f in result["findings"]), confirmed
                )
                self.assertEqual(bool(result["suspected"]), ambiguous)
                if confirmed:
                    self.assertTrue(
                        any(
                            s["quote"] == definition
                            for r in result["findings"][0]["core"]
                            for s in r["spans"]
                        )
                    )

    def test_attested_complements_are_lowered_before_verification(self):
        from semanticlint.pipeline import normalize, compile_rules
        from semanticlint.checker import analyze

        original = extract(
            DEMO / "skills",
            PROFILE,
            Provider("fixture", client=SimpleNamespace(responses=RecordedResponses())),
        )
        rules = deepcopy(original["interpretations"])
        payment = next(r for r in rules if r["modality"] == "forbid")
        literal = payment["when"][0]["facts"][0]
        literal["fact"]["symbol"] = "non_idempotent"
        literal["negated"] = False
        declared, _, _ = compile_rules(original["source"], PROFILE, rules)
        self.assertEqual(analyze(declared)["findings"], [])
        implicit = PROFILE.model_copy(update={"complements": {}})
        broken, registry, _ = compile_rules(original["source"], implicit, rules)
        self.assertEqual(len(analyze(broken)["findings"]), 1)

        class Normalizer:
            def request(self, stage, payload, schema):
                row = next(r for r in payload["rules"] if r["modality"] == "forbid")
                sid = row["source"]
                return schema(
                    changes=[
                        dict(
                            source=sid,
                            kind="predicates",
                            original="non_idempotent",
                            canonical="idempotent",
                            invert=True,
                            evidence=[sid],
                            reason="The profile defines logical complements.",
                        )
                    ]
                )

        spans = {s["id"]: s for s in original["source"]["spans"]}
        corrected, changes = normalize(Normalizer(), implicit, spans, rules, registry)
        self.assertEqual(len(changes), 1)
        self.assertEqual(
            analyze(compile_rules(original["source"], PROFILE, corrected)[0])[
                "findings"
            ],
            [],
        )
        self.assertFalse(literal["negated"])

    def test_rejected_normalization_is_reverted_and_reverified(self):
        class BadMerge:
            model = "fixture"
            receipts = []
            attempts = 0

            def request(self, stage, payload, schema):
                if stage == "normalize":
                    payment = next(
                        r for r in payload["rules"] if r["modality"] == "forbid"
                    )
                    sid = payment["source"]
                    return schema(
                        changes=[
                            dict(
                                source=sid,
                                kind="predicates",
                                original="idempotent",
                                canonical="failed",
                                invert=True,
                                evidence=[sid],
                                reason="Deliberately invalid relationship in a test.",
                            )
                        ]
                    )
                result = (
                    RecordedResponses()
                    .parse(
                        input=json.dumps(payload), model=self.model, text_format=schema
                    )
                    .output_parsed
                )
                if stage == "verify":
                    self.attempts += 1
                    if payload["reconciliations"]:
                        getattr(
                            result, payload["reconciliations"][0]["source"]
                        ).verdict = "disagree"
                return result

        provider = BadMerge()
        run = extract(DEMO / "skills", PROFILE.model_copy(update={"complements": {}}), provider)
        self.assertEqual(provider.attempts, 2)
        self.assertEqual(run["reconciliations"], [])
        self.assertEqual(len(run["rejected_reconciliations"]), 1)
        self.assertEqual(report(run)["coverage"]["unresolved"], 0)

    def test_invalid_mapping_evidence_retains_original_for_independent_check(self):
        class InvalidMapper:
            model = "fixture"
            receipts = []

            def request(self, stage, payload, schema):
                if stage == "normalize":
                    sid = payload["rules"][0]["source"]
                    return schema(
                        changes=[
                            dict(
                                source=sid,
                                kind="predicates",
                                original="idempotent",
                                canonical="failed",
                                invert=True,
                                evidence=["untraceable definition"],
                                reason="guess",
                            )
                        ]
                    )
                return (
                    RecordedResponses()
                    .parse(
                        input=json.dumps(payload), model=self.model, text_format=schema
                    )
                    .output_parsed
                )

        with TemporaryDirectory() as tmp:
            skills = copytree(DEMO / "skills", Path(tmp) / "skills")
            path = skills / "recovery" / "SKILL.md"
            path.write_bytes(path.read_bytes().replace(b"failed idempotent", b"failed"))
            run = extract(skills, PROFILE, InvalidMapper())
            self.assertEqual(run["reconciliations"], [])
            self.assertEqual(
                run["rejected_reconciliations"][0]["assessment"]["verdict"], "invalid"
            )
            self.assertTrue(report(run)["findings"][0]["actionable"])
