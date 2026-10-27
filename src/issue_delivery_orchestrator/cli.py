from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .cloud_launch import (
    build_launch,
    conductor_cli,
    conductor_project_id,
    RESUME_AGENT,
    RESUME_MODEL,
    create_delivery_session,
    create_resume_session,
    create_workspace,
    find_issue_workspace,
    resume_message,
    working_sessions,
    delivery_readiness,
    drop_merged_blockers,
    first_session_id,
    stack_parent_for,
    wait_for_bootstrap,
    workspace_id_from,
)
from .config import DEFAULT_CONFIG_HOME, Settings, settings
from .credentials import CredentialProvider
from .errors import IdentityMismatch, OrchestrationError, RunBlocked
from .evidence import prepare_evidence, publish_evidence, repair_github_evidence
from .git_workspace import GitWorkspace
from .run_evidence import ui_manifest, validation_dir
from .github import GitHubClient
from .spec_lint import assert_spec_publishable
from .slicing import assert_size_accepted, record_decision, record_estimate, size_check
from .stack import (
    link_stack,
    pr_target,
    require_parent_pr,
    restack,
    slice_extract,
    stack_cut,
)
from .pr_quality import (
    assert_pre_review_current,
    assert_validation_current,
    publish_validation,
    record_pre_review,
)
from .qa_script import (
    approved_qa_cases,
    qa_comment_body,
    qa_github_comment_body,
    qa_comment_marker,
    validate_qa_results,
)
from .linear import (
    LinearClient,
    extract_marker_block,
    linear_markdown_equivalent,
    normalize_issue_identifier,
    upsert_marker_block,
)
from .review import (
    acknowledge_processed_blocker,
    assert_review_converged,
    publish_skip_summary,
    wait_for_quiet_review,
)
from .runtime import (
    cleanup_runtimes,
    initialize_runtime,
    launch_browser,
    register_owned_process,
    stop_owned_processes,
)
from .state import (
    DEVELOPMENT_MODES,
    PHASES,
    REVIEW_METHODS,
    block_run,
    complete_phase,
    create_state,
    find_runs,
    new_run_id,
    resumable_run,
    review_method,
    resume_run,
    run_mode,
    run_root,
    save_state,
    select_review_method,
    now,
)
from .util import ensure_within, run
from . import observability_cli


