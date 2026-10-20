---
name: issue-delivery-ticket-publisher
description: Slice an approved Issue Delivery Orchestrator spec into implementation tickets, obtain approval, and publish them into the canonical Linear tickets block.
---

# Issue Delivery Ticket Publisher

Own ticket slicing, AFK/HITL classification, dependencies, approval and Linear publication. Use the
approved canonical spec as the only source of product scope.

## Required input

- Linear issue with a non-empty canonical spec block.
- Effective `linear_marker_prefix` from
  `python3 <plugin-root>/scripts/issue-delivery config`.
- Applicable repository instructions and existing validation conventions.

## Ticket format

Each ticket must use:

```md
Ticket ID: TICKET-001
Parent Spec: SPEC-...
Status: Pending
Type: AFK | HITL
UI Changes: Yes | No
Estimated size: <product lines, excluding tests, generated migrations, lockfiles and docs>
Blocked by:
- None

# Ticket title

## What to build
...

## Current Value — YAGNI
...

## Public Surface Delta
...

## Deep Modules
...

## Coverage Delta
...

## Not Building — YAGNI
...

## Acceptance Criteria
- [ ] ...

## User Stories Covered
- US-...

## QA Cases Covered
- QA-...

## Validation
- Run: `...`

## Implementation Notes
...
```

For `Type: HITL`, insert `## HITL Justification` before Acceptance Criteria with the autonomy
blocker, why available computer use cannot complete it, and the minimum human action required.
Use HITL only for a real authority, credential, physical-world or unavailable-surface dependency,
not because implementation is difficult.

## Slicing and approval

1. Read the spec, repository instructions and existing validation seams.
2. Produce the smallest independently committable tickets that preserve dependency order.
3. Cover every `Now` requirement and user story exactly where it is implemented; do not turn the
   fixed final UI-review phase into an implementation ticket.
   Every `Risk Catalog` row marked `Yes` must be covered by the `Coverage Delta` of the ticket that
   implements it, and every `Consumer inventory` row by the ticket named in `Handled by`.
   Assign every `QA Script` case to exactly one ticket under `QA Cases Covered`: the ticket whose
   change makes it pass. The ticket covering a `concurrency` case also implements the race test
   named in its `Regression test` and lists it under `Coverage Delta` and `Validation`.
   Apply [retained-tests.md](../test/references/retained-tests.md): `Coverage Delta` names the
   permanent tests and their unique signal, while `QA Cases Covered` assigns execution of the
   full QA script. Do not turn every QA case into a new committed test or duplicate an
   invariant across hooks/services and screens without a distinct integration risk.
4. Use unique sequential IDs, valid dependency references and at least one focused validation
   command per AFK ticket.
5. Estimate each ticket's product lines from the files and patterns it will touch. When the sum
   exceeds the profile's `slicing.maxProductLines` (4000 in TurboShop), propose slices with the
   breakdown, following the orchestrator's
   `references/slicing.md`: tickets per slice, dependencies between slices, and the mechanism the
   dependencies imply (stack when a slice needs another's code, independent sub-issues otherwise,
   stack depth at most `slicing.maxStackDepth`). Present it as a recommendation with its cost;
   splitting is the user's decision and keeping one PR is a valid answer.
   At or below that limit, keep one PR per issue. Touching several apps/owners or exceeding an
   initial estimate does not by itself justify proposing a split.
6. Present the complete breakdown and obtain explicit approval before publishing.
7. Immediately call out every HITL ticket with ID, title, reason, required action, dependencies and
   approximate pause point.

## Publication contract

For prefix `<prefix>`, replace only:

```md
<!-- <prefix>:tickets:start -->
...
<!-- <prefix>:tickets:end -->
```

Preserve the spec block and all unrelated description content. Write the approved tickets body
under the current run directory and publish it with:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> publish-linear-block \
  --block tickets --body-file <path>
```

Trust only a successful CLI result. It uses the configured personal Linear API key and verifies the
persisted block after writing. Do not use `$linear`, `linear:linear`, `codex_apps.linear`, or another
Linear MCP/app tool for reads or writes. The CLI owns remote access and optional comments and must
obey `LINEAR_COMMENTS_ENABLED`. Do not alter `AGENTS.md` or create a repository copy.
