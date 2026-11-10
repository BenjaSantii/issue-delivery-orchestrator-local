from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .cloud_launch import (
    PLUGIN_ID,
    _commits_behind,
    _json_output,
    _normalize_remote,
    _remote_branch_exists,
)
from .config import Settings
from .errors import RunBlocked
from .util import run


SUPERSET_CLI_PATHS = (
    Path.home() / ".paradev" / "bin" / "superset",
    Path.home() / ".superset" / "bin" / "superset",
    Path.home() / "superset" / "bin" / "superset",
)
DEFAULT_AGENT = "claude"
DEFAULT_MODEL = "opus"
ONLINE_STATES = {"yes", "local"}
CLAUDE_MODEL = re.compile(r"^(haiku|sonnet|opus|fable)(\[1m\])?$|^claude-[a-z0-9.-]+(\[1m\])?$")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TRUST_PROMPT = "Yes, I trust this folder"
STARTED_STATES = {"working", "idle", "blocked"}


def superset_cli() -> str:
    for candidate in (
        os.environ.get("SUPERSET_CLI", "").strip(),
        shutil.which("superset") or "",
        *(str(path) for path in SUPERSET_CLI_PATHS),
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RunBlocked("Superset CLI not found. Install Superset or set SUPERSET_CLI.")


def _listing(cli: str, *arguments: str) -> list[dict[str, Any]]:
    value = json.loads(run([cli, *arguments, "--json"]).stdout)
    if isinstance(value, dict):
        value = value.get("data", [])
    return [item for item in value if isinstance(item, dict)]


def superset_host(cli: str, wanted: str) -> dict[str, Any]:
    if not wanted:
        raise RunBlocked(
            "Pass --host or set ISSUE_DELIVERY_SUPERSET_HOST with the Superset host "
            "(machine ID or name) that runs the delivery"
        )
    hosts = _listing(cli, "hosts", "list")
    matches = [
        host
        for host in hosts
        if wanted in (str(host.get("id") or ""), str(host.get("name") or ""))
    ]
    if len(matches) != 1:
        known = ", ".join(f"{host.get('name')} ({host.get('id')})" for host in hosts)
        raise RunBlocked(f"Expected one Superset host named {wanted}, found {len(matches)}: {known}")
    host = matches[0]
    if str(host.get("online") or "").lower() not in ONLINE_STATES:
        raise RunBlocked(f"Superset host {host.get('name')} ({host.get('id')}) is offline")
    return host


def superset_project_id(cli: str, repository: Path) -> str:
    remote = run(["git", "remote", "get-url", "origin"], cwd=repository).stdout.strip()
    wanted = _normalize_remote(remote)
    matches = [
        project
        for project in _listing(cli, "projects", "list")
        if _normalize_remote(str(project.get("repo") or "")) == wanted
    ]
    if len(matches) != 1:
        raise RunBlocked(
            f"Expected exactly one Superset project for {remote}, found {len(matches)}. "
            "Pass --superset-project."
        )
    return str(matches[0]["id"])


def existing_issue_workspace(cli: str, host_id: str, branch: str) -> dict[str, Any] | None:
    for workspace in _listing(cli, "workspaces", "list", "--host", host_id):
        if workspace.get("branch") == branch and not workspace.get("archivedAt"):
            return workspace
    return None


def build_superset_launch(
    readiness: dict[str, Any],
    configuration: Settings,
    repository: Path,
    *,
    host_id: str,
    project_id: str,
    base: str,
    stack_parent: str | None,
    agent: str,
    model: str | None,
    effort: str | None = None,
    implement_model: str | None = None,
    qa_agent: str | None = None,
    qa_model: str | None = None,
    qa_effort: str | None = None,
) -> dict[str, Any]:
    if qa_effort and not qa_model:
        raise RunBlocked("--qa-effort requires --qa-model")
    workspace_base = stack_parent or base
    if not _remote_branch_exists(repository, workspace_base):
        raise RunBlocked(f"Branch {workspace_base} does not exist on origin")
    model = model or DEFAULT_MODEL
    validate_model(model, effort, "Delivery")
    validate_model(qa_model, qa_effort, "QA")
    qa = {"agent": qa_agent or agent, "model": qa_model or "", "effort": qa_effort or ""}
    message = superset_launch_message(
        readiness, base=base, stack_parent=stack_parent, implement_model=implement_model, qa=qa
    )
    name = f"[{readiness['identifier']}] {readiness['title']}"
    # The Linear branch already carries the user's prefix, so Superset must not add its own.
    arguments = [
        "workspaces",
        "create",
        "--host",
        host_id,
        "--project",
        project_id,
        "--name",
        name[:80],
        "--branch",
        readiness["branch"],
        "--skip-branch-prefix",
        "--base-branch",
        workspace_base,
        "--agent",
        agent,
        "--model",
        model,
        *(["--effort", effort] if effort else []),
        "--prompt",
        message,
        "--json",
    ]
    return {
        "arguments": arguments,
        "message": message,
        "agent": agent,
        "model": model,
        "effort": effort or "",
        "qa": qa,
        "implementModel": implement_model,
        "workspaceBase": workspace_base,
        "behindTarget": _commits_behind(
            repository, workspace_base, configuration.pr_target_branch
        ),
    }


def superset_launch_message(
    readiness: dict[str, Any],
    *,
    base: str,
    stack_parent: str | None,
    implement_model: str | None = None,
    qa: dict[str, str] | None = None,
) -> str:
    identifier = readiness["identifier"]
    flags = "--mode superset " + (
        f"--stack-parent {stack_parent}" if stack_parent else f"--base {base}"
    )
    if implement_model:
        flags += f" --implement-model {implement_model}"
    if qa and qa.get("agent"):
        flags += f" --qa-agent {qa['agent']}"
    if qa and qa.get("model"):
        flags += f" --qa-model {qa['model']}" + (f" --qa-effort {qa['effort']}" if qa.get("effort") else "")
    tickets = ", ".join(readiness["ticketIds"])
    return (
        f"Entrega {identifier} ({readiness['title']}) con Issue Delivery Orchestrator en este "
        "workspace de Superset.\n\n"
        f"1. Si las skills del plugin `{PLUGIN_ID}` no aparecen en esta sesión, detente y dilo: "
        "no las sustituyas leyendo archivos a mano.\n"
        "2. Inicia el orquestador (Claude Code: "
        "`/issue-delivery-orchestrator-local:issue-delivery-orchestrator`; Codex: "
        f"`$issue-delivery-orchestrator`) para {identifier} con `--worktree` igual a la raíz de "
        f"este checkout y `{flags}`.\n"
        f"3. El usuario ya aprobó el spec {readiness['specId']} y los tickets {tickets}, "
        "publicados en Linear desde otra máquina. Reconcilia esos bloques con el repositorio; si "
        "no hay diferencias, registra la aprobación vigente y continúa a Implement sin repetir el "
        "Grill. Si la reconciliación cambia el alcance, pide aprobación antes de seguir.\n"
        "4. Usa el reviewer que decida el motor: en un host sin pantalla es "
        "`issue-delivery-playwright-review`, con `mobile-web-reviewer` para las historias de "
        "teléfono. El QA corre en otro chat de este workspace: créalo con `qa-session` (toma "
        "agente y modelo del run), espéralo con `qa-wait` en segundo plano y, si se detiene, "
        "retómalo con `qa-resume` o `qa-session --replace`. No crees sesiones de QA a mano.\n"
        "5. Pausa sólo ante tickets HITL, decisiones de tamaño o bloqueos reales; explica la "
        "acción mínima que necesitas. LocalReviewer es opcional: no lo ejecutes ni pidas permiso "
        "para correrlo.\n"
    )


def create_superset_workspace(cli: str, launch: dict[str, Any]) -> dict[str, Any]:
    return _json_output(run([cli, *launch["arguments"]]).stdout)


def validate_model(model: str | None, effort: str | None, subject: str) -> None:
    """Superset passes these through to the agent CLI unchecked, so a typo would only fail at launch."""
    if model and not CLAUDE_MODEL.match(model):
        raise RunBlocked(
            f"{subject} model {model} is not a Claude Code model: use haiku, sonnet, opus, fable "
            "or a claude-* ID"
        )
    if effort and effort not in EFFORTS:
        raise RunBlocked(f"{subject} effort {effort} is not one of {', '.join(EFFORTS)}")


def superset_agent_id(cli: str, location: list[str], wanted: str) -> str:
    """Accept a preset (claude), a host agent config UUID or its label (Claude dev4)."""
    agents = _listing(cli, "agents", "list", *location)
    for agent in agents:
        if wanted == str(agent.get("id") or ""):
            return wanted
    labelled = [agent for agent in agents if wanted == str(agent.get("label") or "")]
    if len(labelled) == 1:
        return str(labelled[0]["id"])
    if any(wanted == str(agent.get("presetId") or "") for agent in agents):
        return wanted
    known = ", ".join(str(agent.get("label")) for agent in agents)
    raise RunBlocked(f"No agent {wanted} on this Superset host. Known agents: {known}")


def agent_state(cli: str, location: list[str], workspace: str, terminal: str) -> str | None:
    explained = _json_output(run([cli, "agents", "explain", *location, "--workspace", workspace,
                                  "--terminal", terminal, "--json"], check=False).stdout)
    return explained.get("state")


def agent_transcript(cli: str, location: list[str], workspace: str, terminal: str) -> str:
    read = _json_output(run([cli, "agents", "read", *location, "--workspace", workspace,
                             "--terminal", terminal, "--json"], check=False).stdout)
    return str(read.get("text") or "")


def accept_folder_trust(
    cli: str, location: list[str], workspace: str, terminal: str, *, timeout_seconds: float = 180,
    poll_seconds: float = 3,
) -> dict[str, Any]:
    """Claude Code asks to trust each new folder and ignores its first prompt until someone answers."""
    accepted = False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = agent_state(cli, location, workspace, terminal)
        if state in STARTED_STATES:
            return {"state": state, "trustAccepted": accepted}
        screen = _json_output(run([cli, "terminals", "read", *location, "--workspace", workspace,
                                   "--terminal", terminal, "--json"], check=False).stdout)
        if TRUST_PROMPT in str(screen.get("text") or ""):
            run([cli, "terminals", "send", *location, "--workspace", workspace,
                 "--terminal", terminal, "--keys", "down,enter"])
            accepted = True
        time.sleep(poll_seconds)
    raise RunBlocked(
        f"The agent in terminal {terminal} did not start within {timeout_seconds:g} seconds; "
        "open the workspace and check its terminal"
    )


def launched_terminal(created: dict[str, Any]) -> str | None:
    for key in ("agents", "terminals"):
        for item in created.get(key) or []:
            if isinstance(item, dict):
                value = item.get("sessionId") or item.get("terminalId") or item.get("id")
                if value:
                    return str(value)
    value = created.get("sessionId") or created.get("terminalId")
    return str(value) if value else None
