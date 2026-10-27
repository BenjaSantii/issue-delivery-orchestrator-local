"""Context packages for fresh workers that implement, repair or refactor.

The principal chat keeps approvals, gates and checkpoints; each long, bounded piece of work runs in
a new worker so the run stops re-reading the whole implementation history on every model call.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import RunBlocked
from .stack import integration_ref, pr_target
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


def _run_file(state: dict[str, Any], raw: Path, label: str) -> Path:
    worktree = Path(state["worktree"])
    path = ensure_within(raw if raw.is_absolute() else worktree / raw,
                         run_root(worktree, state["runId"]))
    if not path.is_file():
        raise RunBlocked(f"The {label} is missing from the run")
    return path


def _accepted(state: dict[str, Any]) -> tuple[str, str]:
    worktree = Path(state["worktree"])
    head = run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    accepted = run(["git", "log", "--reverse", "--format=%h %s", f"{state['adoptedHead']}..HEAD"],
                   cwd=worktree).stdout.strip()
    return head, accepted


def _issue(state: dict[str, Any], key: str, brief: str, head: str, model: str | None,
           extra: dict[str, Any]) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    attempts = state.setdefault("implementWorkers", {})
    attempt = len(attempts.get(key, [])) + 1
    folder = run_root(worktree, state["runId"]) / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{'implement-' if not key.startswith(('refactor', 'review')) else ''}{key}-{attempt}.md"
    path.write_text(brief)
    attempts.setdefault(key, []).append(
        {"attempt": attempt, "startCommit": head, "model": model or "inherit",
         "package": str(path.relative_to(worktree)), **extra})
    save_state(state)
    return {
        "unit": key,
        "attempt": attempt,
        "package": str(path),
        "model": model or "inherit",
        "spawn": {
            "claude": {"subagent_type": "general-purpose", **({"model": model} if model else {})},
            "codex": {"fork_turns": "none", **({"model": model} if model else {})},
        },
        "prompt": f"Lee {path} completo y ejecuta sólo ese trabajo. Devuelve el resultado que pide.",
    }


def package(state: dict[str, Any], ticket_id: str | None, *, finding: Path | None = None,
            model: str | None = None) -> dict[str, Any]:
    """A ticket, a repair of a ticket, or a review/CI repair that belongs to no single ticket."""
    if not ticket_id and finding is None:
        raise RunBlocked("Pass --ticket, or --finding for a review or CI repair")
    spec, tickets = _artifact(state, "spec"), _artifact(state, "tickets")
    block = ticket_block(tickets.read_text(), ticket_id) if ticket_id else None
    repair = _run_file(state, finding, "finding for this repair") if finding is not None else None
    head, accepted = _accepted(state)
    model = model or state.get("implementModel")
    key = ticket_id or "review-repair"
    brief = _brief(state, key, block, head, accepted, spec, tickets, repair)
    result = _issue(state, key, brief, head, model, {"finding": str(repair) if repair else None})
    return {"ticket": ticket_id, **result}


def refactor_package(state: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
    """The Refactor review and its fixes, run on the branch delta without the implementation chat."""
    worktree = Path(state["worktree"])
    spec = _artifact(state, "spec")
    head, accepted = _accepted(state)
    target = integration_ref(state)
    base = run(["git", "merge-base", head, target], cwd=worktree, check=False)
    baseline = base.stdout.strip() if base.returncode == 0 else state["adoptedHead"]
    files = run(["git", "diff", "--name-only", f"{baseline}..HEAD"], cwd=worktree).stdout.strip()
    if not files:
        raise RunBlocked("The branch has no delta to refactor")
    receipt = run_root(worktree, state["runId"]) / "review" / "refactor-review.md"
    model = model or state.get("implementModel")
    reference = PLUGIN_ROOT / "skills" / "issue-delivery-orchestrator" / "references" / "refactor-review.md"
    lines = [
        f"# Refactor de {state['issue']['identifier']}",
        "",
        "Eres un worker nuevo de Issue Delivery Orchestrator: no tienes el historial del chat que te",
        "lanzó y no lo necesitas. Tu única fuente es este paquete y el repositorio.",
        "",
        f"- Worktree: {worktree}",
        f"- Run: {state['runId']} ({run_root(worktree, state['runId'])})",
        f"- Spec aprobado completo: {spec}",
        f"- Criterios a aplicar completos: {reference}",
        f"- Skill para editar: {PLUGIN_ROOT / 'skills' / 'issue-delivery-implement' / 'SKILL.md'}",
        f"- Baseline del delta: {baseline} ({target if base.returncode == 0 else 'adopción'})",
        f"- SHA inicial: {head}",
        f"- Recibo a escribir: {receipt}",
        "",
        "Lee primero los criterios completos, el spec y todos los AGENTS.md/CLAUDE.md aplicables.",
        "Revisa el diff `baseline..HEAD` contra cada gate y corrige sólo violaciones concretas,",
        "dentro del allowlist. Sal de él sólo por una violación concreta de AGENTS.md y registra",
        "regla, archivo y razón. No hagas refactors especulativos. Invoca `test` después del último",
        "cambio de cada commit y consume su recibo. Escribe el recibo con cada criterio en PASS,",
        "FAIL o N/A y su evidencia. No hagas push, no ejecutes checkpoints ni otros comandos del",
        "motor y no lances otros agentes.",
        "",
        "## Allowlist (archivos del delta)",
        "",
        files,
        "",
        "## Commits del run",
        "",
        accepted or "Ninguno.",
        "",
        "## Devuelve",
        "",
        "Una sola respuesta final con: estado (`DONE`, `BLOCKED` o `NEEDS_USER_DECISION`), ruta del",
        "recibo, SHA de cada commit de refactor, archivos tocados fuera del allowlist con su regla,",
        "resultado de `test`, y casos del QA Script o historias UI que los cambios pueden invalidar.",
        "",
    ]
    return {**_issue(state, "refactor", "\n".join(lines), head, model, {"receipt": str(receipt)}),
            "receipt": str(receipt)}


def _brief(state: dict[str, Any], ticket_id: str, block: str | None, head: str, accepted: str,
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
        "Implementa sólo este trabajo, delega la validación a la skill `test` y crea un commit",
        "local por ticket (o por causa raíz en una reparación). No hagas push, no ejecutes",
        "checkpoints, metrics, qa-session ni otros comandos del motor, no lances la etapa siguiente",
        "ni otros agentes, y no edites spec ni tickets. Si el ticket contradice el spec o necesita",
        "una decisión, detente y devuelve NEEDS_USER_DECISION.",
        "",
        *(["## Ticket", "", block, ""] if block else
          ["Esta reparación viene de review o CI y no pertenece a un solo ticket: usa el finding y",
           "el spec como contrato.", ""]),
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
            "que ya fallaron.",
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
