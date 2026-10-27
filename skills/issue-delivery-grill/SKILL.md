---
name: issue-delivery-grill
description: Relentlessly interview the user and inspect the repository to refine a plan, issue, PRD, or feature proposal into an implementation-ready Issue Delivery Orchestrator spec with the canonical YAGNI, architecture, user-story, and risk-coverage format.
---

# Grill To Spec

## Own the spec, not its destination

Own the interview, repository research, decision quality, and final spec body. Do not create
implementation tickets; ticket slicing belongs to the ticket-planning workflow after the spec is
approved. Make clear that a published spec alone is not enough to start Issue Delivery
Orchestrator: the Linear issue also needs a non-empty canonical tickets block.

Return the canonical spec in the conversation unless the user explicitly requests a file or a Linear update. When the user requests publication to Linear, obtain the issue identifier or URL, finish and confirm the spec first, then invoke `issue-delivery-spec-publisher` to preserve the destination markers and unrelated issue content. Do not update an external issue silently.

## Build repository awareness

Read every applicable `AGENTS.md` before evaluating the proposal. Resolve the repository root, then inspect the relevant context map, domain documentation, ADRs, source task, owning code, schemas, routes, and existing tests.

Within Issue Delivery Orchestrator, consume the Linear description and comments returned by
`issue-delivery <issue> read-linear-issue`. Never invoke `$linear`, `linear:linear`,
`codex_apps.linear`, or another Linear MCP/app tool. Treat a failed personal-key CLI read as a
blocker instead of falling back to a shared OAuth connection.

Use repository exploration to answer factual questions instead of asking the user. Verify claims about current behavior and use canonical terminology from the code and documentation. Distinguish verified facts, user decisions, and inferences; never turn an inference into an agreed requirement.

Do not create missing documentation or instruction files. Do not implement product code while producing the spec.

## Run the interview

Interview the user until every decision that materially changes the `Now` implementation is resolved. Ask exactly one question at a time and wait for the answer. Include a recommended answer with every question.

For each question:

1. Explain the decision in concrete repository and user-flow terms.
2. State the evidence already found.
3. Recommend an answer and explain why it fits the current constraints.
4. Explain only the meaningful alternatives and how they change the solution.
5. Ask for one decision.

Do not revisit settled decisions unless new evidence contradicts them. Do not debate rules already governed by `AGENTS.md`; state the rule briefly and apply it.

Walk the decision tree in dependency order:

1. Problem, affected actors, current behavior, and observable impact.
2. Desired user journeys, success outcomes, failure outcomes, and edge cases.
3. `Now`, `Deferred`, `Rejected`, and neighboring out-of-scope behavior.
4. Domain invariants, permissions, tenant boundaries, audit requirements, transactional behavior, concurrency, and error handling when relevant.
5. Ownership boundaries, dependency direction, API and data flow, persistence, migrations, rollout, and compatibility or deletion strategy.
6. Material risks, existing test evidence, the lowest sufficient test seam, and final computer-use flows for UI work.

Skip branches that are demonstrably irrelevant. Keep low-level file and function choices for the implementation agent unless they expose a public surface, encode domain ownership, or change the test strategy.

Do not finalize a spec with a material `Por confirmar`. Continue questioning until the choice is resolved, or move it to `Deferred` or `Out of Scope` only when the `Now` behavior does not depend on it.

## Enforce YAGNI and Deep Modules

Require observable evidence for every item in `Now`: a current actor, current workflow, current defect, or current operational need.

Record future behavior in `Deferred` with the observable trigger that would justify revisiting it. Record explicitly rejected solution alternatives in `Rejected`; do not encode either category as placeholder code, flags, fallbacks, exports, adapters, or compatibility layers.

For every changed seam, resolve and record:

- Owner.
- Knowledge hidden behind the seam.
- Real current consumers.
- Public-surface delta.
- Deletion test: the condition under which compatibility or old behavior can be removed.
- Adapter decision: introduce an adapter abstraction only when two real adapters exist.

Prefer the smallest public interface that keeps domain knowledge inside its owner. Challenge pass-through wrappers, duplicate ownership, speculative configurability, and surfaces larger than the knowledge they hide.

## Build the risk catalog

Before designing coverage, read [references/risk-catalog.md](references/risk-catalog.md) completely
and fill the `## Risk Catalog` section for all six categories. These categories produced most of the
blockers that reached PR review in past runs; modeling them here is cheaper than discovering them one
review round at a time.

- Answer each category from the repository first. Investigate code, callers and existing tests; do
  not ask the user what the code can tell you.
- Mark `No` only with concrete evidence in backticks: the path, symbol or `rg` command that shows the
  risk cannot occur. "Not relevant" is not evidence.
- Mark `Yes` whenever the change could plausibly hit the category, and name the `Testing Decisions`
  row that covers it in `Coverage`.
- Ask the user only when a category exposes a real product decision, such as what the operator sees
  when the remote result is unknown. Ask it through the normal one-question-at-a-time interview.