def parser() -> argparse.ArgumentParser:
    configuration = settings()
    result = argparse.ArgumentParser(
        description="Portable, resumable issue delivery orchestrator"
    )
    result.add_argument("issue", help="Linear issue ID or URL")
    result.add_argument(
        "--base",
        default=configuration.default_base_branch,
        help="Base branch for a new issue branch",
    )
    result.add_argument("--new-run", action="store_true", help="Create a distinct run")
    result.add_argument("--run-id", help="Select an existing run explicitly")
    result.add_argument(
        "--mode",
        choices=DEVELOPMENT_MODES,
        help="Development workspace mode for a new run",
    )
    result.add_argument(
        "--stack-parent",
        metavar="BRANCH",
        help="Start a new run as a stack child on top of this slice branch",
    )
    result.add_argument("--qa-model", help="Conductor model for this run's fresh QA chat")
    result.add_argument("--qa-effort", help="Effort for --qa-model; defaults to the agent's")
    result.add_argument(
        "--reviewer",
        choices=REVIEW_METHODS,
        help=argparse.SUPPRESS,
    )
    result.add_argument(
        "--worktree",
        type=Path,
        help="Existing checkout or worktree to adopt for this run",
    )
    actions = result.add_subparsers(dest="action")

    actions.add_parser(
        "read-linear-issue",
        help="Read the live Linear issue and all comments with the personal API key",
    )
    actions.add_parser("status")
    cloud = actions.add_parser(
        "cloud-launch",
        help="Check that the issue has approved spec/tickets and start it in Conductor Cloud",
    )
    cloud.add_argument(
        "--toolbox-profile",
        default=os.environ.get("ISSUE_DELIVERY_TOOLBOX_PROFILE", ""),
        help="software-skills profile the cloud workspace installs",
    )
    cloud.add_argument("--agent", default="claude", choices=("claude", "codex"))
    cloud.add_argument("--model", help="Model for the Conductor sessions (default per agent)")
    cloud.add_argument("--conductor-project", help="Conductor project ID")
    cloud.add_argument(
        "--confirm",
        action="store_true",
        help="Create the workspace; without it the command only prints the plan",
    )
    resume_cloud = actions.add_parser(
        "cloud-resume",
        help="Start a fresh session in the issue's Conductor workspace that resumes its run",
    )
    resume_cloud.add_argument("--workspace", help="Conductor workspace ID")
    resume_cloud.add_argument("--agent", default=RESUME_AGENT, choices=("claude", "codex"))
    resume_cloud.add_argument("--model", default=RESUME_MODEL)
    resume_cloud.add_argument(
        "--confirm",
        action="store_true",
        help="Create the session; without it the command only prints the plan",
    )
    cloud.add_argument(
        "--workspace",
        help="Resume a launch: wait for this workspace's bootstrap and start delivery",
    )
    cloud.add_argument(
        "--wait-minutes",
        type=float,
        default=25,
        help="How long to wait for the bootstrap session before giving up",
    )

    reviewer = actions.add_parser(
        "reviewer-select",
        help="Legacy runs only; new runs fix the reviewer through --mode",
    )
    reviewer.add_argument("--method", required=True, choices=REVIEW_METHODS)

    checkpoint = actions.add_parser("checkpoint")
    checkpoint.add_argument("--phase", required=True, choices=PHASES)
    checkpoint.add_argument("--artifact", action="append", default=[], metavar="KEY=PATH")
    checkpoint.add_argument("--metrics-receipt", type=Path)
    observability_cli.configure(actions)

    blocked = actions.add_parser("block")
    blocked.add_argument("--reason", required=True)
    blocked.add_argument("--decision", action="store_true")

    resume = actions.add_parser("resume")
    resume.add_argument("--phase", choices=PHASES)

    runtime = actions.add_parser("runtime-init")
    runtime.add_argument("--fresh", action="store_true")

    process = actions.add_parser("register-process")
    process.add_argument("--pid", type=int, required=True)
    process.add_argument("--kind", required=True)
    process.add_argument("--command", default="")

    browser = actions.add_parser("launch-browser")
    browser.add_argument("--url", required=True)

    actions.add_parser("stop-processes")

    pre_review = actions.add_parser("record-pre-review")
    pre_review.add_argument("--receipt", type=Path, required=True)

    ensure_pr = actions.add_parser("ensure-pr")
    ensure_pr.add_argument("--body-file", type=Path, required=True)
    ensure_pr.add_argument("--title")

    linear_block = actions.add_parser("publish-linear-block")
    linear_block.add_argument("--block", required=True, choices=("spec", "tickets"))
    linear_block.add_argument("--body-file", type=Path, required=True)

    qa = actions.add_parser(
        "publish-qa-results",
        help="Validate QA results against the approved QA Script and publish them",
    )
    qa.add_argument("--results", type=Path, required=True)

    publish_run_parser = actions.add_parser(
        "publish-run",
        help="Publish the run's validation folder: UI evidence, QA results and PR validation",
    )
    publish_run_parser.add_argument("--runtime")

    evidence = actions.add_parser("publish-evidence")
    evidence.add_argument("--manifest", type=Path, required=True)

    prepare = actions.add_parser("prepare-evidence")
    prepare.add_argument("--manifest", type=Path, required=True)

    actions.add_parser("repair-evidence-links")

    review = actions.add_parser("wait-review")
    review.add_argument("--quiet-seconds", type=int, default=configuration.quiet_seconds)
    review.add_argument("--max-seconds", type=int, default=configuration.maximum_wait_seconds)
    review.add_argument("--poll-seconds", type=int, default=configuration.poll_seconds)

    acknowledgement = actions.add_parser("acknowledge-blocker")
    acknowledgement.add_argument("--comment-id", type=int, required=True)
    acknowledgement.add_argument("--decision", choices=("FIX", "SKIP"), required=True)

    estimate = actions.add_parser("size-estimate")
    estimate.add_argument("--lines", type=int, required=True)

    actions.add_parser("size-check")

    size_decision = actions.add_parser("size-decision")
    size_decision.add_argument("--decision", choices=("single-pr", "split"), required=True)
    size_decision.add_argument("--reason", required=True)

    link = actions.add_parser("stack-link")
    link.add_argument("--parent-branch", required=True)
    link.add_argument("--tip")

    cut = actions.add_parser("stack-cut")
    cut.add_argument("--sha", required=True)
    cut.add_argument("--branch", required=True)

    extract = actions.add_parser("slice-extract")
    extract.add_argument("--commit", action="append", required=True, dest="commits")
    extract.add_argument("--branch", required=True)

    actions.add_parser("restack")

    sub_issue = actions.add_parser("create-sub-issue")
    sub_issue.add_argument("--title", required=True)
    sub_issue.add_argument("--body-file", type=Path, required=True)
    sub_issue.add_argument(
        "--assignee",
        help="Linear user ID, me, or none; otherwise use the profile's app-specific default",
    )
    sub_issue.add_argument(
        "--relation",
        choices=("none", "blocks-current", "blocked-by-current"),
        default="none",
        help="blocks-current for a lower stack slice; blocked-by-current for a higher one",
    )

    validation = actions.add_parser("publish-validation")
    validation.add_argument("--receipt", type=Path, required=True)

    skip_summary = actions.add_parser("publish-skip-summary")
    skip_summary.add_argument("--input", type=Path, required=True)

    actions.add_parser("review-gate")

    cleanup = actions.add_parser("cleanup")
    cleanup.add_argument("--force", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv == ["config"]:
        print(json.dumps(settings().public_dict(), indent=2, sort_keys=True))
        return 0
    args = parser().parse_args(raw_argv)
    try:
        payload = dispatch(args)
    except (IdentityMismatch, RunBlocked, OrchestrationError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "read-linear-issue":
        return read_linear_issue(args.issue)
    if args.action == "cloud-launch":
        return cloud_launch(args)
    if args.action == "cloud-resume":
        return cloud_resume(args)

    configuration = settings()
    repository = _repository(args.worktree, configuration.repository)
    worktrees = Path(
        os.environ.get(
            "ISSUE_DELIVERY_WORKTREES",
            str(DEFAULT_CONFIG_HOME / "worktrees"),
        )
    ).expanduser().resolve()
    workspace = GitWorkspace(repository)
    registered_worktrees = workspace.paths()
    if args.action is None:
        return bootstrap(
            args,
            repository,
            worktrees,
            workspace,
            registered_worktrees,
        )

    state = _select_state(
        args.issue,
        worktrees,
        args.run_id,
        registered_worktrees,
    )
    if args.action == "status":
        return _public_state(state)
    if args.action in observability_cli.ACTIONS:
        return observability_cli.dispatch(state, args, _verified_linear, configuration)
    if args.action == "reviewer-select":
        selection = select_review_method(state, args.method)
        return {"reviewer": selection, "state": _public_state(state)}
    if args.action == "checkpoint":
        artifacts = _parse_artifacts(args.artifact, Path(state["worktree"]))
        if args.phase == "manual-revision":
            manifest = artifacts.get("ui-manifest")
            if not manifest:
                raise RunBlocked(
                    "Manual revision requires the ui-manifest artifact"
                )
            prepare_evidence(
                state,
                Path(state["worktree"]) / manifest,
            )
            _require_published_qa(state, artifacts.get("qa-results"))
        if args.phase in {"pr-creation", "review-convergence"}:
            assert_validation_current(state)
        if args.phase == "review-convergence":
            assert_review_converged(state)
        measurement = state.get("phaseMeasurement")
        complete_phase(state, args.phase, artifacts)
        metric = observability_cli.phase_completed(
            state, args.phase, measurement, args.metrics_receipt, _verified_linear, configuration)
        stopped: list[int] = []
        if state["status"] == "completed_preserved":
            stopped = stop_owned_processes(state)
        return {"state": _public_state(state), "stoppedPids": stopped, "metrics": metric}
    if args.action == "block":
        stopped = stop_owned_processes(state)
        block_run(state, args.reason, args.decision)
        return {"state": _public_state(state), "stoppedPids": stopped}
    if args.action == "resume":
        if args.phase:
            state["currentPhase"] = args.phase
        elif state.get("currentPhase") is None:
            state["currentPhase"] = "review-convergence"
        resume_run(state)
        return _public_state(state)
    if args.action == "runtime-init":
        return initialize_runtime(state, fresh=args.fresh)
    if args.action == "register-process":
        register_owned_process(
            state,
            args.pid,
            kind=args.kind,
            command=args.command,
        )
        return _public_state(state)
    if args.action == "launch-browser":
        if review_method(state) == "playwright":
            raise RunBlocked(
                "Dedicated Chrome is disabled in conductor mode; "
                "verify the UI with issue-delivery-playwright-review"
            )
        if review_method(state) != "cua-driver":
            raise RunBlocked(
                "Dedicated Chrome is disabled in codex mode; "
                "resume manual revision in the Codex app with Browser available"
            )
        return launch_browser(state, args.url)
    if args.action == "stop-processes":
        return {"stoppedPids": stop_owned_processes(state), "state": _public_state(state)}
    if args.action == "size-estimate":
        return {"size": record_estimate(state, args.lines)}
    if args.action == "size-check":
        return size_check(state)
    if args.action == "size-decision":
        return record_decision(state, args.decision, args.reason)
    if args.action == "stack-link":
        return {"stack": link_stack(state, args.parent_branch, parent_tip=args.tip)}
    if args.action == "stack-cut":
        return {"cut": stack_cut(state, args.sha, args.branch)}
    if args.action == "slice-extract":
        return {"extraction": slice_extract(state, args.commits, args.branch)}
    if args.action == "restack":
        github = GitHubClient(Path(state["worktree"]))
        github.verify_identity()
        return restack(state, github)
    if args.action == "create-sub-issue":
        return create_sub_issue(state, args.title, args.body_file, args.relation, args.assignee)
    if args.action == "record-pre-review":
        return {"preReview": record_pre_review(state, args.receipt)}
    if args.action == "publish-validation":
        github = GitHubClient(Path(state["worktree"]))
        github.verify_identity()
        return {"validationBlock": publish_validation(state, args.receipt, github)}
    if args.action == "ensure-pr":
        return ensure_pull_request(state, args.body_file, args.title)
    if args.action == "publish-linear-block":
        return publish_linear_block(state, args.block, args.body_file)
    if args.action == "publish-qa-results":
        return publish_qa_results(state, args.results)
    if args.action == "publish-run":
        return publish_run(state, args.runtime)
    if args.action == "publish-evidence":
        linear, _ = _verified_linear()
        github = GitHubClient(Path(state["worktree"]))
        github.verify_identity()
        return publish_evidence(
            state,
            _resolve_in_worktree(args.manifest, Path(state["worktree"])),
            linear=linear,
            github=github if state.get("pr") else None,
            comments_enabled=configuration.linear_comments_enabled,
        )
    if args.action == "prepare-evidence":
        return prepare_evidence(
            state,
            _resolve_in_worktree(args.manifest, Path(state["worktree"])),
        )
    if args.action == "repair-evidence-links":
        github = GitHubClient(Path(state["worktree"]))
        github.verify_identity()
        return repair_github_evidence(state, github=github)
    if args.action == "wait-review":
        if not state.get("pr"):
            raise RunBlocked("Cannot wait for review before a PR has been recorded")
        if not 0 < args.quiet_seconds <= args.max_seconds:
            raise OrchestrationError("quiet-seconds must be positive and no greater than max-seconds")
        return wait_for_quiet_review(
            state,
            quiet_seconds=args.quiet_seconds,
            max_seconds=args.max_seconds,
            poll_seconds=args.poll_seconds,
        )
    if args.action == "acknowledge-blocker":
        if not state.get("pr"):
            raise RunBlocked("Cannot acknowledge a blocker before a PR has been recorded")
        return acknowledge_processed_blocker(
            state,
            comment_id=args.comment_id,
            decision=args.decision,
        )
    if args.action == "publish-skip-summary":
        return publish_skip_summary(
            state,
            input_path=_resolve_in_worktree(args.input, Path(state["worktree"])),
        )
    if args.action == "review-gate":
        return assert_review_converged(state)
    if args.action == "cleanup":
        return final_cleanup(state, force=args.force)
    raise OrchestrationError(f"Unknown action: {args.action}")


def read_linear_issue(identifier_or_url: str) -> dict[str, Any]:
    linear, credential_source = _verified_linear()
    issue = linear.issue(identifier_or_url)
    comments = linear.issue_comments(issue.id)
    return {
        "credentialSource": credential_source,
        "issue": asdict(issue),
        "comments": [asdict(comment) for comment in comments],
    }


def cloud_resume(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDUCTOR_IS_LOCAL", "").strip() == "0":
        raise RunBlocked("cloud-resume runs from your machine, not inside a cloud workspace")
    identifier = normalize_issue_identifier(args.issue)
    cli = conductor_cli()
    workspace_id = args.workspace or find_issue_workspace(cli, identifier)["id"]
    busy = working_sessions(cli, workspace_id)
    if busy:
        names = ", ".join(f"{item['name']} ({item['id']})" for item in busy)
        raise RunBlocked(
            f"Workspace {workspace_id} still has working sessions: {names}. Cancel them with "
            "conductor session cancel before resuming, so two agents do not share the worktree."
        )
    message = resume_message(identifier)
    plan = {
        "workspaceId": workspace_id,
        "agent": args.agent,
        "model": args.model,
        "message": message,
        "created": False,
    }
    if not args.confirm:
        return plan
    session = create_resume_session(
        cli, workspace_id, message, agent=args.agent, model=args.model
    )
    return {**plan, "created": True, "session": session}


def cloud_launch(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDUCTOR_IS_LOCAL", "").strip() == "0":
        raise RunBlocked("cloud-launch runs from your machine, not inside a cloud workspace")
    if not args.toolbox_profile:
        raise RunBlocked(
            "Set ISSUE_DELIVERY_TOOLBOX_PROFILE or pass --toolbox-profile so the cloud "
            "workspace installs this plugin"
        )
    configuration = settings()
    # The launcher targets the configured product repository, not whichever workspace
    # (for example a Superset session) the command happens to run from.
    repository = _repository(configuration.repository, None)
    linear, credential_source = _verified_linear()
    readiness = delivery_readiness(linear, args.issue, configuration.linear_marker_prefix)
    if readiness["problems"]:
        raise RunBlocked("; ".join(readiness["problems"]))
    if not args.stack_parent:
        drop_merged_blockers(readiness, repository, configuration.pr_target_branch)
    stack_parent = stack_parent_for(readiness, args.stack_parent)
    cli = conductor_cli()
    project_id = args.conductor_project or conductor_project_id(cli, repository)
    launch = build_launch(
        readiness,
        configuration,
        repository,
        base=args.base,
        stack_parent=stack_parent,
        agent=args.agent,
        model=args.model,
        toolbox_profile=args.toolbox_profile,
        project_id=project_id,
        qa_model=args.qa_model,
        qa_effort=args.qa_effort,
    )
    plan = {
        "credentialSource": credential_source,
        "readiness": readiness,
        "stackParent": stack_parent,
        "qa": launch["qa"],
        "conductor": cli,
        "command": [cli, *launch["arguments"]],
        "bootstrapMessage": launch["bootstrapMessage"],
        "message": launch["message"],
        "workspaceBase": launch["workspaceBase"],
        "workspaceBaseBehindPrTarget": launch["behindTarget"],
        "created": False,
    }
    if not args.confirm:
        return plan

    # Plugins load when an agent process starts. The first session installs and checks
    # the toolbox; delivery runs in a second session started after it reports READY.
    created = None
    if args.workspace:
        workspace_id = args.workspace
    else:
        created = create_workspace(cli, launch)
        workspace_id = workspace_id_from(created)
    bootstrap_session = first_session_id(cli, workspace_id, created)
    outcome = wait_for_bootstrap(
        cli,
        bootstrap_session,
        timeout_seconds=args.wait_minutes * 60,
    )
    resume = (
        f"Resume with: issue-delivery {readiness['identifier']} cloud-launch "
        f"--confirm --workspace {workspace_id}"
    )
    if outcome is None:
        raise RunBlocked(
            f"Workspace {workspace_id} did not finish its bootstrap within "
            f"{args.wait_minutes:g} minutes. {resume}"
        )
    if outcome[0] == "FAILED":
        raise RunBlocked(
            f"Workspace {workspace_id} bootstrap failed: {outcome[1] or 'no cause given'}. "
            f"Fix it in that workspace, then: {resume}"
        )
    delivery = create_delivery_session(cli, workspace_id, launch)
    return {
        **plan,
        "created": True,
        "workspaceId": workspace_id,
        "bootstrapSessionId": bootstrap_session,
        "deliverySession": delivery,
    }


def bootstrap(
    args: argparse.Namespace,
    repository: Path,
    worktrees: Path,
    workspace: GitWorkspace,
    registered_worktrees: tuple[Path, ...],
) -> dict[str, Any]:
    linear, credential_source = _verified_linear()
    issue = linear.issue(args.issue)
    configuration = settings()
    github = GitHubClient(repository)
    github_login = github.verify_identity()

    requested_worktree = _requested_worktree(args.worktree, args.mode)
    previous = resumable_run(worktrees, issue.identifier, registered_worktrees)
    if previous and not args.new_run:
        if args.mode and args.mode != run_mode(previous):
            raise RunBlocked(
                f"Run {previous['runId']} already uses {run_mode(previous)} mode; "
                "development mode cannot change after worktree creation"
            )
        if args.reviewer and args.reviewer != review_method(previous):
            raise RunBlocked(
                f"Run {previous['runId']} already uses {review_method(previous)}; "
                "use reviewer-select before its first manual revision to change it"
            )
        return {
            "resumed": True,
            "credentialSource": credential_source,
            "modeSource": "persisted-run",
            "state": _public_state(previous),
            "modeDecision": _mode_decision(previous, "persisted-run"),
            "nextAction": _next_action(previous),
        }
    if (
        previous
        and requested_worktree
        and Path(previous["worktree"]).resolve() == requested_worktree.expanduser().resolve()
    ):
        raise RunBlocked(
            f"Worktree {requested_worktree} already contains resumable run "
            f"{previous['runId']}; resume it instead of using --new-run"
        )

    if args.reviewer:
        raise RunBlocked(
            "New runs select their UI reviewer through --mode; "
            "codex uses codex-browser; superset and vanilla use cua-driver; "
            "conductor uses playwright"
        )
    if not requested_worktree:
        mode_hint = (
            f"{args.mode} mode"
            if args.mode
            else "Automatic mode detection"
        )
        raise RunBlocked(
            f"{mode_hint} requires --worktree with the checkout or worktree "
            "that the loop must adopt"
        )
    mode, mode_source = _new_run_mode(
        args.mode,
        requested_worktree,
        configuration,
    )

    if args.stack_parent:
        args.base = args.stack_parent
    run_id = new_run_id()
    workspace.fetch(args.base)
    if mode == "codex":
        worktree = workspace.adopt_codex(
            requested_worktree,
            issue.branch_name,
            args.base,
            issue.identifier,
        )
    elif mode == "conductor":
        worktree = workspace.adopt_conductor(
            requested_worktree,
            issue.branch_name,
            args.base,
            issue.identifier,
        )
    elif mode == "vanilla":
        worktree = workspace.adopt_vanilla(
            requested_worktree,
            issue.branch_name,
            args.base,
            issue.identifier,
            allow_discard=mode_source != "vanilla-fallback",
        )
    else:
        worktree = workspace.adopt(
            requested_worktree,
            issue.branch_name,
            issue.identifier,
        )
    state = create_state(
        worktree=worktree.path,
        run_id=run_id,
        issue=asdict(issue),
        branch=issue.branch_name,
        base=args.base,
        created_from=worktree.created_from,
        adopted_head=worktree.adopted_head,
        adopted_status=worktree.adopted_status,
        discarded_status=worktree.discarded_status,
        identities={
            "linear": configuration.linear_expected_email,
            "github": github_login,
        },
        mode=mode,
        profile=configuration.public_dict(),
    )
    if args.stack_parent:
        link_stack(state, args.stack_parent)
    if args.qa_model:
        state["qaConfiguration"] = {"model": args.qa_model, "effort": args.qa_effort or ""}
        save_state(state)
    try:
        linear_state = linear.start_issue(issue.id)
    except OrchestrationError as error:
        # The run already exists; a status outage must not block the Grill.
        linear_state = {"changed": False, "error": str(error)}
    return {
        "resumed": False,
        "credentialSource": credential_source,
        "linearState": linear_state,
        "modeSource": mode_source,
        "state": _public_state(state),
        "modeDecision": _mode_decision(state, mode_source),
        "nextAction": _next_action(state),
    }


def ensure_pull_request(
    state: dict[str, Any],
    body_file: Path,
    title: str | None,
) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    body_file = _resolve_in_worktree(body_file, worktree)
    if not body_file.is_file():
        raise OrchestrationError(f"PR body file not found: {body_file}")
    linear, _ = _verified_linear()
    github = GitHubClient(worktree)
    github.verify_identity()
    branch = run(["git", "branch", "--show-current"], cwd=worktree).stdout.strip()
    if branch != state["branch"]:
        raise RunBlocked(f"Expected branch {state['branch']}, got {branch}")

    target = pr_target(state)
    github = GitHubClient(worktree, pr_target=target)
    run(["git", "push", "-u", "origin", branch], cwd=worktree)
    open_prs = github.find(branch, target, "open")
    created = False
    if open_prs:
        pr = open_prs[0]
    else:
        previous = github.find(branch, target, "all")
        closed = [item for item in previous if item.state != "OPEN"]
        merged = next((item for item in closed if item.merged_at), None)
        if merged:
            block_run(
                state,
                f"PR {merged.url} is already merged; use a follow-up issue and branch",
                decision=False,
            )
            raise RunBlocked(state["blocker"]["reason"])
        if closed:
            block_run(
                state,
                f"PR {closed[0].url} is closed without merge and requires a user decision",
                decision=True,
            )
            raise RunBlocked(state["blocker"]["reason"])
        require_parent_pr(state, github)
        assert_size_accepted(state)
        assert_pre_review_current(state)
        pr = github.create(
            branch,
            title or f"{state['issue']['identifier']}: {state['issue']['title']}",
            body_file,
        )
        created = True

    if pr.is_draft:
        run(["gh", "pr", "ready", pr.url], cwd=worktree)
        pr = github.view(pr.url)
    if pr.base != target or pr.head != branch:
        raise RunBlocked(
            f"PR routing mismatch: expected {branch} -> {target}, "
            f"got {pr.head} -> {pr.base}"
        )
    state["pr"] = asdict(pr)
    save_state(state)
    if created and settings().linear_comments_enabled:
        linear.post_comment(
            state["issue"]["id"],
            f"Pull request lista hacia `{target}`: {pr.url}",
        )
    return {"created": created, "pr": asdict(pr), "state": _public_state(state)}


def create_sub_issue(
    state: dict[str, Any],
    title: str,
    body_file: Path,
    relation: str,
    assignee: str | None = None,
) -> dict[str, Any]:
    body_path = _resolve_in_worktree(body_file, Path(state["worktree"]))
    if not body_path.is_file():
        raise OrchestrationError(f"Sub-issue body file not found: {body_path}")
    body = body_path.read_text().strip()
    if not body or not title.strip():
        raise OrchestrationError("A sub-issue needs a title and a non-empty description")
    linear, _ = _verified_linear()
    current = linear.issue(state["issue"]["identifier"])
    sub = linear.create_sub_issue(
        current.identifier,
        title.strip(),
        body,
        assignee=assignee,
        self_assignment_labels=settings().linear_self_assignment_labels,
    )
    if relation == "blocks-current":
        linear.add_blocks_relation(sub.id, current.id)
    elif relation == "blocked-by-current":
        linear.add_blocks_relation(current.id, sub.id)
    return {
        "issue": sub.identifier,
        "branch": sub.branch_name,
        "url": sub.url,
        "relation": relation,
    }


def publish_linear_block(
    state: dict[str, Any],
    block: str,
    body_file: Path,
) -> dict[str, Any]:
    configuration = settings()
    worktree = Path(state["worktree"])
    body_path = _resolve_in_worktree(body_file, worktree)
    if not body_path.is_file():
        raise OrchestrationError(f"Linear block body file not found: {body_path}")
    body = body_path.read_text().strip()
    if not body:
        raise OrchestrationError(f"Linear {block} block body must not be empty")
    if block == "spec":
        assert_spec_publishable(body)

    linear, _ = _verified_linear()
    issue = linear.issue(state["issue"]["identifier"])
    description = upsert_marker_block(
        issue.description,
        marker_prefix=configuration.linear_marker_prefix,
        block=block,
        body=body,
    )
    if block == "spec" and f"<!-- {configuration.linear_marker_prefix}:tickets:start -->" not in description:
        description = upsert_marker_block(
            description,
            marker_prefix=configuration.linear_marker_prefix,
            block="tickets",
            body="<!-- Tickets pending. Run $issue-delivery-ticket-publisher after approving the breakdown. -->",
        )
    linear.update_description(issue.id, description)

    verified = linear.issue(state["issue"]["identifier"])
    verified_body = extract_marker_block(
        verified.description,
        marker_prefix=configuration.linear_marker_prefix,
        block=block,
    )
    if not linear_markdown_equivalent(body, verified_body):
        raise OrchestrationError(f"Linear {block} block verification failed")
    if configuration.linear_comments_enabled:
        linear.post_comment(
            issue.id,
            f"Bloque `{block}` actualizado por Issue Delivery Orchestrator.",
        )
    return {
        "issue": verified.identifier,
        "block": block,
        "commentsEnabled": configuration.linear_comments_enabled,
        "updated": True,
    }


def publish_qa_results(state: dict[str, Any], results_file: Path) -> dict[str, Any]:
    configuration = settings()
    worktree = Path(state["worktree"])
    results = validate_qa_results(state, _resolve_in_worktree(results_file, worktree))
    if results is None:
        raise RunBlocked("The approved spec has no QA Script; there are no QA results to publish")

    linear, _ = _verified_linear()
    issue = linear.issue(state["issue"]["identifier"])
    asset_urls = {
        screenshot: linear.upload_file(worktree / screenshot)
        for case in results["cases"]
        for screenshot in case["screenshots"]
    }
    body = qa_comment_body(state["runId"], results, asset_urls, worktree)
    if configuration.linear_comments_enabled:
        linear_target = {
            "commentId": linear.upsert_comment(issue.id, qa_comment_marker(state["runId"]), body)
        }
    else:
        linear.update_description(
            issue.id,
            upsert_marker_block(
                issue.description,
                marker_prefix=configuration.linear_marker_prefix,
                block="qa-results",
                body=body,
            ),
        )
        linear_target = {"descriptionBlock": "qa-results"}

    github_target = None
    if state.get("pr"):
        github = GitHubClient(worktree)
        github.verify_identity()
        marker = f"<!-- issue-delivery-qa:{state['runId']} -->"
        github_body = qa_github_comment_body(state["runId"], results, worktree, issue.url)
        github_target = {
            "commentId": github.upsert_comment(
                int(state["pr"]["number"]), marker, f"{marker}\n{github_body}"
            )
        }

    state["qa"] = {
        "verifiedCommit": results["verifiedCommit"],
        "publishedAt": now(),
        "cases": {case["id"]: case["status"] for case in results["cases"]},
        "linear": linear_target,
        "github": github_target,
    }
    save_state(state)
    return {"published": True, "qa": state["qa"]}


def publish_run(state: dict[str, Any], runtime_id: str | None) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    folder = validation_dir(state)
    result: dict[str, Any] = {"uiManifest": None, "uiImages": 0, "qa": None, "validation": None}
    manifest = ui_manifest(state, runtime_id)
    if manifest:
        linear, _ = _verified_linear()
        github = None
        if state.get("pr"):
            github = GitHubClient(worktree)
            github.verify_identity()
        receipt = publish_evidence(
            state,
            manifest,
            linear=linear,
            github=github,
            comments_enabled=settings().linear_comments_enabled,
        )
        result["uiManifest"] = str(manifest.resolve().relative_to(worktree.resolve()))
        result["uiImages"] = len(receipt["assets"])
        result["linearIssue"] = receipt["linearIssue"]
    qa_results = folder / "qa-results.json"
    if qa_results.is_file() and approved_qa_cases(state) is not None:
        result["qa"] = publish_qa_results(state, qa_results)["qa"]["cases"]
    validation = folder / "pr-validation.md"
    if validation.is_file() and state.get("pr"):
        github = GitHubClient(worktree)
        github.verify_identity()
        result["validation"] = publish_validation(state, validation, github)["sha"]
    return result


def _require_published_qa(state: dict[str, Any], results_artifact: str | None) -> None:
    if approved_qa_cases(state) is None:
        if state.get("observabilityVersion"):
            raise RunBlocked("New runs require an approved QA Script and fresh QA results")
        return
    if not results_artifact:
        raise RunBlocked("The approved spec has a QA Script; manual revision requires qa-results")
    results = validate_qa_results(state, Path(state["worktree"]) / results_artifact)
    published = (state.get("qa") or {}).get("verifiedCommit")
    if published != results["verifiedCommit"]:
        raise RunBlocked(
            "Publish the QA results for the current commit with publish-qa-results "
            "before completing manual revision"
        )


def final_cleanup(state: dict[str, Any], *, force: bool) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    github = GitHubClient(worktree)
    github.verify_identity()
    if not force:
        if not state.get("pr"):
            raise RunBlocked("Final cleanup requires a recorded merged PR; use --force to override")
        pr = github.view(state["pr"]["url"])
        if not pr.merged_at:
            raise RunBlocked(f"PR {pr.url} is not merged; refusing final cleanup")
    stopped = stop_owned_processes(state)
    cleaned = cleanup_runtimes(state)
    profile = run_root(worktree, state["runId"]) / "browser-profile"
    if profile.exists():
        shutil.rmtree(profile)
    state["status"] = "cleaned"
    state["cleanedAt"] = now()
    save_state(state)
    return {
        "stoppedPids": stopped,
        "cleanedRuntimes": cleaned,
        "worktreePreserved": str(worktree),
        "branchPreserved": state["branch"],
    }


def _verified_linear() -> tuple[LinearClient, str]:
    expected_email = settings().linear_expected_email
    if not expected_email:
        raise IdentityMismatch(
            "LINEAR_EXPECTED_EMAIL must be configured before Linear access"
        )
    secret = CredentialProvider().linear_api_key()
    client = LinearClient(secret.value)
    viewer = client.viewer()
    if viewer.email != expected_email:
        raise IdentityMismatch(
            f"Linear identity mismatch: expected {expected_email}, "
            f"got {viewer.email or '<empty>'}"
        )
    return client, secret.source


def _repository(explicit_worktree: Path | None, configured: Path | None) -> Path:
    candidates = [
        explicit_worktree,
        Path(os.environ["SUPERSET_WORKSPACE_PATH"])
        if os.environ.get("SUPERSET_WORKSPACE_PATH")
        else None,
        configured,
        Path.cwd(),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        path = candidate.expanduser().resolve()
        if not path.is_dir():
            continue
        result = run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    raise RunBlocked(
        "Could not locate the target Git repository. Run from a repository worktree "
        "or set ISSUE_DELIVERY_REPOSITORY."
    )


def _select_state(
    issue: str,
    worktrees: Path,
    run_id: str | None,
    registered_worktrees: tuple[Path, ...] = (),
) -> dict[str, Any]:
    identifier = normalize_issue_identifier(issue)
    candidates = find_runs(worktrees, identifier, registered_worktrees)
    if run_id:
        candidates = [state for state in candidates if state.get("runId") == run_id]
    if not candidates:
        raise RunBlocked(f"No orchestration run found for {identifier}")
    return candidates[0]


def _requested_worktree(explicit: Path | None, mode: str | None) -> Path | None:
    if explicit:
        return explicit
    if mode in {"codex", "vanilla", "conductor"}:
        return None
    raw = os.environ.get("SUPERSET_WORKSPACE_PATH")
    return Path(raw) if raw else None


def _new_run_mode(
    explicit: str | None,
    worktree: Path,
    configuration: Settings,
) -> tuple[str, str]:
    if explicit:
        return explicit, "explicit"

    if os.environ.get("CONDUCTOR_IS_LOCAL", "").strip() == "0":
        return "conductor", "conductor-cloud-environment"

    candidate = worktree.expanduser().resolve()
    superset_workspace = os.environ.get("SUPERSET_WORKSPACE_PATH", "").strip()
    if superset_workspace and candidate == Path(superset_workspace).expanduser().resolve():
        return "superset", "superset-environment"

    configured_matches = _configured_mode_matches(candidate, configuration)
    if len(configured_matches) > 1:
        raise RunBlocked(
            "Worktree matches both configured Codex and Superset roots. "
            "Fix ISSUE_DELIVERY_*_WORKTREE_ROOTS or pass --mode explicitly."
        )
    if configured_matches:
        return configured_matches.pop(), "configured-root"

    marker_matches = _path_mode_markers(candidate)
    if len(marker_matches) > 1:
        raise RunBlocked(
            f"Worktree path contains both Codex and Superset markers: {candidate}. "
            "Pass --mode explicitly."
        )
    if marker_matches:
        return marker_matches.pop(), "path-marker"

    return "vanilla", "vanilla-fallback"


def _configured_mode_matches(
    worktree: Path,
    configuration: Settings,
) -> set[str]:
    matches: set[str] = set()
    roots = {
        "codex": configuration.codex_worktree_roots,
        "superset": configuration.superset_worktree_roots,
    }
    for mode, configured_roots in roots.items():
        for raw_root in configured_roots:
            root = Path(raw_root).expanduser().resolve()
            if worktree == root or root in worktree.parents:
                matches.add(mode)
    return matches


def _path_mode_markers(path: Path) -> set[str]:
    matches: set[str] = set()
    for part in path.parts:
        tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", part.casefold())
            if token
        }
        if "codex" in tokens:
            matches.add("codex")
        if "superset" in tokens:
            matches.add("superset")
    return matches


def _resolve_in_worktree(path: Path, worktree: Path) -> Path:
    candidate = path if path.is_absolute() else worktree / path
    return ensure_within(candidate, worktree)


def _parse_artifacts(items: list[str], worktree: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for item in items:
        key, separator, raw_path = item.partition("=")
        if not separator or not key.strip() or not raw_path.strip():
            raise OrchestrationError(f"Expected artifact KEY=PATH, got: {item}")
        path = _resolve_in_worktree(Path(raw_path), worktree)
        if not path.exists():
            raise OrchestrationError(f"Artifact does not exist: {path}")
        artifacts[key.strip()] = str(path.relative_to(worktree))
    return artifacts


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": state["runId"],
        "issue": state["issue"]["identifier"],
        "title": state["issue"]["title"],
        "branch": state["branch"],
        "base": state["base"],
        "worktree": state["worktree"],
        "workspaceMode": (
            "adopted"
            if str(state.get("createdFrom", "")).startswith(
                ("adopted:", "codex:", "vanilla:", "conductor:")
            )
            else "private"
        ),
        "developmentMode": run_mode(state),
        "adoptedStatus": state.get("adoptedStatus", []),
        "discardedInitialStatus": state.get("discardedInitialStatus", []),
        "status": state["status"],
        "currentPhase": state.get("currentPhase"),
        "reviewerMethod": review_method(state),
        "observabilityVersion": state.get("observabilityVersion"),
        "qaSession": state.get("qaSession"),
        "pr": state.get("pr"),
        "prTarget": pr_target(state),
        "stack": state.get("stack"),
        "size": (state.get("size") or {}).get("lastCheck"),
        "blocker": state.get("blocker"),
        "runtimes": [item["runtimeId"] for item in state.get("runtimes", [])],
    }


def _mode_decision(
    state: dict[str, Any],
    source: str,
) -> dict[str, str]:
    return {
        "mode": run_mode(state),
        "source": source,
        "reviewer": review_method(state),
        "worktree": str(Path(state["worktree"]).resolve()),
    }


def _next_action(state: dict[str, Any]) -> str:
    if state["status"] == "needs_user_decision":
        return "Ask the user to resolve the recorded decision gate."
    if state["status"] == "blocked":
        return "Resolve the recorded blocker, then run the resume action."
    if state["status"] == "completed_preserved":
        return "Wait for explicit human-review work or final cleanup after merge."
    if state.get("currentPhase") == "manual-revision" and state.get("observabilityVersion"):
        return (
            "Start/register a fresh QA conversation for this candidate using fresh-qa.md; "
            "reuse the runtime and verify database consequences before publishing its results."
        )
    if (
        state.get("currentPhase") == "manual-revision"
        and run_mode(state) == "codex"
    ):
        return (
            "Resume this worktree and run in the Codex app with the Browser plugin; "
            "do not launch dedicated Chrome."
        )
    if (
        state.get("currentPhase") == "manual-revision"
        and run_mode(state) == "vanilla"
    ):
        return (
            "Launch the run-owned dedicated browser and verify the UI with "
            "Cua Driver from this checkout."
        )
    if (
        state.get("currentPhase") == "manual-revision"
        and run_mode(state) == "conductor"
    ):
        return (
            "Verify the UI with issue-delivery-playwright-review from this "
            "Conductor Cloud checkout; use mobile-web-reviewer for phone stories."
        )
    return f"Execute phase {state.get('currentPhase')} from the worktree."


if __name__ == "__main__":
    raise SystemExit(main())
