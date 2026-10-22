from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .errors import OrchestrationError, RunBlocked
from .linear import LinearClient, extract_marker_block, normalize_issue_identifier
from .util import run


CONDUCTOR_APP_CLI = Path("/Applications/Conductor.app/Contents/Resources/bin/conductor")
CLOSED_STATES = {"completed", "canceled"}
AGENTS = ("claude", "codex")
PLUGIN_ID = "issue-delivery-orchestrator-local"
MINIMUM_PLUGIN_VERSION = "0.12.1"
BOOTSTRAP_MARKER = re.compile(r"(?m)^\s*`?IDO-BOOTSTRAP (READY|FAILED)(?::\s*(.*?))?`?\s*$")

READINESS_QUERY = """
query CloudLaunchReadiness($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    url
    branchName
    description
    state { name type }
    parent { identifier branchName state { type } }
    inverseRelations(first: 50) {
      nodes {
        type
        issue { identifier title branchName state { name type } }
      }
    }
  }
}
"""


def delivery_readiness(
    client: LinearClient,
    identifier_or_url: str,
    marker_prefix: str,
) -> dict[str, Any]:
    identifier = normalize_issue_identifier(identifier_or_url)
    issue = client._graphql(READINESS_QUERY, {"id": identifier}).get("issue")
    if not issue:
        raise OrchestrationError(f"Linear issue not found: {identifier_or_url}")
    description = issue.get("description") or ""
    problems: list[str] = []

    state = issue.get("state") or {}
    if state.get("type") in CLOSED_STATES:
        problems.append(f"{identifier} is {state.get('name')}; there is nothing to deliver")
    if not (issue.get("branchName") or "").strip():
        problems.append(f"{identifier} has no Linear git branch name")

    spec = _block(description, marker_prefix, "spec")
    tickets = _block(description, marker_prefix, "tickets")
    if not spec or not re.search(r"(?m)^Spec ID:\s*\S", spec):
        problems.append("No approved spec block (Spec ID) in the Linear description")
    if not tickets or not re.search(r"(?m)^Ticket ID:\s*\S", tickets):
        problems.append("No approved tickets block (Ticket ID) in the Linear description")

    blockers = [
        node["issue"]
        for node in (issue.get("inverseRelations") or {}).get("nodes", [])
        if node.get("type") == "blocks"
        and (node.get("issue") or {}).get("state", {}).get("type") not in CLOSED_STATES
    ]
    return {
        "identifier": issue["identifier"],
        "title": issue["title"],
        "url": issue.get("url") or "",
        "branch": (issue.get("branchName") or "").strip(),
        "state": state.get("name"),
        "specId": _first_value(spec, "Spec ID"),
        "ticketIds": re.findall(r"(?m)^Ticket ID:\s*(\S+)", tickets or ""),
        "parent": issue.get("parent"),
        "openBlockers": [
            {
                "identifier": blocker["identifier"],
                "title": blocker["title"],
                "branch": blocker.get("branchName") or "",
                "state": (blocker.get("state") or {}).get("name"),
            }
            for blocker in blockers
        ],
        "problems": problems,
    }


def stack_parent_for(readiness: dict[str, Any], explicit: str | None) -> str | None:
    """Return the branch a blocked slice must stack on, or raise when it cannot start."""
    if explicit:
        return explicit
    blockers = readiness["openBlockers"]
    if not blockers:
        return None
    parent = readiness.get("parent") or {}
    if len(blockers) == 1 and blockers[0]["identifier"] == parent.get("identifier"):
        return blockers[0]["branch"]
    names = ", ".join(f"{item['identifier']} ({item['state']})" for item in blockers)
    raise RunBlocked(
        f"{readiness['identifier']} is blocked by {names}. Deliver the blockers first or "
        "pass --stack-parent with the branch to stack on."
    )


def drop_merged_blockers(
    readiness: dict[str, Any],
    repository: Path,
    target: str,
) -> list[dict[str, Any]]:
    """Remove blockers whose branch already has a PR merged into the PR target.

    Linear often keeps a delivered issue open until release, but once its PR is in the
    target branch the dependent slice must start from that branch, not stack on it.
    """
    merged = []
    still_open = []
    for blocker in readiness["openBlockers"]:
        branch = blocker.get("branch") or ""
        if branch and _pr_merged_into(repository, branch, target):
            merged.append(blocker)
        else:
            still_open.append(blocker)
    readiness["openBlockers"] = still_open
    readiness["mergedBlockers"] = merged
    return merged


