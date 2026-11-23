# SemanticLint

SemanticLint extracts behavioral rules from Markdown skills and checks whether an agent can comply with them in a finite decision context. A finding includes a possible disclosure and task context, the responsible source passages, and an inclusion-minimal set of incompatible grounded rules.

## Run the checker

Use Python 3.11 or newer and uv. From the repository root run:

```sh
uv sync --locked
uv run semanticlint analyze-ir tests/fixtures/retry/ir.json
uv run semanticlint analyze-ir tests/fixtures/verification/ir.json --format json
uv run python -m unittest discover -s tests -v
```

The retry fixture requires retrying a failed operation and forbids retrying a non-idempotent operation. The witness has both properties on `op1`, with both skills visible to the same agent. Try `tests/fixtures/retry-exception/ir.json` to see the repaired rule, or `tests/fixtures/verification-alternative/ir.json` to see how an alternate reviewer resolves a three-rule conflict.

## Input contract

A version `0.1` package declares finite typed domains, predicate and action signatures, a single receiving agent and decision window, admissibility constraints, disclosure units, and source-attributed rules. Use separate packages to model separate receivers or decision windows. Visibility and task predicates range over both Boolean values subject to the profile constraints. Variable arguments start with `$` and are universally instantiated over their declared domains.

Guards accept Boolean literals or one of `predicate`, `visible`, `all`, `any`, and `not`. Rule effects are `must`, `forbid`, and `requires` with a nonempty `anyOf` array. The latter is conditional on performing its antecedent action. Exceptions deactivate a rule. An active, undefeated rule can override an explicitly named opposing rule on the same grounded action. The fixtures contain complete examples. The Pydantic contract is available as `semanticlint.ir.Package.model_json_schema()`.

The checker checks connected action components, explores distinct locally relevant contexts that satisfy the full profile, and reports one sufficient minimal core per conflicting component context, deduplicated by rule and bindings. Each emitted core is independently re-solved after removing the tracking labels. The test oracle enumerates context and action assignments directly from the JSON using the Python standard library.

The CLI returns 0 for a completed analysis, including a completed analysis with findings. Invalid inputs and incomplete searches return 2. Use `--max-contexts` and `--timeout-ms` to set the per-component assignment cap and per-query solver timeout. Partial results retain proven findings. In JSON mode invalid input is reported on stderr and analysis reports appear on stdout.

## Check Markdown skills

Set `OPENAI_API_KEY` in the process environment for fresh extraction. Optionally set `SEMANTICLINT_MODEL` or pass `--model` to select a model with Structured Outputs support. The default is `gpt-5.4-mini-2026-03-17`. No glossary is required. With no manifest, catalog descriptions are assumed visible and bodies independently eligible. Host-dependent findings are conditional.

```sh
semanticlint check examples/retry-server/skills --manifest examples/retry-server/profile.yml --cache .semanticlint/cache --save-run .semanticlint/baseline
semanticlint replay .semanticlint/baseline --format json
```

The parser records source bytes, digests, heading context and local references. It fixes unambiguous explicit modalities before the provider proposes typed bindings for each document. A separate verifier checks the reconstruction against the whole bundle. The JSON report identifies unresolved interpretations and source-level qualifications, and includes the token usage returned by the provider. Uncertain interpretations can produce conditional findings. Use `--fail-on actionable-conflict` for a declared-host policy, or `--fail-on reviewed-conflict` for human-reviewed findings. Both policies return 1 when triggered.

There is one extraction request per changed document and reference closure, then a bundle-level registry reconciliation for multi-rule inputs and one independent verification request. `--cache DIR` reuses identical requests. `--offline` on `check` requires complete matching cache entries. `replay` runs locally from the snapshot with no key. Source and model responses are stored in the cache and saved run. Use a local directory suitable for the sensitivity of the inputs.

### Inspect or correct a finding

Use `semanticlint explain RUN SL-001` with an ID from the report. The saved `run.json` contains the exact source text, IR, mapping evidence, dispositions and verifier judgments. An overlay is a JSON object with `base` equal to the report's `run_id` and an `edits` array. Each edit names the source span, records a reason, and supplies a replacement interpretation for that span. To mark one mapping unresolved, use:

```json
{
  "base": "<run_id>",
  "edits": [{
    "source": "<source span id>",
    "reason": "The reviewer may be a person or a subagent.",
    "replacement": {
      "fixed_bindings": [],
      "inferred_rules": [],
      "dispositions": [{"source":"<source span id>","kind":"unresolved","reason":"Reviewer identity is unspecified."}]
    }
  }]
}
```

To correct a binding, copy the source's record from `run.interpretations` into `fixed_bindings` for a parser-fixed modality or `inferred_rules` otherwise. Remove `origin` and, for fixed bindings, `modality`. Edit the term and its supporting evidence. Include one matching disposition. Run `semanticlint replay RUN --overlay correction.json --save-run NEW_RUN` to preserve the correction and replay it later. Source or prompt changes invalidate the old review anchor.

See the [MCP server and CI demonstration](examples/retry-server/README.md) and [implementation and evaluation specification](docs/SPEC.md).
