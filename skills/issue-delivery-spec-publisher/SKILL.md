---
name: issue-delivery-spec-publisher
description: Publish an approved Issue Delivery Orchestrator spec into its canonical Linear description block while preserving tickets and unrelated issue content.
---

# Issue Delivery Spec Publisher

Own only the Linear destination and format contract. The approved spec body must come from
`issue-delivery-grill`; do not rewrite architecture or scope while publishing.

## Required input

- Linear issue ID, identifier, URL, or unambiguous current issue.
- The explicitly approved canonical spec body.
- The effective `linear_marker_prefix` from
  `python3 <plugin-root>/scripts/issue-delivery config`.

## Destination contract

For a prefix `<prefix>`, write the spec only between:

```md
<!-- <prefix>:spec:start -->
...
<!-- <prefix>:spec:end -->
```

Preserve all content outside the orchestrator blocks. If no tickets markers exist, initialize:

```md
<!-- <prefix>:tickets:start -->
<!-- Tickets pending. Run `issue-delivery-ticket-publisher` after approving the breakdown. -->
<!-- <prefix>:tickets:end -->
```

## Validation

Before publishing, require a non-empty `Spec ID`, title, and exactly one occurrence of every
canonical heading in the order produced by `issue-delivery-grill`. Require the exact
`Testing Decisions` table header and a complete `Risk Catalog` (plus `Consumer inventory` and
`State model` when their categories apply). Reject placeholders and unresolved material decisions.
`publish-linear-block --block spec` enforces these structural rules and lists every violation;
return to `issue-delivery-grill` to fix the content instead of editing the table to pass.
It also rejects `Lane: small` with concurrency or more than four QA cases, and `Lane: express`
with concurrency or roles-authorization applying or more than four QA cases. When a lane is
rejected, drop the line and return to the normal flow; do not trim the spec to fit the lane.

## Workflow

1. Validate the approved body from the current conversation. The local CLI reads the live Linear issue and preserves its current description.
2. Validate the already approved spec without changing its decisions.
3. Replace only the canonical spec block; initialize only a missing tickets block.
4. Write the approved body to a file under the current run directory and publish it with:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> publish-linear-block \
     --block spec --body-file <path>
   ```

5. Trust only a successful CLI result. The command uses the configured personal Linear API key,
   reads the issue back, and verifies markers, body, preserved tickets and unrelated content.
6. Do not use `$linear`, `linear:linear`, `codex_apps.linear`, or another Linear MCP/app tool for
   reads or writes. The personal-key CLI owns remote access and optional comments and must obey
   `LINEAR_COMMENTS_ENABLED`.

Do not write a repository copy, alter `AGENTS.md`, expose secrets, or publish without explicit user
approval. Report the issue, Spec ID and which blocks were updated or initialized.
