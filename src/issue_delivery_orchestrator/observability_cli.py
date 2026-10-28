"""CLI wiring for metrics and fresh QA; no implicit changes to legacy runs."""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Callable

from . import fresh_qa, metrics
from .cloud_launch import _json_output, _session_transcript, conductor_cli
from .errors import OrchestrationError, RunBlocked
from .linear import upsert_marker_block
from .qa_script import _reject_secrets
from .state import now, run_mode, run_root, save_state
from .usage import conductor_usage
from .util import ensure_within, read_json, run

ACTIONS = {"metrics-start", "metrics-finish", "metrics-usage", "metrics-session",
           "publish-metrics", "qa-session", "qa-register", "qa-wait", "qa-resume", "qa-rebase"}
IDLE_POLL_SECONDS = 30
QA_WAIT_MINUTES = 360


def configure(actions: Any) -> None:
    for name in ("metrics-start", "metrics-finish", "metrics-usage"):
        action = actions.add_parser(name)
        action.add_argument("--unit", required=True)
        action.add_argument("--attempt", type=int, default=1)
        if name == "metrics-start":
            action.add_argument("--kind", choices=("ticket", "phase"), default="ticket")
            action.add_argument("--model", required=True)
            action.add_argument("--effort", required=True)
        elif name == "metrics-finish":
            action.add_argument("--status", choices=metrics.STATUSES, required=True)
            action.add_argument("--receipt", type=Path)
        else:
            action.add_argument("--session", required=True)
            action.add_argument("--transcript", type=Path)
            action.add_argument("--attribution", choices=("dedicated", "shared"), required=True)
    session = actions.add_parser(
        "metrics-session", help="Import a whole Conductor chat's usage as its own metrics row")
    session.add_argument("--session", required=True)
    session.add_argument("--label", required=True)
    session.add_argument("--wait-idle-minutes", type=int, default=0,
                         help="Wait until the chat stops working, so its last turn is reported")
    actions.add_parser("publish-metrics")
    qa = actions.add_parser("qa-session", help="Create a fresh QA chat in the same Conductor workspace")
    qa.add_argument("--workspace", required=True)
    qa.add_argument("--implementation-session", required=True)
    qa.add_argument("--url", action="append", required=True)
    qa.add_argument("--model", help="QA chat model; omitted, QA inherits the calling chat")
    qa.add_argument("--effort", help="QA chat effort; requires --model, defaults to the agent's")
    qa.add_argument("--rerun", action="append", metavar="QA-ID|all",
                    help="After a repair, rerun only these cases and carry the previous PASS ones")
    qa.add_argument("--brief", type=Path,
                    help="Run file with routes, accounts and seeded data for the QA reviewer")
    qa.add_argument("--replace", action="store_true",
                    help="Replace a stopped QA of this commit, keeping the cases it settled")
    rebased = actions.add_parser(
        "qa-rebase", help="Re-pin a finished QA to HEAD after a rebase that kept the branch's patch")
    rebased.add_argument("--old-base", help="Base of the QA commit's patch, for runs that did not record it")
    rebased.add_argument("--rerun", action="append", metavar="QA-ID",
                         help="Case the new base can affect; it reruns with qa-session --replace")
    awaken = actions.add_parser("qa-resume", help="Resume the registered QA chat after it stopped")
    awaken.add_argument("--note", type=Path, help="Run file with what the controller restored")
    wait = actions.add_parser("qa-wait", help="Block until the registered QA chat stops working")
    wait.add_argument("--timeout-minutes", type=int, default=QA_WAIT_MINUTES)
    native = actions.add_parser("qa-register", help="Register a fresh native-host QA conversation receipt")
    native.add_argument("--receipt", type=Path, required=True)


def _artifact(state: dict[str, Any], path: Path | None) -> Any:
    if path is None:
        return None
    root = run_root(Path(state["worktree"]), state["runId"])
    resolved = ensure_within(path if path.is_absolute() else Path(state["worktree"]) / path, root)
    if not resolved.is_file():
        raise OrchestrationError("Observability receipt is missing from the run")
    try:
        return read_json(resolved)
    except (ValueError, OSError) as error:
        raise OrchestrationError("Observability receipt is not readable JSON") from error


def _session_status(cli: str, session_id: str) -> str | None:
    return _json_output(run([cli, "session", "status", session_id, "--json"]).stdout).get("status")


def publish(state: dict[str, Any], linear_factory: Callable, configuration: Any) -> dict[str, Any]:
    body = metrics.report(state)
    _reject_secrets("metrics", body)
    linear, _ = linear_factory()
    issue = linear.issue(state["issue"]["identifier"])
    marker = f"Métricas de entrega — {state['runId']}"
    if configuration.linear_comments_enabled:
        target = {"commentId": linear.upsert_comment(issue.id, marker, body)}
    else:
        linear.update_description(issue.id, upsert_marker_block(
            issue.description, marker_prefix=configuration.linear_marker_prefix,
            block="metrics", body=body))
        target = {"descriptionBlock": "metrics"}
    state["metricsPublication"] = {**target, "publishedAt": now()}
    save_state(state)
    return {"published": True, **target}


def _publish_safely(state: dict[str, Any], linear_factory: Callable, configuration: Any) -> dict[str, Any]:
    try:
        return publish(state, linear_factory, configuration)
    except OrchestrationError as error:
        # Measurements remain durable; a reporting outage must not discard a completed ticket.
        return {"published": False, "publicationError": str(error),
                "retry": "publish-metrics"}


