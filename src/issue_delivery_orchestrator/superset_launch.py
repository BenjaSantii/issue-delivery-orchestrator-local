from __future__ import annotations

import json
import os
import shutil
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
)
DEFAULT_AGENT = "claude"
DEFAULT_MODEL = "opus"
ONLINE_STATES = {"yes", "local"}


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
    implement_model: str | None = None,
) -> dict[str, Any]:
    workspace_base = stack_parent or base
    if not _remote_branch_exists(repository, workspace_base):
        raise RunBlocked(f"Branch {workspace_base} does not exist on origin")
    model = model or DEFAULT_MODEL
    message = superset_launch_message(
        readiness, base=base, stack_parent=stack_parent, implement_model=implement_model
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
        "--prompt",
        message,
        "--json",
    ]
    return {
        "arguments": arguments,
        "message": message,
        "agent": agent,
        "model": model,
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
) -> str:
    identifier = readiness["identifier"]
    flags = "--mode superset " + (
        f"--stack-parent {stack_parent}" if stack_parent else f"--base {base}"
    )
    if implement_model:
        flags += f" --implement-model {implement_model}"
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
        "teléfono.\n"
        "5. Pausa sólo ante tickets HITL, decisiones de tamaño, permisos de LocalReviewer o "
        "bloqueos reales; explica la acción mínima que necesitas.\n"
    )


def create_superset_workspace(cli: str, launch: dict[str, Any]) -> dict[str, Any]:
    result = run([cli, *launch["arguments"]])
    created = _json_output(result.stdout)
    workspace = created.get("workspace")
    return workspace if isinstance(workspace, dict) else created
