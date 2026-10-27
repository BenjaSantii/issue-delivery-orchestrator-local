"""Context packages for the fresh worker that implements or repairs one ticket.

The principal chat keeps approvals, gates and checkpoints; each ticket runs in a new worker so
long runs stop re-reading the whole implementation history on every model call.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import RunBlocked
from .stack import pr_target
from .state import run_root, save_state
from .util import ensure_within, run

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
# Claude Code's Agent tool accepts only these aliases; Codex takes its own model IDs.
CLAUDE_WORKER_MODELS = ("sonnet", "opus", "haiku", "fable")
_TICKET = re.compile(r"(?m)^Ticket ID:[ \t]*(\S+)")


def ticket_block(tickets: str, ticket_id: str) -> str:
    starts = [(match.start(), match.group(1)) for match in _TICKET.finditer(tickets)]
    for index, (start, identifier) in enumerate(starts):
        if identifier == ticket_id:
            end = starts[index + 1][0] if index + 1 < len(starts) else len(tickets)
            return re.sub(r"(?s)\n-{3,}\s*$", "", tickets[start:end].rstrip()).rstrip()
    raise RunBlocked(f"{ticket_id} is not in the approved tickets")


def _artifact(state: dict[str, Any], key: str) -> Path:
    relative = (state.get("artifacts") or {}).get(key)
    if not relative:
        raise RunBlocked(f"Implement workers require the approved {key} artifact")
    root = run_root(Path(state["worktree"]), state["runId"])
    path = ensure_within(Path(state["worktree"]) / relative, root)
    if not path.is_file():
        raise RunBlocked(f"Missing approved {key} artifact")
    return path


def package(state: dict[str, Any], ticket_id: str, *, finding: Path | None = None,
            model: str | None = None) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    root = run_root(worktree, state["runId"])
    spec, tickets = _artifact(state, "spec"), _artifact(state, "tickets")
    block = ticket_block(tickets.read_text(), ticket_id)
    head = run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    accepted = run(["git", "log", "--reverse", "--format=%h %s", f"{state['adoptedHead']}..HEAD"],
                   cwd=worktree).stdout.strip()
    repair = None
    if finding is not None:
        repair = ensure_within(finding if finding.is_absolute() else worktree / finding, root)
        if not repair.is_file():
            raise RunBlocked("The finding for this repair is missing from the run")
    model = model or state.get("implementModel")
    attempts = state.setdefault("implementWorkers", {})
    attempt = len(attempts.get(ticket_id, [])) + 1
    folder = root / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"implement-{ticket_id}-{attempt}.md"
    path.write_text(_brief(state, ticket_id, block, head, accepted, spec, tickets, repair))
    attempts.setdefault(ticket_id, []).append(
        {"attempt": attempt, "startCommit": head, "model": model or "inherit",
         "package": str(path.relative_to(worktree)), "finding": str(repair) if repair else None})
    save_state(state)
    return {
        "ticket": ticket_id,
        "attempt": attempt,
        "package": str(path),
        "model": model or "inherit",
        "spawn": {
            "claude": {"subagent_type": "general-purpose", **({"model": model} if model else {})},
            "codex": {"fork_turns": "none", **({"model": model} if model else {})},
        },
        "prompt": f"Lee {path} completo y ejecuta sólo ese trabajo. Devuelve el resultado que pide.",
    }


def _brief(state: dict[str, Any], ticket_id: str, block: str, head: str, accepted: str,
           spec: Path, tickets: Path, repair: Path | None) -> str:
    worktree = Path(state["worktree"])
    lines = [
        f"# {'Reparación' if repair else 'Implementación'} de {ticket_id} — "
        f"{state['issue']['identifier']}",
        "",
        "Eres un worker nuevo de Issue Delivery Orchestrator: no tienes el historial del chat que te",
        "lanzó y no lo necesitas. Tu única fuente es este paquete y el repositorio.",
        "",
        f"- Worktree: {worktree}",
        f"- Run: {state['runId']} ({run_root(worktree, state['runId'])})",
        f"- Spec aprobado completo: {spec}",
        f"- Tickets aprobados: {tickets}",
        f"- Skill a seguir: {PLUGIN_ROOT / 'skills' / 'issue-delivery-implement' / 'SKILL.md'}",
        f"- SHA inicial: {head}",
        f"- Base / target: {state.get('base')} / {pr_target(state)}",
        "",
        "Lee primero la skill, el spec completo y todos los AGENTS.md/CLAUDE.md aplicables.",
        "Implementa sólo este ticket, delega la validación a la skill `test` y crea un commit",
        "local por ticket (o por causa raíz en una reparación). No hagas push, no ejecutes",
        "checkpoints, metrics, qa-session ni otros comandos del motor, no lances la etapa siguiente",
        "ni otros agentes, y no edites spec ni tickets. Si el ticket contradice el spec o necesita",
        "una decisión, detente y devuelve NEEDS_USER_DECISION.",
        "",
        "## Ticket",
        "",
        block,
        "",
        "## Tickets ya aceptados en este run",
        "",
        accepted or "Ninguno todavía.",
        "",
    ]
    if repair:
        lines += [
            "## Finding a reparar",
            "",
            f"Evidencia: {repair}",
            "Lee la evidencia, reproduce el fallo y no repitas el enfoque de intentos anteriores",
            "de este ticket que ya fallaron.",
            "",
        ]
    lines += [
        "## Devuelve",
        "",
        "Una sola respuesta final con: estado (`DONE`, `NO_OP`, `BLOCKED` o `NEEDS_USER_DECISION`),",
        "SHA del commit, archivos del delta, comandos de validación y su resultado según el recibo de",
        "`test`, tests no ejecutados y por qué, historias UI y casos del QA Script que el cambio",
        "puede invalidar, y bloqueos con la acción mínima que necesitas.",
        "",
    ]
    return "\n".join(lines)