- When `consumers-platforms` applies, add `### Consumer inventory` built from `rg` output: every real
  consumer of each changed concept, its surface (web, Android, iOS, PDF, email, report) and the
  ticket or decision that handles it. A consumer left unmigrated must appear as `Out of Scope` with a
  reason, never silently.
- When `concurrency` or `external-failure` applies, add `### State model` covering every state the
  user or system can be in, including conflict, retry and, for remote writes, the unknown-outcome
  state. Design the exits before implementation; do not leave them for review rounds.

`issue-delivery-spec-publisher` rejects a spec whose catalog, inventory or state model is missing
or malformed.

## Design coverage before finalizing

Inventory existing evidence before proposing new tests. Map every material risk to exactly one row in `Testing Decisions` using:

- `extend` when existing evidence can gain the unique assertion.
- `replace` when stronger or lower-level evidence should supersede redundant coverage.
- `create` only when no existing test owns the risk.
- `none` when the change adds no distinct failure signal; explain why.

Choose the lowest sufficient seam. Use E2E only for a unique browser or assembled-system risk. Name the marginal failure signal for every new test and identify any superseded test.

Apply [retained-tests.md](../test/references/retained-tests.md): distinguish committed regression
tests in `Testing Decisions` from temporary execution evidence in `QA Script`. A QA case does
not require its own permanent test; keep one owner per invariant and justify any additional
integration signal instead of repeating the same internal scenarios across layers.

For UI changes, identify the affected user flows and expected visible result so Issue Delivery
Orchestrator can perform final computer-use validation and publish current screenshots. Do not turn
this fixed closure phase into a separate implementation ticket.
For Conductor, define mobile acceptance as web simulation (phone viewport, touch, simulated
keyboard and focused code tests for lifecycle callbacks). Do not introduce a physical-device/ADB
requirement merely because the product is Expo/native. Record OS-specific limits as observations.
Use [conductor-validation.md](../issue-delivery-orchestrator/references/conductor-validation.md):
native contracts use Jest/scripts against real code with mocked OS adapters; browser UI uses
localhost ports 3001–3004. State these methods in QA Steps/Evidence so a passing native-contract
test does not later turn into a phone or waiver requirement.

## Design the QA script

Write `## QA Script` after `Testing Decisions`, following the orchestrator's
`references/qa-script.md`. It is the script manual revision will execute against the running
system, not a list of unit tests:

- one `happy-path` case per user story, with the seeded data and the observable result;
- `edge-case`, `authorization` and `failure` cases for the limits, roles and uncertain states the
  spec names;
- for `concurrency`, a case that fires the competing operations in parallel through `API` or
  `command` and checks the final invariant, plus the committed race test that keeps protecting it
  (`Regression test`, usually a `*.race.db.spec.ts`), which must also appear in `Testing Decisions`;
- every `Applies: Yes` risk category named in `Covers`.

For cases that persist data, retry writes, roll back or reject mutations, include `DB` in the
`Evidence` cell and name the final invariant to check (IDs, values, relations, audit, counts or
unchanged state). Verification uses before/after read-only queries on the local runtime DB,
not just UI success or an HTTP status. Purely visual cases may explain why DB is not applicable.

Present the QA Script with the spec: its approval is part of the spec approval.

The QA chat runs on `sonnet-5-5-1m` with `high` effort by default. Recommend another model only
when the QA Script clearly departs from the usual size or risk, and add `QA Model:` (and
optionally `QA Effort:`) right below `Spec ID`: a lighter model such as `haiku-5-5-1m` for a few
visual or single-step cases, or a stronger one such as `opus-5-5-1m` for concurrency, money,
permissions or many cases with DB verification. Give the reason in one line when presenting the
spec; the user approves it with the spec. Otherwise omit both lines.

### Small lane

Propose `Lane: small` below `Spec ID` when the issue is small and low risk: at most two tickets,
about 300 estimated lines in total, no concurrency risk, and a QA Script of at most four cases (a
happy path per user story plus only the applicable edge, authorization or failure case). The
publisher rejects `Lane: small` with concurrency or more than four QA cases. The lane keeps every
gate (fresh QA with DB verification, refactor review, PR convergence) and changes only the cost:
fewer QA cases and a QA chat with `medium` effort. Ask the user to approve the lane with the spec;
when in doubt, omit it.

## Resolve the architecture

Define a high-level implementation architecture sufficient for an autonomous implementer. Cover the relevant owner, boundary, data flow, persistence, authorization, failure behavior, rollout, and testing decisions without prescribing a file-by-file patch.

Call out conflicts with repository standards immediately. Prefer existing module boundaries and naming. Do not produce a premature architecture summary while blocking decisions remain unresolved.

At the end of the interview, summarize the agreed architecture and compare it with the original source task. Surface obsolete requirements, changed invariants, new constraints, resolved ambiguities, API or data-model changes, permission or audit implications, and newly excluded scope.

## Generate the canonical spec

Reuse an existing `Spec ID` when updating a spec. Otherwise use `SPEC-<LINEAR-IDENTIFIER>` when a Linear identifier is available. If neither exists, ask for the intended Spec ID before generating the final artifact.

