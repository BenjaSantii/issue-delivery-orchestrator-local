"""Start a Linear issue in a T3 Code thread on this host, from the Grill or straight to delivery."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .cloud_launch import PLUGIN_ID, _json_output
from .errors import RunBlocked
from .qa_hosts import t3_cli, t3_model
from .util import run

DEFAULT_PROVIDER = "claudeAgent"
DEFAULT_MODEL = "opus"
NEEDS_GRILL = (
    "No approved spec block (Spec ID) in the Linear description",
    "No approved tickets block (Ticket ID) in the Linear description",
)


def needs_grill(readiness: dict[str, Any]) -> bool:
    """Missing spec or tickets start the thread at the Grill; any other problem blocks the launch."""
    others = [problem for problem in readiness["problems"] if problem not in NEEDS_GRILL]
    if others:
        raise RunBlocked("; ".join(others))
    return bool(readiness["problems"])


def branch_in_use(repository: Path, branch: str) -> str | None:
    """Where the issue branch already lives: a worktree here, or origin."""
    listing = run(["git", "worktree", "list", "--porcelain"], cwd=repository).stdout
    for block in listing.split("\n\n"):
        if f"branch refs/heads/{branch}" in block.splitlines():
            return block.splitlines()[0].removeprefix("worktree ")
    if run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repository,
           check=False).returncode == 0:
        return f"local branch {branch}"
    if run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], cwd=repository,
           check=False).returncode == 0:
        return f"origin/{branch}"
    return None


QA_STEPS = {
    "t3": ("thread de T3",
           "El QA corre en otro thread de T3: `qa-session` responde `needs_agent_launch`; ábrelo con "
           "`t3_thread_launch` tal como dice el pedido y regístralo con `qa-session --launched "
           "<threadId>`."),
    "paseo": ("agente de Paseo",
              "El QA corre en otro agente de Paseo del mismo workspace: créalo con `qa-session`, que "
              "usa el provider `claude-dev4` salvo que pases `--agent`."),
}


def t3_launch_message(readiness: dict[str, Any], *, grill: bool, base: str,
                      stack_parent: str | None, implement_model: str | None = None,
                      qa: dict[str, str] | None = None, host: str = "t3",
                      setup: str | None = None) -> str:
    identifier = readiness["identifier"]
    where, qa_step = QA_STEPS[host]
    flags = f"--mode {host} " + (f"--stack-parent {stack_parent}" if stack_parent else f"--base {base}")
    if implement_model:
        flags += f" --implement-model {implement_model}"
    if qa and qa.get("model"):
        flags += f" --qa-model {qa['model']}" + (f" --qa-effort {qa['effort']}" if qa.get("effort") else "")
    orchestrator = (
        "Inicia el orquestador (Claude Code: "
        "`/issue-delivery-orchestrator-local:issue-delivery-orchestrator`; Codex: "
        f"`$issue-delivery-orchestrator`) para {identifier} con `--worktree` igual a la raíz de "
        f"este checkout y `{flags}`."
    )
    if grill:
        start = (
            "2. Haz el Grill de la issue con `issue-delivery-grill`, una pregunta a la vez y con tu "
            "recomendación en cada una. Cuando apruebe el spec, publícalo con "
            "`issue-delivery-spec-publisher`. Si el spec dice `Prototype: yes` o el usuario lo pide, "
            "haz el prototipo con `issue-delivery-prototype` antes de los tickets, salvo que lo salte. "
            "Arma los tickets con `issue-delivery-ticket-publisher` y publícalos cuando los apruebe.\n"
            f"3. Con spec y tickets publicados y aprobados, {orchestrator[0].lower()}{orchestrator[1:]} "
            "Da por aprobados ese spec y esos tickets y no repitas el Grill.\n"
        )
    else:
        tickets = ", ".join(readiness["ticketIds"])
        start = (
            f"2. {orchestrator}\n"
            f"3. El usuario ya aprobó el spec {readiness['specId']} y los tickets {tickets}, "
            "publicados en Linear. Reconcilia esos bloques con el repositorio; si no hay "
            "diferencias, registra la aprobación vigente y continúa a Implement sin repetir el "
            "Grill. Si la reconciliación cambia el alcance, pide aprobación antes de seguir.\n"
        )
    return (
        f"Entrega {identifier} ({readiness['title']}) con Issue Delivery Orchestrator en este "
        f"{where}, sobre la rama {readiness['branch']}.\n\n"
        f"1. Si las skills del plugin `{PLUGIN_ID}` no aparecen en esta sesión, detente y dilo: "
        "no las sustituyas leyendo archivos a mano."
        + (f" Antes de seguir, prepara este worktree con `{setup}` y detente si falla." if setup else "")
        + "\n"
        f"{start}"
        "4. Usa el reviewer que decida el motor: en un host sin pantalla es "
        "`issue-delivery-playwright-review`, con `mobile-web-reviewer` para las historias de "
        f"teléfono. {qa_step} Espéralo con `qa-wait` en segundo plano y, si se detiene, "
        "retómalo con `qa-resume` o `qa-session --replace`. No abras el QA de otra forma.\n"
        "5. Pausa sólo ante tickets HITL, decisiones de tamaño o bloqueos reales; explica la "
        "acción mínima que necesitas. LocalReviewer es opcional: no lo ejecutes ni pidas permiso "
        "para correrlo.\n"
    )


def build_t3_launch(readiness: dict[str, Any], repository: Path, *, base: str,
                    stack_parent: str | None, provider: str, model: str | None,
                    implement_model: str | None = None, qa_model: str | None = None,
                    qa_effort: str | None = None) -> dict[str, Any]:
    if qa_effort and not qa_model:
        raise RunBlocked("--qa-effort requires --qa-model")
    grill = needs_grill(readiness)
    used = branch_in_use(repository, readiness["branch"])
    if used:
        raise RunBlocked(f"{readiness['identifier']} branch {readiness['branch']} already exists "
                         f"({used}); continue its thread instead of launching again")
    workspace_base = stack_parent or base
    run(["git", "fetch", "--quiet", "origin", workspace_base], cwd=repository)
    slug = t3_model(provider, model or DEFAULT_MODEL)
    qa = {"model": qa_model or "", "effort": qa_effort or ""}
    message = t3_launch_message(readiness, grill=grill, base=base, stack_parent=stack_parent,
                                implement_model=implement_model, qa=qa)
    title = f"[{readiness['identifier']}] {readiness['title']}"[:80]
    arguments = ["thread", "start", str(repository), "-", "--title", title,
                 "--worktree", f"origin/{workspace_base}", "--branch", readiness["branch"],
                 "--provider", provider, "--model", slug, "--json"]
    return {"arguments": arguments, "message": message, "provider": provider, "model": slug,
            "title": title, "startsAt": "grill" if grill else "implement",
            "workspaceBase": f"origin/{workspace_base}", "qa": qa}


def create_t3_thread(launch: dict[str, Any]) -> dict[str, Any]:
    created = _json_output(run([t3_cli(), *launch["arguments"]], input_text=launch["message"]).stdout)
    if not created.get("threadId"):
        raise RunBlocked("T3 did not return the new thread; check the app before launching again")
    return created
