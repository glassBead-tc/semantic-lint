# Retry server CI demonstration

This MCP stdio server publishes the recovery and payments skills in this directory. The declared host profile allows one agent to see both bodies. Its optional `complements` entry gives the solver the signed predicate alias described by the glossary, which defines the relationship between idempotent and non-idempotent operations.

From the repository root:

```sh
uv sync --locked --group demo
uv run --no-sync python examples/retry-server/check.py --publication-only --cache .semanticlint/cache
uv run --no-sync python examples/retry-server/check.py --cache .semanticlint/cache
uv run --no-sync python examples/retry-server/acceptance.py --cache .semanticlint/cache
```

The second and third commands use OpenAI on a cache miss. Configure `OPENAI_API_KEY` in the runner environment. The GitHub workflow reads the repository Actions secret of the same name. It runs the publication test on fork PRs and runs fresh inference on trusted same-repository events. The disposable runner cache is shared across steps in that job.

The consumer enumerates both entries over MCP, fetches each manifest and reads the resources. It compares the returned descriptions, bytes, sizes and digests with the working tree and then runs the installed linter. The SDK's extension interface supplies Skills discovery methods alongside its standard Resources implementation.

`check.py` returns 0 when its rules are accounted for and verified without a conflict, 1 for an actionable contradiction, and 2 for a failed or inconclusive semantic check. The acceptance script temporarily removes the word “idempotent” from the recovery duty, expects a conflict citing both skills, restores the source, and checks for a clean result. All three revisions must agree with their offline replays.