Produce the following headings in exactly this order. Fill every section with concrete decisions; write `None` only when a section genuinely has no content. Do not emit placeholders.

```md
Spec ID: SPEC-...
Lane: <opcional; `small` sólo si cumple los criterios de vía chica>
QA Model: <opcional; sólo si el QA no debe usar el default>
QA Effort: <opcional; requiere QA Model>

# <Spec title>

## Problem Statement
<Verified current behavior, affected actors, impact, and why the problem matters now.>

## Solution
<Observable behavior and high-level architecture that solve the problem.>

## Scope — YAGNI

### Now
<Only evidenced behavior required by the current need.>

### Deferred
<Deferred capability plus the observable trigger for revisiting it, or None.>

### Rejected
<Explicitly rejected alternatives and why, or None.>

## User Stories
- US-001: As <actor>, I want <capability>, so that <outcome>.

## Implementation Decisions
<Relevant ownership, dependency, API/data-flow, persistence, permission, audit,
transaction, error-handling, rollout, migration, and compatibility decisions.>

### Changed seams
| Seam | Owner | Knowledge hidden | Real consumers | Public-surface delta | Deletion test |
| --- | --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... | ... |

## Risk Catalog

| Risk category | Applies | Evidence | Coverage |
| --- | --- | --- | --- |
| `concurrency` | Yes/No | `<path, symbol or rg command>` | <Testing Decisions row, or — when No> |
| `external-failure` | ... | ... | ... |
| `async-lifecycle` | ... | ... | ... |
| `edge-cases` | ... | ... | ... |
| `consumers-platforms` | ... | ... | ... |
| `roles-authorization` | ... | ... | ... |

### Consumer inventory
<Only when consumers-platforms applies.>
| Changed concept | Consumer | Surface | Handled by |
| --- | --- | --- | --- |

### State model
<Only when concurrency or external-failure applies.>
| State | Entered when | User sees | Allowed actions | Exit |
| --- | --- | --- | --- | --- |

## Testing Decisions

| Risk owner | Existing evidence | Decision | Lowest sufficient seam | Unique signal | Superseded test |
| --- | --- | --- | --- | --- | --- |
| ... | ... | extend/replace/create/none | ... | ... | ... |

## QA Script

| QA ID | Type | Covers | Surface | Steps | Expected | Evidence | Regression test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QA-001 | happy-path | US-001 | UI/API/command | <precondición y pasos> | <resultado que decide PASS> | <captura o salida> | — |

## Out of Scope
<Neighboring behavior or systems intentionally unaffected by this spec.>

## Further Notes
<Source links, UI validation flows, screenshot expectations, rollout notes, or None.>
```

Keep the exact `Testing Decisions`, `Risk Catalog`, `Consumer inventory`, `State model` and
`QA Script` table headers because the orchestrator validates them before publication. Keep
`Deferred`, `Rejected`, and `Out of Scope` semantically distinct:

- `Deferred`: potentially valuable later, with a trigger.
- `Rejected`: considered solution choices deliberately not selected.
- `Out of Scope`: neighboring behavior this spec does not change.

When no public seam changes, keep `### Changed seams` and state `None`; do not invent one.

Output the spec body without orchestrator HTML markers. `issue-delivery-spec-publisher` owns
insertion between the canonical markers and initialization of the tickets block.

Before presenting the spec, verify:

- A non-empty `Spec ID` and title exist.
- Every required heading appears once and in order.
- `Now` contains no speculative capability.
- Every changed seam has the required ownership record.
- Every material risk has a coverage decision and unique signal.
- The Risk Catalog covers all six categories; every `No` cites repository evidence and every
  `Yes` names its Testing Decisions row.
- Consumer inventory and State model exist whenever their categories apply, and the state model
  includes the unknown-outcome state for remote writes.
- The QA Script has a happy path per user story, covers every applicable risk, and each
  concurrency case names its committed race test.
- No unresolved material decision remains.
- The spec is internally consistent with repository rules and the source task.

## Reconcile and publish

Present the final spec and briefly identify any material change from the source task. Ask for approval before replacing an external source of truth.

If the user approves a Linear publication, invoke `issue-delivery-spec-publisher` with the destination. Preserve unrelated description content and the tickets block, and let that skill manage the mandatory HTML markers through the personal-key CLI. It must not use the Linear MCP connector, and comments remain controlled by `LINEAR_COMMENTS_ENABLED`.

After publishing the spec, report that `issue-delivery-ticket-publisher` must produce the approved
ticket block before `issue-delivery-orchestrator` can enter Implement.

Do not modify `AGENTS.md` during the interview. If the resulting decision creates a genuinely general architectural rule, explain it after the spec is complete and ask for explicit approval before editing `AGENTS.md`.

## Offer ADRs sparingly

Offer an ADR only when the decision is hard to reverse, surprising without context, and the result of a real trade-off. When the user accepts, read [references/adr-format.md](references/adr-format.md) completely and follow the repository's more specific ADR convention when one exists.
