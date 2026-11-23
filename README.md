# SemanticLint

SemanticLint checks whether an agent can comply with a finite set of handwritten instruction rules. A finding includes a possible disclosure and task context, the responsible source passages, and an inclusion-minimal set of incompatible grounded rules.

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

Milestone 1 trusts the handwritten interpretation and its source attribution. The accompanying Markdown lets a reader inspect the fixture passages. Automatic extraction, source-digest validation, and the MCP server's CI demonstration are planned in the [implementation and evaluation specification](docs/SPEC.md).
