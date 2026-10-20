# Issue Delivery Orchestrator

A dual Codex and Claude Code plugin for taking a Linear issue through specification, ticket
slicing, implementation, focused validation, manual UI review, pull request creation, and
automated review convergence.

The plugin is self-contained: its orchestration engine and workflow skills live in this repository.
It supports four fixed workspace modes. Claude Code uses the host-independent `vanilla` mode,
or `conductor` inside a Conductor Cloud workspace:

- `codex`: work in a Codex app worktree and review UI through the in-app Browser, with headless
  Playwright assistance limited to demonstrated `file-upload` or `hover` capability gaps.
- `superset`: adopt a Superset worktree and review UI through Cua Driver in a dedicated browser.
- `vanilla`: adopt any user-prepared checkout or worktree from Codex CLI, without depending on a
  workspace host, and review UI through Cua Driver.
- `conductor`: adopt a Conductor Cloud workspace (`CONDUCTOR_IS_LOCAL=0`) and review UI with
  Playwright headless Chromium inside the VM; phone stories go to the `mobile-web-reviewer`
  agent. See [Conductor Cloud](#conductor-cloud).

The orchestrator never creates a worktree. Start the session in a worktree prepared by Codex,
Superset, or the user's normal Git tooling. Vanilla users must run the repository's local setup
before starting the loop. A new run cleans tracked changes and untracked, non-ignored files before
adoption while preserving ignored `.env`, dependency, and runtime files. Existing runs are never
cleaned when resumed.

## Included skills

- `$issue-delivery-orchestrator`
- `$issue-delivery-grill`
- `$issue-delivery-spec-publisher`
- `$issue-delivery-ticket-publisher`
- `$issue-delivery-implement`
- `$test`
- `$issue-delivery-blocker-triage`
- `$issue-delivery-cua-review`
- `$issue-delivery-browser-review`
- `$issue-delivery-playwright-review`
- `$issue-delivery-mobile-web-review` (also packaged as the Claude Code agent `mobile-web-reviewer`)
- `$issue-delivery-cloud-launch`

The Browser skill, Figma connector, GitHub CLI, Cua Driver, Playwright, and the target repository's
runtime commands remain environment capabilities. Codex Browser review is available only in Codex
mode; Claude Code runs use Cua Driver through Vanilla mode. Linear access is owned entirely by the
bundled CLI through the configured personal API key; the orchestrator does not use the Linear
MCP/app connector. Playwright is required in the target repository only when a story hits a
supported primary-reviewer capability gap; the plugin does not install or add it to product code.

## Skill invocation

Codex exposes plugin skills with `$` names, for example:

```text
$issue-delivery-orchestrator
```

Claude Code namespaces plugin skills under the plugin name, for example:

```text
/issue-delivery-orchestrator-local:issue-delivery-orchestrator
```

References between bundled skills use provider-neutral skill names so the active host can resolve
the correct invocation syntax.

## Blocker checks and local review permission

A request to check blockers reads and classifies existing feedback. It does not authorize repairs,
tests, commits, pushes, comment resolution, or LocalReviewer. Invoking the convergence skill after
that request preserves the inspection scope. Repairs require authorization to resolve blockers or
an authorized delivery whose scope has not been narrowed by the user.

Every LocalReviewer invocation needs explicit permission for its SHA, scope, provider/mode, and
execution count. Permission covers one execution by default. An explicitly approved bounded cycle
can cover multiple executions; do not extend it automatically or ask again for an already approved
execution. Approval of a spec, delivery, PR, repair, or test run does not approve LocalReviewer.
After cancellation, only a new authorization that explicitly names LocalReviewer permits it to run
again. A pending review gate never grants permission to run the reviewer or fabricate a PASS.

When reporting progress, distinguish feedback inspection, focused tests, UI verification, and code
review. An agent running tests is not LocalReviewer.

## Install

### Prerequisites

- Codex CLI with plugin support.
- Git and GitHub CLI (`gh`) authenticated with access to this private repository.
- Python 3.9 or newer.
- Node.js with Corepack/pnpm for the bundled test command and the default TurboShop runtime profile.
- Cua Driver and its operating-system permissions for Superset or Vanilla UI review.
- macOS `sips`, ImageMagick, or ffmpeg when a reviewer returns JPEG bytes under a `.png` filename.

If HTTPS Git credentials are not already configured:

```bash
gh auth login
gh auth setup-git
```

### Install with Codex CLI

Codex and Claude Code share one source directory through the same `agent-kit` marketplace root.
Codex reads its manifest from `agent-kit/.agents/plugins/marketplace.json`, Claude Code from
`agent-kit/.claude-plugin/marketplace.json`, and both declare the plugin at
`./plugins/issue-delivery-orchestrator-local`.

Add the marketplace and install the plugin from it:

```bash
codex plugin marketplace add ~/agent-kit
codex plugin add issue-delivery-orchestrator-local@agent-kit
```

Confirm that Codex can see the installed entry:

```bash
codex plugin list
```

Then start a **new** Codex CLI session so its bundled skills are loaded:

```bash
codex
```

Inside Codex, run `/plugins` to inspect the installation and make sure
`issue-delivery-orchestrator-local` is enabled. The plugin is then available as
`$issue-delivery-orchestrator`.

### Install in the Codex desktop app

Run the marketplace command above once, restart the desktop app, select **Codex**, and open
**Plugins**. Choose the **Agent Kit** marketplace, install the plugin with the plus button, and
start a new chat. Local and repository marketplaces are supported in Codex and
the ChatGPT desktop app, but not in the IDE extension.

### Install in Claude Code

Register this repository's parent as a local marketplace once, then install the plugin so it loads
in every session without a flag:

```bash
claude plugin marketplace add ~/agent-kit
claude plugin install issue-delivery-orchestrator-local@agent-kit
```

Confirm the installation and its component inventory:

```bash
claude plugin list
claude plugin details issue-delivery-orchestrator-local@agent-kit
```

For live iteration on the plugin itself, load the source directory directly instead. This bypasses
the install cache, so edits apply on the next session with no version bump:

```bash
claude --plugin-dir ~/agent-kit/plugins/issue-delivery-orchestrator-local
```

Confirm that `/help` lists skills under the `issue-delivery-orchestrator-local` namespace, then
start a run with:

```text
/issue-delivery-orchestrator-local:issue-delivery-orchestrator SFW-1234
```

Claude Code does not use the Codex Browser reviewer. Start it from the intended prepared checkout
or worktree and let the engine select `vanilla` with Cua Driver. Superset-hosted worktrees continue
to select `superset` normally.

### Update

Both hosts install a plugin by copying an immutable snapshot into their own cache
(`~/.claude/plugins/cache/...` and `~/.codex/plugins/cache/...`). Editing this repository does not
change an installed plugin, and editing the cache is overwritten on the next install. Every change
therefore has to be published and reinstalled.

Increment the version in **both** manifests first, otherwise the installer reuses the cached
bundle:

- `.claude-plugin/plugin.json` → `version`
- `.codex-plugin/plugin.json` → `version` (keep the `+codex.<timestamp>` build suffix)

Then refresh the marketplace snapshot and reinstall.

Claude Code (use `update`, not `install` — `install` short-circuits on an already-installed plugin
and leaves the old cached snapshot in place):

```bash
claude plugin marketplace update agent-kit
claude plugin update issue-delivery-orchestrator-local@agent-kit
```

Codex (`marketplace upgrade` only refreshes **Git** marketplace snapshots, so a local marketplace
needs nothing beyond the version bump and a re-add):

```bash
codex plugin add issue-delivery-orchestrator-local@agent-kit
```

Start a new CLI session or desktop chat afterwards; a running session keeps the skills it loaded at
startup. Claude Code can skip this loop entirely during development by using `--plugin-dir` against
the source directory; Codex has no equivalent flag and always requires the version bump and
reinstall.

Skill runs asked to change the plugin follow the same rule through
[`plugin-maintenance.md`](skills/issue-delivery-orchestrator/references/plugin-maintenance.md):
edit the source repository, never the installed snapshot.

These steps follow the official
[Codex plugin packaging](https://developers.openai.com/plugins/build/plugins) and
[plugin usage](https://learn.chatgpt.com/docs/plugins) guidance.

## Configure

Create the per-user configuration file:

```text
~/.config/issue-delivery-orchestrator/.env
```

At minimum, configure:

```dotenv
LINEAR_API_KEY=lin_api_...
LINEAR_EXPECTED_EMAIL=you@example.com
GITHUB_EXPECTED_LOGIN=your-github-login
ISSUE_DELIVERY_REPOSITORY=/absolute/path/to/repository
LINEAR_COMMENTS_ENABLED=false
```

When `LINEAR_COMMENTS_ENABLED=false`, the local engine still updates the canonical spec, ticket,
and UI evidence blocks but does not create Linear comments. Issue reads, comments, spec
publication, ticket publication, and evidence updates use the personal API key rather than the
Linear MCP/app connector, so a shared OAuth identity cannot participate in the run.

New runs detect their mode from `SUPERSET_WORKSPACE_PATH`, configured worktree roots, or
unambiguous path components such as `.codex` and `superset-worktrees`. Configure custom roots when
your tools use paths without those markers. Separate multiple roots with the operating system path
separator (`:` on macOS/Linux and `;` on Windows):

```dotenv
ISSUE_DELIVERY_CODEX_WORKTREE_ROOTS=/absolute/path/to/codex/worktrees
ISSUE_DELIVERY_SUPERSET_WORKTREE_ROOTS=/absolute/path/to/superset/worktrees
```

An explicit mode always overrides detection. If no Codex or Superset signal matches, the loop
selects Vanilla with `modeSource: vanilla-fallback`. A fallback Vanilla run requires a clean
checkout and blocks before discarding local changes; selecting `modo vanilla` explicitly retains
the normal new-run cleanup contract. Contradictory Codex/Superset signals still require an explicit
choice.

The `.env` is user-owned and must not be committed. On macOS, `LINEAR_API_KEY` can instead live in
Keychain under the configured service and `LINEAR_EXPECTED_EMAIL` account. Existing `gh`
authentication is used for GitHub, and its login must match `GITHUB_EXPECTED_LOGIN`.

From a clone of this repository, inspect the effective non-secret configuration:

```bash
python3 scripts/issue-delivery config
```

The default profile is [`profiles/turboshop.json`](profiles/turboshop.json). Point
`ISSUE_DELIVERY_PROFILE` at another JSON profile to adapt base/target branches, Local Runtime
commands, review bots, evidence branch, and Linear markers without changing the engine.
`review.blockerBots` lists the logins whose general comments carry a `BLOCKERS` section (the
legacy single `review.blockerBot` string is still accepted).
`slicing` sets the PR-size thresholds (`maxProductLines`, `estimateDeviation`, `maxStackDepth`)
that pause a run for a split-or-keep decision; the engine never splits on its own.
TurboShop keeps one PR per issue through 4000 product lines (additions plus deletions).
Only a larger delta can request a split decision; estimate overruns or multiple app owners alone
do not trigger one. Tests and generated artifacts do not count toward this threshold.

## Start a run

Open a new Codex or Claude Code session in the intended product worktree and invoke the
`issue-delivery-orchestrator` skill with a Linear issue. For a host-independent CLI run, start the
agent in the prepared checkout; Vanilla will be selected automatically when no Codex or Superset
signal exists. The skill immediately states the chosen mode, decision source, reviewer, and
worktree in the chat, then preserves that mode for the complete run.

The deterministic engine can also be inspected directly:

```bash
python3 scripts/issue-delivery --help
```

All run memory, receipts, logs, screenshots, and browser profiles are written under the ignored
runtime directory inside the adopted worktree. Product commits contain only product changes.

Final UI evidence uses deterministic numbered callouts to highlight the changed or relevant
regions. The annotated PNG is shown in Linear and GitHub while the untouched original remains
available through an audit link. Global changes can explicitly omit a localized callout.

In Codex mode, Browser remains the primary reviewer. If Browser demonstrably cannot operate a real
file input or activate a required CSS hover state, only the affected story may use the target
repository's existing Playwright installation in headless mode. Upload assistance drives the real
file input; hover assistance uses the real pointer path, verifies `:hover`, captures the accepted
state, moves away, and checks adjacent persistent state.

In Superset and Vanilla modes, Cua Driver remains the primary reviewer. The same bounded Playwright
assistance is available only after Cua demonstrates an equivalent capability gap. Scripts,
fixtures, screenshots, and receipts stay inside the ignored run directory and remain tied to the
same commit and Local Runtime. The evidence gate rejects unsupported kinds, missing primary
attempts, altered artifacts, or stale receipts. Version 0.3 `uploadAssistance` and version 0.4
Codex Browser receipts remain readable for preserved runs.

## Development

```bash
corepack pnpm test
```

The project requires Python 3.9+ and has no runtime Python package dependencies.

## QA Script

New specs carry a `## QA Script`: the approved cases manual revision executes against the running
system (happy paths per user story, edge cases, authorization, failures and real concurrency).
The engine rejects a spec whose script misses a user story's happy path, an applicable risk, or
the committed race test of a concurrency case. Manual revision records
`validation/qa-results.json`, and `publish-qa-results` validates it against the approved script and
posts a `Resultado QA` comment in Linear (and in the PR once it exists) with screenshots and
command output. The `manual-revision` checkpoint requires every case to be `PASS`, or `WAIVED`
with the user's own instruction. Specs approved before this section keep the previous flow. See
`skills/issue-delivery-orchestrator/references/qa-script.md`.

## Conductor Cloud

Issues whose spec and tickets are already approved can run end to end in a Conductor Cloud
workspace. From your machine:

```bash
python3 scripts/issue-delivery LOG-123 --base test cloud-launch --toolbox-profile benjasanti
python3 scripts/issue-delivery LOG-123 --base test cloud-launch --toolbox-profile benjasanti --confirm
```

The first command checks the issue (approved `Spec ID` and `Ticket ID` blocks, state, open
blockers, stack parent) and prints the plan without creating anything. `--confirm` creates the
workspace with a bootstrap session that waits for setup, installs or checks the toolbox and replies
`IDO-BOOTSTRAP READY`. The launcher polls that transcript and then starts a separate delivery
session, because plugins only load when an agent process starts. After a timeout, resume with
`cloud-launch --confirm --workspace <id>`. Only `CODEX_TOOLBOX_PROFILE`,
`LINEAR_EXPECTED_EMAIL`, and `GITHUB_EXPECTED_LOGIN` are passed; configure `LINEAR_API_KEY` as a
Conductor project environment variable, because the VM has no macOS Keychain.
`ISSUE_DELIVERY_TOOLBOX_PROFILE` can replace `--toolbox-profile`.

Inside the workspace the agent prepares the VM following
`skills/issue-delivery-orchestrator/references/conductor-cloud.md` and starts the run with
`--mode conductor`. UI stories are verified with `issue-delivery-playwright-review`; phone stories
use `mobile-web-reviewer`, which emulates 360×800 and 390×844 touch devices, checks that the
on-screen keyboard does not hide focused fields or the primary action, and measures drag and swipe
smoothness. Criteria that only a native device can show are reported as `NATIVE-ONLY`.

The mobile helpers have their own browser test suite:

```bash
PLAYWRIGHT_WORKTREE=/path/to/repo-with-playwright pnpm test:playwright-review
```
