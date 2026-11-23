# SemanticLint

## Implementation and evaluation specification

SemanticLint helps a maintainer decide whether a change to an agent skill is safe to serve alongside other skills. Its CLI takes Markdown and a model of what the host can disclose. It returns source-linked contradictions, the circumstances that expose them, and an account of what it could analyze.

Two choices govern the first implementation. **Possible joint disclosure** makes passages eligible for cross-skill analysis whenever a host could expose them to the same agent during a shared decision. **A blinded comparison with direct LLM review** determines whether to invest further. The pipeline must find genuine conflicts the direct review misses with few false alarms. The numerical targets in §7 are proposals for preregistration.

### 1. What constitutes a finding

Suppose one skill says “Retry every failed operation” and another says “Never retry a non-idempotent operation.” On a failed, non-idempotent operation governed by both skills, retrying violates the second rule and declining to retry violates the first. The diagnostic should identify **that operation**, both passages, and the profile in which both apply. If the first author writes “Retry every failed *idempotent* operation,” that diagnostic should disappear on the next check.

The unit of analysis is a **decision window** in a particular profile, with named actors and finite, typed objects. A profile describes which catalog entries, instruction bodies and references a host can expose together, and the conditions that may apply to the task. Sequential loads count if the earlier instructions still govern the decision. An action is a Boolean choice about whether a particular actor performs it on particular objects in that window. A finding needs an admissible disclosure and task context in which every assignment of the modeled actions violates an effective hard rule.

| Input situation | Required behavior |
| --- | --- |
| One skill contains both retry rules | Report the internal conflict when its passages can govern a decision together. |
| A host could expose both instruction bodies, even though nobody has observed that combination | Check it on the ordinary CLI path. Report a cross-skill conflict with a possible disclosure state. |
| A catalog description itself says “Never retry” | Check that directive from the point at which the description is visible, including against any body that could be visible with it. |
| A catalog entry names a skill whose body the host could never expose in this window | Keep rules found only in that body inactive. Descriptive metadata contributes no duty. |
| The only basis for possible joint disclosure is a generic assumption about the host | Report a candidate under that assumption. Identify the host fact that would confirm or dismiss it. |
| A profile makes the roles, object identities, or activation conditions disjoint | Preserve the distinction. Report a conflict only if there is a witness satisfying the profile. |
| A phrase admits multiple plausible meanings, one of which would cause a conflict | Show a suspected finding with the interpretations that change the result. |