def _pr_merged_into(repository: Path, branch: str, target: str) -> bool:
    result = run(
        ["gh", "pr", "list", "--head", branch, "--base", target, "--state", "merged",
         "--json", "number", "--limit", "1"],
        cwd=repository,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        return bool(json.loads(result.stdout or "[]"))
    except json.JSONDecodeError:
        return False


def build_launch(
    readiness: dict[str, Any],
    configuration: Settings,
    repository: Path,
    *,
    base: str,
    stack_parent: str | None,
    agent: str,
    model: str | None,
    toolbox_profile: str,
    project_id: str,
) -> dict[str, Any]:
    if agent not in AGENTS:
        raise OrchestrationError(f"Unsupported Conductor agent {agent}; use {', '.join(AGENTS)}")
    if not configuration.linear_expected_email or not configuration.github_expected_login:
        raise RunBlocked(
            "LINEAR_EXPECTED_EMAIL and GITHUB_EXPECTED_LOGIN must be configured; the cloud "
            "toolbox install fails without them"
        )
    workspace_base = stack_parent or base
    if not _remote_branch_exists(repository, workspace_base):
        raise RunBlocked(f"Branch {workspace_base} does not exist on origin")

    message = launch_message(readiness, base=base, stack_parent=stack_parent)
    name = f"[{readiness['identifier']}] {readiness['title']}"
    arguments = [
        "workspace",
        "create",
        "--project-id",
        project_id,
        "--branch",
        workspace_base,
        "--name",
        name[:80],
        "--agent",
        agent,
    ]
    if model:
        arguments += ["--model", model]
    # Only non-secret identity values travel here; LINEAR_API_KEY comes from the
    # Conductor project environment.
    arguments += [
        "--env",
        f"CODEX_TOOLBOX_PROFILE={toolbox_profile}",
        "--env",
        f"LINEAR_EXPECTED_EMAIL={configuration.linear_expected_email}",
        "--env",
        f"GITHUB_EXPECTED_LOGIN={configuration.github_expected_login}",
        "--message-file",
        "-",
        "--json",
    ]
    return {
        "arguments": arguments,
        "bootstrapMessage": bootstrap_message(agent, toolbox_profile),
        "message": message,
        "agent": agent,
        "model": model,
        "workspaceBase": workspace_base,
        "behindTarget": _commits_behind(
            repository,
            workspace_base,
            configuration.pr_target_branch,
        ),
    }


def bootstrap_message(agent: str, toolbox_profile: str) -> str:
    # Plugins load when the agent process starts, so the delivery session must start
    # after the toolbox is installed. This first session only prepares the workspace.
    plugin_list = f"{agent} plugin list" + (" --json" if agent == "claude" else "")
    return (
        "Prepara este workspace de Conductor Cloud para Issue Delivery Orchestrator. En esta "
        "sesión no trabajes ninguna issue: sólo prepara el entorno y responde.\n\n"
        # The bracket keeps pgrep from matching the shell that runs the wait loop itself.
        "1. Si el setup del workspace sigue corriendo (`pgrep -f '[s]etup-workspace\\.sh'`; los "
        "corchetes evitan que pgrep encuentre al propio shell que espera), espera a que termine "
        "revisando cada 15 segundos, hasta 10 minutos. No uses el patrón sin corchetes.\n"
        f"2. Ejecuta `{plugin_list}` y confirma que `{PLUGIN_ID}` esté habilitado con versión "
        f"{MINIMUM_PLUGIN_VERSION} o mayor. Si falta o es menor, ejecuta desde la raíz del repo "
        f"`CODEX_TOOLBOX_PROFILE={toolbox_profile} pnpm conductor:toolbox` y vuelve a "
        "comprobarlo.\n"
        "3. Comprueba, sin imprimir sus valores, que existan `LINEAR_API_KEY`, "
        "`LINEAR_EXPECTED_EMAIL` y `GITHUB_EXPECTED_LOGIN` (`test -n \"$VAR\"`).\n"
        "4. Termina tu respuesta con una sola línea final, exactamente una de estas:\n"
        "IDO-BOOTSTRAP READY\n"
        "IDO-BOOTSTRAP FAILED: <causa concreta>\n\n"
        "Otra sesión de este workspace continuará con la entrega cuando respondas READY.\n"
    )


def launch_message(
    readiness: dict[str, Any],
    *,
    base: str,
    stack_parent: str | None,
) -> str:
    identifier = readiness["identifier"]
    command = f"`--mode conductor --base {base}`"
    if stack_parent:
        command = f"`--mode conductor --stack-parent {stack_parent}`"
    tickets = ", ".join(readiness["ticketIds"])
    return (
        f"Entrega {identifier} ({readiness['title']}) con Issue Delivery Orchestrator en "
        "Conductor Cloud.\n\n"
        "1. Una sesión anterior de este workspace ya instaló y verificó el plugin "
        f"`{PLUGIN_ID}`. Si aun así sus skills no aparecen en esta sesión, detente y dilo: no "
        "las sustituyas leyendo archivos a mano. Prepara el workspace siguiendo "
        "`<plugin-root>/skills/issue-delivery-orchestrator/references/conductor-cloud.md`.\n"
        "2. Inicia el orquestador (Claude Code: "
        "`/issue-delivery-orchestrator-local:issue-delivery-orchestrator`; Codex: "
        f"`$issue-delivery-orchestrator`) para {identifier} con `--worktree` igual a la raíz de "
        f"este checkout y {command}.\n"
        f"3. El usuario ya aprobó el spec {readiness['specId']} y los tickets {tickets}, "
        "publicados en Linear. Reconcilia esos bloques con el repositorio; si no hay "
        "diferencias, registra la aprobación vigente y continúa a Implement sin repetir el "
        "Grill. Si la reconciliación cambia el alcance, pide aprobación antes de seguir.\n"
        "4. Verifica las historias UI con `issue-delivery-playwright-review` y las de teléfono "
        "con el agente `mobile-web-reviewer`.\n"
        "5. En Conductor no ejecutes ni esperes LocalReviewer: no es requisito previo a la PR. "
        "Mantén QA web, paridad CI y convergencia de reviews de GitHub. Pausa sólo ante tickets "
        "HITL, decisiones de tamaño o bloqueos reales; explica la acción mínima que necesitas.\n"
    )


def conductor_cli() -> str:
    for candidate in (
        os.environ.get("CONDUCTOR_CLI", "").strip(),
        shutil.which("conductor") or "",
        str(CONDUCTOR_APP_CLI),
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RunBlocked(
        "Conductor CLI not found. Install the Conductor app or set CONDUCTOR_CLI."
    )


def conductor_project_id(cli: str, repository: Path) -> str:
    remote = run(["git", "remote", "get-url", "origin"], cwd=repository).stdout.strip()
    wanted = _normalize_remote(remote)
    listing = json.loads(run([cli, "project", "list", "--json", "--limit", "200"]).stdout)
    projects = listing.get("data", listing) if isinstance(listing, dict) else listing
    matches = [
        project
        for project in projects
        if _normalize_remote(str(project.get("gitRemote") or project.get("repoUrl") or ""))
        == wanted
    ]
    if len(matches) != 1:
        raise RunBlocked(
            f"Expected exactly one Conductor project for {remote}, found {len(matches)}. "
            "Pass --conductor-project."
        )
    return str(matches[0]["id"])


def create_workspace(cli: str, launch: dict[str, Any]) -> dict[str, Any]:
    result = run([cli, *launch["arguments"]], input_text=launch["bootstrapMessage"])
    return _json_output(result.stdout)


DELIVERY_SESSION_NAME = "Generador"


def workspace_sessions(cli: str, workspace_id: str) -> list[dict[str, Any]]:
    return _json_output(
        run([cli, "workspace", "session", workspace_id, "--json"]).stdout
    ).get("data") or []


def first_session_id(cli: str, workspace_id: str, created: dict[str, Any] | None = None) -> str:
    for value in _find_values(created or {}, ("sessionId",)):
        return value
    sessions = workspace_sessions(cli, workspace_id)
    if not sessions:
        raise RunBlocked(f"Conductor workspace {workspace_id} has no session yet")
    return str(sessions[0]["id"])


def workspace_id_from(created: dict[str, Any]) -> str:
    candidates = (
        created.get("workspaceId"),
        (created.get("workspace") or {}).get("id"),
        (created.get("data") or {}).get("id") if isinstance(created.get("data"), dict) else None,
        created.get("id"),
    )
    for value in candidates:
        if isinstance(value, str) and value:
            return value
    raise RunBlocked(f"Could not read the workspace ID from Conductor output: {created}")


def bootstrap_outcome(transcript: dict[str, Any]) -> tuple[str, str] | None:
    """Return the agent's bootstrap verdict, reading only assistant prose.

    User messages and tool calls are ignored so the marker quoted in the prompt or
    echoed by a command never counts as the agent's answer.
    """
    outcome = None
    for item in transcript.get("data") or []:
        if item.get("type") != "agent":
            continue
        for text in _assistant_texts(((item.get("content") or {}).get("rawPayload")) or {}):
            for match in BOOTSTRAP_MARKER.finditer(text):
                outcome = (match.group(1), (match.group(2) or "").strip())
    return outcome


def wait_for_bootstrap(
    cli: str,
    session_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float = 20,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, str] | None:
    deadline = clock() + timeout_seconds
    while True:
        outcome = bootstrap_outcome(_session_transcript(cli, session_id))
        if outcome or clock() >= deadline:
            return outcome
        sleep(poll_seconds)


def create_delivery_session(cli: str, workspace_id: str, launch: dict[str, Any]) -> dict[str, Any]:
    # A resumed launch must not start a second delivery agent on the same workspace.
    for session in workspace_sessions(cli, workspace_id):
        if session.get("name") in {DELIVERY_SESSION_NAME, "Entrega"}:
            return {**session, "reused": True}
    arguments = [
        cli,
        "session",
        "create",
        "--workspace",
        workspace_id,
        "--agent",
        launch["agent"],
        "--name",
        DELIVERY_SESSION_NAME,
    ]
    if launch.get("model"):
        arguments += ["--model", launch["model"]]
    arguments += ["--message-file", "-", "--json"]
    return _json_output(run(arguments, input_text=launch["message"]).stdout)


TRANSCRIPT_PAGE_SIZE = 50


def _session_transcript(cli: str, session_id: str) -> dict[str, Any]:
    # A truncated page must never read as an empty transcript.
    items: list[Any] = []
    offset = 0
    while True:
        page = _transcript_page(cli, session_id, offset)
        batch = page.get("data") or []
        items += batch
        if not page.get("hasMore") or not batch:
            return {"data": items}
        offset += len(batch)


def _transcript_page(cli: str, session_id: str, offset: int) -> dict[str, Any]:
    limit = TRANSCRIPT_PAGE_SIZE
    while True:
        stdout = _cli_output(
            [cli, "session", "message", session_id, "--limit", str(limit),
             "--offset", str(offset), "--json"],
        )
        try:
            page = json.loads(stdout)
        except json.JSONDecodeError:
            if limit == 1:
                raise OrchestrationError(
                    f"Conductor returned an unreadable transcript page at offset {offset}"
                )
            limit = max(1, limit // 2)
            continue
        return page if isinstance(page, dict) else {"data": page}


def _cli_output(arguments: list[str]) -> str:
    """Run the Conductor CLI with stdout redirected to a file.

    Writing to a pipe, the CLI exits before flushing and cuts its output at 128 KiB,
    so large transcript pages arrive as broken JSON. A regular file receives it whole.
    """
    with tempfile.TemporaryFile(mode="w+") as output:
        result = subprocess.run(
            arguments, stdout=output, stderr=subprocess.PIPE, text=True, check=False
        )
        if result.returncode != 0:
            raise OrchestrationError(
                f"{' '.join(arguments[1:4])} failed: {result.stderr.strip() or result.returncode}"
            )
        output.seek(0)
        return output.read()


def _assistant_texts(payload: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(payload, dict):
        if payload.get("type") in {"tool_use", "tool_result", "user"}:
            return texts
        if payload.get("type") == "result" and isinstance(payload.get("result"), str):
            texts.append(payload["result"])
        if payload.get("type") == "text" and isinstance(payload.get("text"), str):
            texts.append(payload["text"])
        for key, value in payload.items():
            if key not in {"text", "result"}:
                texts += _assistant_texts(value)
    elif isinstance(payload, list):
        for value in payload:
            texts += _assistant_texts(value)
    return texts


def _find_values(payload: Any, keys: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value:
                found.append(value)
            else:
                found += _find_values(value, keys)
    elif isinstance(payload, list):
        for value in payload:
            found += _find_values(value, keys)
    return found


def _json_output(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return {"output": stdout.strip()}
    return value if isinstance(value, dict) else {"data": value}


def _block(description: str, marker_prefix: str, block: str) -> str | None:
    try:
        return extract_marker_block(description, marker_prefix=marker_prefix, block=block)
    except OrchestrationError:
        return None


def _first_value(block: str | None, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(\S+)", block or "")
    return match.group(1) if match else None


def _remote_branch_exists(repository: Path, branch: str) -> bool:
    result = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        cwd=repository,
        check=False,
    )
    return result.returncode == 0


def _commits_behind(repository: Path, branch: str, target: str) -> int | None:
    run(["git", "fetch", "--quiet", "origin", branch, target], cwd=repository, check=False)
    result = run(
        ["git", "rev-list", "--count", f"origin/{branch}..origin/{target}"],
        cwd=repository,
        check=False,
    )
    return int(result.stdout.strip()) if result.returncode == 0 else None


def _normalize_remote(remote: str) -> str:
    value = remote.strip().lower()
    value = re.sub(r"^git@([^:]+):", r"https://\1/", value)
    value = re.sub(r"^ssh://git@", "https://", value)
    return value.removesuffix(".git").rstrip("/")


RESUME_AGENT = "claude"
RESUME_MODEL = "opus-5-5-1m"


def resume_message(identifier: str) -> str:
    return (
        f"Reanuda la entrega de {identifier} con Issue Delivery Orchestrator. Una sesión anterior "
        "de este workspace se detuvo (por ejemplo por límite de uso) y no tienes su conversación.\n\n"
        "1. Comprueba que el plugin `issue-delivery-orchestrator-local` esté cargado en esta "
        "sesión; si no aparece, detente y dilo.\n"
        "2. Inicia el orquestador (Claude Code: "
        "`/issue-delivery-orchestrator-local:issue-delivery-orchestrator`; Codex: "
        f"`$issue-delivery-orchestrator`) para {identifier} con `--worktree` igual a la raíz de este "
        "checkout. Es un run existente: **no uses `--new-run`**. El motor devuelve "
        "`resumed: true`, la fase actual y `nextAction`; si el run quedó `blocked`, usa la acción "
        "`resume` del motor.\n"
        "3. Antes de seguir, revisa `git status` y `git log` contra el estado del run: cambios sin "
        "commit pueden ser trabajo a medias de la sesión anterior. Consérvalos y reconcílialos con "
        "el ticket en curso; no los descartes sin decírmelo.\n"
        "4. Relee el spec, los tickets y la issue en Linear con `read-linear-issue` y continúa desde "
        "`nextAction`. Los permisos que di en la sesión anterior (por ejemplo LocalReviewer) no se "
        "heredan: pídelos de nuevo cuando corresponda.\n"
    )


def find_issue_workspace(cli: str, identifier: str) -> dict[str, Any]:
    listing = _json_output(
        run([cli, "workspace", "list", "--mine", "--name", f"[{identifier}]",
             "--limit", "20", "--json"]).stdout
    )
    matches = [
        item for item in (listing.get("data") or [])
        if str(item.get("name") or "").startswith(f"[{identifier}]") and not item.get("archivedAt")
    ]
    if len(matches) != 1:
        raise RunBlocked(
            f"Expected one active Conductor workspace named [{identifier}], found "
            f"{len(matches)}. Pass --workspace."
        )
    return matches[0]


def working_sessions(cli: str, workspace_id: str) -> list[dict[str, Any]]:
    busy = []
    for session in workspace_sessions(cli, workspace_id):
        status = _json_output(
            run([cli, "session", "status", str(session["id"]), "--json"]).stdout
        ).get("status")
        if status == "working":
            busy.append({"id": session["id"], "name": session.get("name")})
    return busy


def create_resume_session(
    cli: str,
    workspace_id: str,
    message: str,
    *,
    agent: str,
    model: str | None,
) -> dict[str, Any]:
    existing = workspace_sessions(cli, workspace_id)
    count = sum(1 for item in existing if str(item.get("name") or "").startswith("Reanudación"))
    arguments = [cli, "session", "create", "--workspace", workspace_id, "--agent", agent,
                 "--name", f"Reanudación {count + 1}"]
    if model:
        arguments += ["--model", model]
    arguments += ["--message-file", "-", "--json"]
    return _json_output(run(arguments, input_text=message).stdout)