def wait_for_qa(state: dict[str, Any], timeout_minutes: int) -> dict[str, Any]:
    review = (state.get("qaSession") or {}).get("reviewSessionId")
    if not review:
        raise RunBlocked("No QA conversation is registered; run qa-session first")
    cli = conductor_cli()
    # One blocking call replaces the implementer's polling turns; it costs no model tokens while waiting.
    deadline = time.monotonic() + timeout_minutes * 60
    while (status := _session_status(cli, review)) == "working":
        if time.monotonic() > deadline:
            raise RunBlocked("The QA chat is still working; run qa-wait again")
        time.sleep(IDLE_POLL_SECONDS)
    # Idle is not done: a usage limit or a VM restart also leaves the reviewer idle mid-script.
    return {"reviewSessionId": review, "status": status, **fresh_qa.outcome(state, cli)}


def dispatch(state: dict[str, Any], args: argparse.Namespace, linear_factory: Callable,
             configuration: Any) -> dict[str, Any]:
    if not state.get("observabilityVersion"):
        raise RunBlocked("Observability is enabled only for newly created runs")
    if args.action == "metrics-start":
        return metrics.start(state, args.unit, args.kind, args.attempt, args.model, args.effort)
    if args.action == "metrics-finish":
        item = metrics.finish(state, args.unit, args.attempt, args.status, _artifact(state, args.receipt))
        return {"metric": item, "linear": _publish_safely(state, linear_factory, configuration)}
    if args.action == "metrics-usage":
        if args.transcript:
            transcript = _artifact(state, args.transcript)
            usage = conductor_usage(transcript, args.session)
        else:
            try:
                usage = conductor_usage(_session_transcript(conductor_cli(), args.session), args.session)
            except OrchestrationError:
                usage = {"source": "conductor-sdk", "sessionId": args.session,
                         "availability": "unavailable", "requests": {},
                         "reason": "Transcript could not be read completely"}
        item = metrics.attach_usage(state, args.unit, args.attempt, usage, args.attribution)
        return {"metric": item, "linear": _publish_safely(state, linear_factory, configuration)}
    if args.action == "metrics-session":
        cli = conductor_cli()
        deadline = time.monotonic() + args.wait_idle_minutes * 60
        while args.wait_idle_minutes and _session_status(cli, args.session) == "working":
            if time.monotonic() > deadline:
                raise RunBlocked("The chat is still working; import its usage later")
            time.sleep(IDLE_POLL_SECONDS)
        try:
            usage = conductor_usage(_session_transcript(cli, args.session), args.session)
        except OrchestrationError:
            usage = {"sessionId": args.session, "requests": {}, "availability": "unavailable"}
        item = metrics.record_session(state, args.label, usage)
        return {"metric": item, "linear": _publish_safely(state, linear_factory, configuration)}
    if args.action == "publish-metrics":
        return publish(state, linear_factory, configuration)
    if args.action == "qa-session":
        return fresh_qa.launch(state, args.workspace, args.implementation_session,
                               args.url, args.model, args.effort, args.rerun, args.brief,
                               args.replace)
    if args.action == "qa-resume":
        return fresh_qa.resume(state, args.note)
    if args.action == "qa-rebase":
        return fresh_qa.rebase(state, old_base=args.old_base, rerun=args.rerun)
    if args.action == "qa-wait":
        return wait_for_qa(state, args.timeout_minutes)
    if run_mode(state) == "conductor":
        raise RunBlocked("Use qa-session to create and verify the actual Conductor QA chat")
    receipt = _artifact(state, args.receipt)
    if not isinstance(receipt, dict) or receipt.get("freshContext") is not True or (
        receipt.get("host") != run_mode(state)
    ):
        raise RunBlocked("Native QA requires the host's fresh-conversation receipt")
    return fresh_qa.register(state, receipt.get("implementationSessionId"),
                             receipt.get("reviewSessionId"), host_receipt=str(args.receipt))


def phase_completed(state: dict[str, Any], phase: str, started: dict[str, str] | None,
                    receipt_path: Path | None, linear_factory: Callable,
                    configuration: Any) -> dict[str, Any] | None:
    if not state.get("observabilityVersion"):
        return None
    unit = f"phase-{phase}"
    previous = [r for r in metrics.records(state) if r["unit"] == unit]
    attempt = max((r["attempt"] for r in previous), default=0) + 1
    try:
        if not started:
            raise OrchestrationError("Phase start was not recorded; duration is unavailable")
        from .util import atomic_write_json
        atomic_write_json(metrics.record_path(state, unit, attempt), {
            "unit": unit, "kind": "phase", "attempt": attempt, **started,
            "model": "N/D", "effort": "N/D", "usage": None, "status": "RUNNING"})
        metric = metrics.finish(state, unit, attempt, "PASS", _artifact(state, receipt_path))
        result = {"metric": metric, "linear": _publish_safely(state, linear_factory, configuration)}
    except OrchestrationError as error:
        result = {"measurementError": str(error)}
    # The product phase already passed; maintain the next boundary even if reporting failed.
    from .util import run
    state["phaseMeasurement"] = {"startedAt": now(), "startCommit": run(
        ["git", "rev-parse", "HEAD"], cwd=Path(state["worktree"])).stdout.strip()}
    save_state(state)
    return result