The Agent Skills format distinguishes discovery metadata, activated instruction bodies, and resources loaded on demand. A directory listing establishes availability. The composition input supplies the evidence for joint applicability. [Agent Skills specification](https://agentskills.io/specification) · [Client implementation guide](https://agentskills.io/client-implementation/adding-skills-support)

### 2. Inputs and scope

- Discover `SKILL.md` under caller-supplied roots. Read UTF-8 Markdown, YAML frontmatter, headings, paragraphs, list items, tables, and fenced examples. Retain original bytes for source mapping. Two skills with the same frontmatter name in different directories retain distinct path-based identities.
- Divide each skill into disclosure units for the visible fields of its catalog entry, its Markdown body, and each relevant referenced file. Scope clauses to the text exposed by that unit. A use-trigger in a description may constrain selection when the host enforces it. An ordinary statement of purpose supplies classification evidence.
- A profile manifest names the roots, eligible units, any always-visible text such as `AGENTS.md`, the window, typed objects, and visibility constraints. Each evidence-backed constraint can express facts like “the delivery role can load both bodies,” “these modes are exclusive,” or “a reference is visible only after this body is loaded.” A fixed bundle is one special case. Host configuration, a maintainer declaration, or an observed disclosure supplies provenance. Logs establish positive examples of disclosure. An exclusion requires evidence that the host rules out the combination.
- Inventory local document references with source and target. Resolve file links relative to the containing file and inspect reachable local Markdown instructions inside the supplied roots. Cache by canonical path and digest so repeated links and cycles terminate. Each reference remains a separate disclosure unit with its loading condition. Identify referenced procedures by target heading, anchor or sourced definition when available. Report missing files or anchors as structural defects and ambiguous or unexamined required procedures as unresolved semantic references. Informational links, external URLs, scripts, and assets remain inventory entries. Ingestion reads documents as data.
- With no manifest, `check ./skills` assumes a common agent can see all supplied descriptions and can receive any subset of the supplied bodies. It checks internal and cross-skill conflicts in one run and labels host-dependent findings with this default assumption. A host profile replaces the default with evidence about what is possible. Check jointly feasible subsets of any size up to the available search budget. Evaluating only pairs would miss compositional conflicts. Evaluating only the full union would miss a conflict that a sometimes-visible override happens to resolve.

The following minimal profile describes the retry example. Both bodies are independently eligible during the decision. The host owner stipulates the common catalog and visibility policy. Conditions of individual instructions, such as operation failure, still apply after disclosure.

```yaml
version: 1
name: retry-decision
window: deciding whether to retry the current operation
evidence: maintainer-declared
skills: [recovery/SKILL.md, payments/SKILL.md]
catalog: names-and-descriptions-visible
bodies: any-subset
domains:
  actor: [agent]
  operation: [op1]
```

An optional `constraints` list restricts this space with the same Boolean guard syntax used in the IR. For example, `¬(body(recovery) ∧ body(payments))` rules out that joint-body state. Profiles may also pin a visibility atom true or false. Display the disclosures in every witness, including a relevant absent unit when its presence would defeat a rule.

For a directory check with inferred domains, bind one receiving agent. For each referenced object sort, retain distinctly identified constants and add one fresh representative object. Variable names may refer to the same object. Range declared Boolean task predicates over both values subject to sourced constraints. Display this finite grounding and the provenance of the assumptions. Rules requiring several distinct anonymous instances of one sort need an explicit larger domain or receive an unresolved disposition. An absent definition or an uncertain identity stays unresolved.

The first semantic fragment covers obligations, prohibitions, Boolean context guards, exceptions, explicit rule precedence, and necessary same-window action prerequisites. Retain permissions, preferences, examples, descriptions, and unsupported clauses as classified source material. Time ordering, unbounded quantifiers, arbitrary tool effects, epistemic claims, and eventual progress require later extensions.

### 3. Compilation contract

| Stage | Output and checks |
| --- | --- |
| Parse | Produce a document manifest, reference graph, and addressable source units. Include path, source digest, section ancestry, start/end UTF-8 byte offsets, and display lines. Byte ranges are half-open. Validate a quoted span by slicing the original bytes. |
| Extract | An LLM receives source units, their heading/list context, and visible definitions. It emits schema-validated candidate rules or classifications. One sentence may yield several rules. One rule may need several spans, such as a list introduction plus an exception in a later paragraph. |
| Normalize | Resolve actor, object, predicate, and action signatures against a versioned registry. Retain the original wording and source or profile evidence for each mapping. Model-proposed synonyms with uncertain identity stay candidates. Alternative bindings may be explored in separate hypothetical runs. |
| Verify interpretation | Render candidate IR into plain language. A separate verifier pass sees the source, referenced definitions, and reconstruction and checks modality, scope, arguments, exceptions, and necessity of prerequisites. It may agree, disagree, or abstain. Agreement is recorded as machine evidence. A maintainer can inspect and correct the same record. |
| Analyze | Validate all names, argument types, variables, rule targets, and overrides. Ground over finite objects. Compute effective rules per context and call the solver. A saved IR package can be analyzed offline. |
| Report | Emit source excerpts, a concrete failing context, a sufficient set of effective rules, relevant assumptions, interpretation provenance, and coverage. A deterministic template can explain each supported proof. Suggested edits are optional and require a fresh check. |

Every source unit has a disposition. A unit with several clauses can have several dispositions. Each extracted claim must point to supporting text. Track condition-bearing headings, qualifications, and referenced definitions explicitly. If a missing qualification could change a finding, flag the dependency on that qualification. The reviewer must be able to reach the unresolved text from the finding.

**Persisted IR v0.1.** Use versioned JSON with the following records. Validate at the boundary between extraction and analysis.

| Record | Required information |
| --- | --- |
| Document and span | Stable document key, version digest, path, byte offsets, section and exact quote. Derived line and column coordinates serve display. |
| Profile | Named window, visibility atoms for each unit and receiving agent, actor and object domains, admissibility formula, fixed facts, and provenance of each assumption. |
| Registry symbol | Stable ID, kind (context predicate or action), argument sorts and order, definition, aliases, evidence and review status. Actor and object identities participate in action equality. |
| Rule | ID, owning disclosure unit, required visible supporting units, source spans, typed variables, context guard, exception guards, effect, targeted override relations, mapping references, interpretation status, and optional extractor confidence. A conditional qualification from a separate unit keeps its own visibility guard. |
| Unresolved item | Source spans, reason, candidate interpretations where available, affected symbols or ownership scope. Examples include missing definitions, unspecified time window, and ambiguous “should.” |
| Run | Source, profile, registry, schema, prompt, extractor, verifier, overlay and solver versions or digests, configured limits, result status, and coverage counts. |

**Logical fragment.** An atom is a declared predicate or action with typed arguments, including its actor when relevant. Guard syntax comprises constants, context atoms, negation, conjunction, and disjunction. Rules bind universally over the listed finite domains. Effects are `must(a)`, `forbid(a)`, or `requires(a, anyOf(b1, …, bn))` with at least one alternative. A prerequisite is necessary if the antecedent action is performed. A suggestion that delegation is a convenient verification method does not support a necessary-prerequisite rule. A bare permission or preference imposes no hard constraint in this fragment. An explicit waiver can be represented as a targeted exception.

The first retry rule has this semantic payload.

```json
{
  "id": "retry-on-failure",
  "variables": {"x": "operation"},
  "guard": {"predicate": "failed", "args": ["$x"]},
  "exceptions": [],
  "effect": {"must": {"action": "retry", "args": ["agent", "$x"]}},
  "overrides": []
}
```

The enclosing record binds this payload to a skill, spans, registry and review state. In this example the registry declares `failed(operation)` and `retry(actor, operation)`. The second rule binds the same variable sort and actor, changes the guard to `non_idempotent($x)`, and changes the effect to `forbid`. A renderer should reconstruct the all-operations quantifier and keep the guard visible.

### 4. Checking the IR

Choose one receiving agent `p` and one decision window for the entire check. Let `C` assign disclosure and task facts and `A` assign agent actions. Let `D(p,C)` express the profile’s admissible contexts and `H(p,C,A)` the conjunction of rules effective for that same recipient. The performer named by an action can differ from the instruction recipient. The checker looks for a witness to this formula.

```text
exists p, C such that D(p,C) and there is no A satisfying H(p,C,A)
```

1. Validate the profile, including nonempty domains for objects required by the scenario. If `D` is unsatisfiable, report an invalid profile. A declared variable fact ranges over both values consistent with `D`. A missing definition is unresolved. A verified property of the deployment and a hypothetical profile assumption must remain distinguishable in the report.
2. For each receiver and window, ground the rules. Form components by connecting rules that share a ground action, a prerequisite alternative, or an override dependency. For each component, collect every fact and visibility atom that can affect its rules, exceptions, definitions or overrides. Enumerate distinct assignments to those atoms in a stable order. Admit an assignment only if it extends to a model of the **full** profile formula. Save one such extension as a witness. This projection avoids enumerating disclosures of unrelated skills and preserves global constraints such as a limit on the total number of loaded bodies. On the tiny conformance corpus, compare projected results with full enumeration.
3. In the fixed context, activate a rule when the same recipient `p` can see its owning and required supporting units in the window, its guard holds, and no visible explicit exception applies. Apply the same recipient binding to qualifications, definitions and override edges. A three-rule finding needs one joint witness for the full core.
4. Apply explicit precedence to opposing `must` and `forbid` effects on the identical ground action. An override edge names its target and carries supporting evidence. Traverse the acyclic graph from higher to lower priority. Only an active, undefeated rule can defeat its directly named target. Limit defeat to the grounding and context in which both apply. Cycles and incompatible targets produce a configuration or interpretation diagnostic. An edge requires explicit evidence of precedence.
5. Compile `must(a)` to `a`, `forbid(a)` to `¬a`, and `requires(a, anyOf(b,c))` to `a → (b ∨ c)`. Ask Z3 for a compliant action assignment with the context fixed. One satisfiable context completes only that context’s check.
6. On an unsatisfiable result, track effective clauses by rule and grounding. Shrink the returned core by deletion, replay it under the same context, and verify that dropping each remaining clause restores satisfiability before labeling it inclusion-minimal. Keep context facts, override decisions and other assumptions in the explanation. Z3 supports tracked assertions and cores. Its default core may contain unnecessary clauses. [Programming Z3, §4.4](https://theory.stanford.edu/~nikolaj/programmingz3.html#sec-cores)
7. Record at least one replayable core for each unsatisfiable component and projected context visited. Group recurring cores by profile, source rules, and normalized bindings, retaining a representative witness. Multiple sufficient explanations can exist in one component. The report states the core enumeration policy.
8. Keep useful findings if a later context times out. Any timeout, solver `unknown`, search cap, or interrupted traversal marks the affected search incomplete. Claim absence of modeled conflicts only after exhausting the named profile’s projected assignments for every component. Make projected assignments per component and solver time per query configurable. Report limits, actual counts and unfinished components.

Use independent classifications. **Direct** means opposite effects on the same action. **Conditional** means guard overlap matters. **Compositional** means an irreducible core needs three or more source rules. **Cross-skill** means contributing rules span multiple skills. One finding can carry several tags.

### 5. Developer workflow and diagnostics

```sh
semanticlint check ./skills
semanticlint check ./skills --profile delivery --manifest profiles.yml --save-run run/
semanticlint analyze-ir tests/fixtures/retry/ir.json --format json
semanticlint explain run/ SL-003
semanticlint replay run/ --overlay interpretations.json --format json
```

These are proposed CLI contracts. `explain` takes a run-local **finding ID**, so two findings of the same diagnostic class are independently addressable. `replay` uses a frozen snapshot, validates overlay digests, and records the corrected result as a new run. A maintainer can amend a rule, confirm or reject an alias, or mark an interpretation unresolved. Each change records its reason and target. Source edits or changes to a referenced definition, profile, or registry invalidate affected approvals before the next live check. A saved historical run remains reproducible.

**Illustrative finding**

```text
SL-003   Compositional contradiction · cross-skill
Profile  delivery-task, same agent and artifact, during this task

delivery/SKILL.md:8       Every delivery task must perform independent verification.
verification/SKILL.md:12  Independent verification in this task requires spawning a subagent.
delivery/SKILL.md:15      Never spawn a subagent during a delivery task.

Witness  Agent A handles artifact X in an active delivery task.
         A must verify X. Verification requires A to spawn a subagent.
         Spawning one violates line 15. Omitting it prevents required verification.
Visible  Both bodies may be loaded for Agent A under the delivery profile.
Evidence IR core replayed. Three effective rules. Source interpretation machine-checked.
Review   Inspect whether line 12 really makes delegation the only permissible route.
```

Every diagnostic includes its profile and source excerpts, stable source locations, context witness, any required same-actor or same-object bindings, effective rules and assumptions, search status, interpretation status, and a replay identifier. Human-readable and JSON outputs must agree.

| Report status | Interpretation and consumer behavior |
| --- | --- |
| Established over the IR | The core replays under the stated assumptions. Identify whether each relevant interpretation is machine-checked, human-reviewed, or explicitly supplied. An automatic actionable finding additionally needs supported mappings, no outstanding material interpretation dispute, and evidence for joint disclosure in the profile. |
| Suspected | A plausible alias, missing exception, competing interpretation, or unconfirmed coactivation materially changes the claim. List the smallest clarification that would settle it. A conditional core may be attached. |
| Unresolved | The source or environmental information cannot support a check in this fragment. Give the passage and a specific reason. |
| No supported conflict found | State the profiles, analyzed hard clauses, unresolved clauses, missing references, and whether the solver finished. A zero-rule run should say “no analyzable hard rules.” |

Default checks produce reports for review. For CI, an explicit `--fail-on reviewed-conflict` setting makes a replayed conflict in a declared profile blocking when its critical mappings and assumptions have current approval. Proposed exit codes are `0` for a completed report with no configured blocking finding, `1` for a configured blocking finding, and `2` for invalid input, operational failure, or an incomplete requested solver search. When failure and findings coexist, return `2` and preserve both in JSON. Unresolved source clauses contribute to coverage reporting.

### 6. Required acceptance fixtures

Each semantic fixture includes exact Markdown, profile, expected spans, an independently authored IR, acceptable witnesses or reason for abstention, and expected end-to-end disposition. For tiny fixtures, a separate exhaustive Boolean evaluator checks all context/action assignments. Implement it without using the Z3 encoder.

| Fixture | Expected result |
| --- | --- |
| Opposite unconditional retry rules in one file | Internal direct conflict. |
| Failed-operation obligation and non-idempotent prohibition across two skills | Only the shared failed, non-idempotent operation witnesses a conflict. |
| Same words for retries of two different operations, or actions assigned to different actors | A compliant assignment exists. |
| General retry rule except non-idempotent operations | No retry conflict. |
| Explicit priority for the prohibition on that operation | Preserve mandatory retry on other failed operations. Cyclic priority is reported separately. |
| Retry is preferred or permitted alongside a hard prohibition | No incompatible pair of hard rules. |
| Three verification rules in the example diagnostic | All three are needed. Removing any one admits a compliant assignment. |
| Verification required only if delivery occurs, delivery optional in this window | Abstaining from delivery is compliant. |
| Prior verification expressly satisfies the duty, and the profile says the artifact was already verified | No delegation is required during this window. Unspecified timing with a material effect is unresolved. |
| Verification can use either delegation or an available local reviewer | A compliant local-review choice exists. |
| Two skills apply to mutually exclusive modes | No cross-skill finding in either declared mode. |
| Catalog description prohibits retry while a possibly visible body requires it | Report the description span and body span. Making that body undisclosable removes this finding. A neutral description leaves its undisclosed body inactive. |
| A body requires verification and forbids delegation. Its linked local procedure defines delegation as necessary for verification. | When the procedure can be disclosed, include its definition span in the core. If the definition is missing or its meaning depends on an unavailable reference, surface that uncertainty. |
| A host can disclose any two of three necessary skills, yet never all three | No three-skill contradiction. Lifting the triple restriction exposes the three-rule core. |
| Two isolated agent contexts each see one opposing rule about the same named performer and action | Neither recipient has a conflict. Expose both passages to one recipient and the conflict appears. |
| Add 100 unrelated, neutral skills to a directory containing one conflicting pair | Find the same pair within the same per-component assignment budget. |
| An optional third skill explicitly overrides a duty in a conflicting pair | Report the pair with the override unit absent. If the host always exposes the override with the pair, the conflict disappears. |
| A paragraph quotes a forbidden instruction as a bad example | Preserve it as an example. It contributes no duty. |
| “Delegate to a reviewer” could refer to a human or a subagent | An unsupported alias cannot produce an established source-level claim. |
| Broken local link, missing procedure definition, unsupported “eventually,” contradicting profile facts, solver timeout | Respectively report structural reference defect, unresolved symbol, unsupported time semantics, invalid profile, and incomplete search. |
| Maintainer corrects an alias, replays, then edits its source passage | Finding disappears on corrected replay. New source marks old approval stale. |

### 7. Evaluation protocol

**Corpus.** Start cheaply with these fixtures and a development set of roughly 10–15 unrelated real skills to expose extraction failures. For the comparative study, target roughly 100 pinned, redistributable public `SKILL.md` sources across independent repository or template families. Record license, revision, file digest, source selection method, and genuine or constructed composition. Split by family before tuning. A fork, copied template, mutation, paraphrase and its source stay on the same side of the split.

Use two panels. A **natural panel** samples unmodified documents from real distributions with evidence of what one agent could see together, and estimates alert yield and triage burden. A **challenge panel** contains labeled internal and cross-skill defects, three-or-more-rule conflicts, paraphrases, and clean near-misses. Include matched edits that introduce and repair a defect and profiles that change disclosure without changing the prose. Unmodified documents still require annotation. Record ambiguity explicitly. Keep natural prevalence and injected prevalence separate.

**Gold standard.** Two annotators independently label held-out source clauses, hard/soft modality, actor, action, arguments, conditions, exceptions, necessary prerequisites, admissible context and sufficient source spans. Resolve disagreement with an adjudicator before unlocking scores. Blind adjudicators to the producing method when checking novel reports. Keep legitimately ambiguous cases for abstention scoring. Each gold conflict needs a feasible circumstance and a human explanation of why all modeled choices violate an instruction.

| Arm | Information and comparison |
| --- | --- |
| A. Structural lint | Parse frontmatter, structure and local references. Score structural findings separately and report semantic detection on the same cases. |
| B. Direct LLM review | Give the model source, profile, shared project glossary and output contract. Ask for conflicts, necessary spans, context and reasoning. Tune the prompt on development data. |
| C. Extraction + LLM reasoning | Feed the same frozen extracted and normalized IR used by D to a model for conflict analysis. Supply the same spans and profile. |
| D. Extraction + deterministic reasoning | Run the specified checker on that IR. Also run D against gold IR to isolate checker errors from extraction errors. |

Use the same held-out bundles, source access, profile information, model family for LLM stages and total per-bundle inference budget. Let B spend its budget on a second review if that helps on development data. Charge C and D each the full cold cost of extraction and verification when comparing costs, even if the harness shares cached IR. Tune selection thresholds on development data and freeze models, prompts, thresholds, budgets, solver, registry and match rules before the holdout. Keep holdout-specific human aliases and corrections out of the automatic score. Report the human-assisted path and minutes of review separately. Repeat B, C and D on the same preselected sample for three fresh runs per arm. Keep repeats within their source family when estimating uncertainty. Replaying saved IR must be deterministic.

**Scoring.** Match predicted and gold findings one-to-one within the profile. Require the same incompatible behavior, responsible passages or an accepted alternative core, and a feasible shared context. Pool novel claims from all arms for blinded adjudication, freeze the augmented gold set, then rescore every arm against that same set. Repeated witnesses for one defect count once. An abstention, timeout or empty report counts as a miss for a supported gold conflict. Define fragment support from the source annotations before evaluation. Report both supported recall and recall across all labeled conflicts.

| Measure | What to publish |
| --- | --- |
| Diagnostic precision and recall | TP, FP and FN with denominators, by arm, panel, core size and single/cross-skill scope. Score the automatic actionable tier before any maintainer confirms it. Publish suspected-alert volume and accuracy separately. |
| Fidelity | Correct modality, guard, exception, binding, alias and dependency direction against human labels. Identify the first pipeline stage responsible for each sampled error. |
| Witness and attribution | Fraction of claims with a human-valid context and sufficient source set. Each reported formal core must replay. |
| Stability and coverage | Finding agreement across meaning-preserving paraphrases and fresh model runs. Count eligible normative clauses captured, unsupported clauses, unresolved references, and source units classified. |
| Utility and cost | Actionable natural findings, false alerts per 100 natural bundles, review minutes per finding, model calls/tokens and cost, cold/warm median and p95 latency, and contexts checked. |

**Proposed continuation rule.** Before opening the holdout, freeze a 90% automatic actionable-precision floor, 70% D recall on supported defects, and a 10-percentage-point gain in compositional recall for D over B at the common precision floor. Score the comparison on the challenge panel at the frozen operating points. Require every gold-IR conformance fixture and core replay check to pass. An arm with no predictions has undefined precision. If B misses the floor in development or on holdout, the common-precision comparison is inconclusive. Publish both precision/recall pairs and preserve the frozen thresholds.

For the natural panel, propose a minimum of two blind-adjudicated source conflicts from independent families found by D and missed by B. Publish the B-only wins as well. For false alarms, sample one primary natural bundle per independently sampled source family and require the one-sided 95% exact binomial upper bound on the proportion receiving **any false actionable alert** to be at most 5%. With zero affected bundles, this requires at least 59 independent bundles. Report the sampling frame, affected-bundle fraction, and total false alerts per 100 bundles. If independence or sample size is inadequate, treat the rate as pilot evidence. A seed-only win answers the reasoning question while leaving real-skill value open.

Report paired 95% intervals by resampling source families for the recall difference. Use family-level uncertainty estimates for challenge-panel precision and recall, with a preregistered minimum number of informative families and a conservative interval for sparse or boundary counts. Proceed when conformance passes, natural incremental wins exist, and the appropriate confidence bounds clear the preregistered margins. A sparse or threshold-straddling result is inconclusive. Size the confirmatory panels from the pilot before unlocking the holdout. Freeze any follow-up cohort size, spending ceiling and stopping rule in advance, and report its results separately. If gold-IR analysis passes but end-to-end fidelity fails, repair the most consequential observed extraction error and evaluate on fresh data. If direct review matches the pipeline at comparable precision and cost, pause expansion of the symbolic backend.

### 8. Build sequence

| Milestone | Deliverable | Exit test |
| --- | --- | --- |
| 1. Formal checker | Standalone Python package, versioned Pydantic schemas, Z3 adapter, independent tiny truth-table evaluator, handwritten IR. | Retry case, three-rule verification case and near-misses agree with the independent evaluator. Every asserted core replays. |
| 2. Markdown to finding | Source mapper, one schema-constrained extractor adapter, verifier, typed registry, declared profiles, review overlay and text/JSON reports. | A maintainer can run the real CLI on the fixture bundles, inspect every necessary span, correct one interpretation, and reproduce the changed report offline. |
| 3. Comparative experiment | Pinned corpus manifest, annotation guide, split, four runners, adjudicated labels, cost logs and scoring script. | A single evaluation entry point regenerates metrics from frozen outputs. Publish misses, false alerts and the continue/pivot/stop result. |
| 4. Developer release | Cache, semantic diff, SARIF, PR/CI integration and documented installation. | A changed skill or alias rechecks affected profiles, detects a seeded regression, and produces the same substantive findings as a full rerun. |
| 5. Procedural and runtime extensions | Candidate semantics for ordering, capabilities, reachability, deadlock and bounded traces. Later, compare original and repaired skills on paired execution scenarios. | Each new diagnostic has positive and near-miss fixtures, explicit bounds, and separate evidence of fidelity. For runtime value, measure compliant completion across fixed tasks and repeated agent runs. |

Begin a fresh experimental repository with `src/semanticlint/` for parsing, IR, extraction, analysis and reporting, `tests/fixtures/` for Markdown/profile/IR triples, and `bench/` for corpus, labels and runners. Keep IR serialization independent of Pydantic, the extraction provider and Z3. The first working command should accept the handwritten retry IR and output the impossible context. Add a model only after the checker passes its positive and negative controls.

#### Proposed demonstration in a server’s CI

A maintainer broadens a recovery instruction in a pull request. The server can still publish every skill successfully, yet an agent using two of them can receive incompatible instructions. Show the maintainer the changed sentence, the other skill it conflicts with, and the exact operation for which there is no compliant choice.

Use a small MCP server that serves two repository-backed skills through the Skills extension. Its CI installs SemanticLint as a consumer. A local protocol test starts the PR revision, enumerates its skills with `skills/list`, obtains their entries with `skills/get`, reads their `SKILL.md` bodies with `resources/read`, and checks catalog descriptions and body digests against the inputs supplied to the linter. A demo host profile states that the same agent can receive both bodies during one retry decision. The protocol test establishes what the server publishes. The profile supplies the disclosure policy. [MCP Skills extension](https://github.com/modelcontextprotocol/ext-skills/blob/main/specification/stable/skills.mdx) · [SDK serving example](https://py.sdk.modelcontextprotocol.io/advanced/skills/)

| Server revision | Expected CI result |
| --- | --- |
| Baseline. Recovery says “Retry every failed idempotent operation.” Payments says “Never retry a non-idempotent operation.” The shared profile defines idempotent and non-idempotent as complements. | Green. Both published rules are accounted for and the check completes. |
| PR removes the word “idempotent” from recovery. | Red. Both skills still publish correctly. SemanticLint cites both source lines and identifies a failed, non-idempotent operation governed by both skills. |
| Author restores the qualification. | Green. The same failed, non-idempotent operation is still possible. Declining to retry now satisfies the instructions. |

Use the handwritten IR and existing controls for milestone 1. Stage the author-facing demonstration when milestone 2 can extract rules from the edited Markdown through the real CLI. The demo CI job reads the JSON report and fails on an actionable contradiction in its declared profile. Missing served inputs, stale formalizations, unresolved fixture rules, or an incomplete check fail the job with a distinct diagnostic. This small consumer check can run before a reusable CI integration ships. Keep these published teaching fixtures on the development side of the blinded benchmark.

Incremental checks must key extracted artifacts on source and dependency digests, schema, model and prompt. Changes to aliases, profiles, overlays or solver semantics invalidate the corresponding analysis and report caches. Full reruns are the reference result. Defer editor integrations and runtime enforcement until the experiment answers the comparison question.

Prior work on controlled-language engineering requirements supplies a precedent for testing the combination of LLM interpretation and formal logic. Ordinary skill prose and selectively activated bundles are the empirical target here. [Gärtner & Göhlich, 2024](https://link.springer.com/article/10.1007/s10515-024-00452-x)
