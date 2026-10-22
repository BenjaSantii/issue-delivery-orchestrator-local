"""Start an independent QA conversation against pinned approved artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .cloud_launch import conductor_cli, workspace_sessions
from .errors import RunBlocked
from .state import now, run_mode, run_root, save_state
from .util import ensure_within, run


def snapshot(state: dict[str, Any]) -> dict[str, str]:
    root = run_root(Path(state["worktree"]), state["runId"])
    result = {"verifiedCommit": run(["git", "rev-parse", "HEAD"],
                                    cwd=Path(state["worktree"])).stdout.strip()}
    for key in ("spec", "tickets"):
        relative = (state.get("artifacts") or {}).get(key)
        if not relative:
            raise RunBlocked(f"Fresh QA requires the approved {key} artifact")
        path = ensure_within(Path(state["worktree"]) / relative, root)
        if not path.is_file():
            raise RunBlocked(f"Missing approved {key} artifact")
        result[key + "Sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def qa_prompt(state: dict[str, Any], pinned: dict[str, str], urls: list[str]) -> str:
    root = run_root(Path(state["worktree"]), state["runId"])
    return (
        f"Eres el evaluador independiente de QA para {state['issue']['identifier']}, "
        f"run {state['runId']}, commit {pinned['verifiedCommit']}. Este es un chat nuevo, "
        "sin historial del implementador. Usa las skills del plugin cargadas por el host; "
        "si faltan, devuelve BLOCKED. No inicies otro run ni modifiques código, tests, ramas, "
        "spec o tickets. Puedes escribir únicamente scripts y evidencia bajo el run ignorado.\n"
        f"Lee el spec aprobado {state['artifacts']['spec']} y los tickets "
        f"{state['artifacts']['tickets']}, y la referencia fresh-qa.md del orquestador. "
        "Juzga el resultado contra esos contratos, sin usar el relato del implementador.\n"
        f"Worktree: {state['worktree']}. Runtime registrado: "
        f"{json.dumps(state.get('runtimes', []))}. URLs: {json.dumps(urls)}. "
        f"Reviewer del modo: {run_mode(state)}. Reutiliza los servicios; no los reinicies "
        "entre casos. Verifica que ejecuten el SHA indicado.\n"
        "Ejecuta todos los casos del QA Script. UI con la skill de revisión del modo y "
        "mobile-web-reviewer para teléfono; API/concurrencia con scripts contra el runtime. "
        "En Conductor la revisión móvil SIEMPRE es simulación web; no pidas teléfono, ADB "
        "ni emulador nativo. Registra límites del SO como notas no bloqueantes, sin decir "
        "que validaste nativo. Reutiliza el flag local E2E existente para vendedor si aplica. "
        "Lee conductor-validation.md: abre las vistas web en http://localhost:3001 a :3004, "
        "con el servidor realmente en ese puerto; las APIs conservan sus puertos internos. "
        "Valida GPS, Alert.alert y demás contratos nativos con Jest o scripts contra código "
        "real, sustituyendo sólo adaptadores del SO. Adjunta comandos, SHA y assertions. "
        "Esos casos pueden ser PASS sin dispositivo ni WAIVED si las pruebas y la web "
        "aplicable pasan; no declares observado el SO. Fallos reales siguen siendo FAIL. "
        "Comprueba consecuencias: recarga/relectura por la app y, para persistencia, consulta "
        "la DB LOCAL del runtime con transacciones READ ONLY. Guarda antes, después, query "
        "redactada y prueba de transaction_read_only. Comprueba IDs, relaciones, auditoría, "
        "ausencia de duplicados y no-cambios ante rechazos cuando el caso lo requiera. No "
        "fabriques resultados mediante writes directos a DB ni uses producción. Una respuesta "
        "200 o un toast no demuestran persistencia.\n"
        f"Escribe {root}/validation/qa-results.json con reviewSessionId igual al ID real "
        "de ESTA nueva conversación y un campo persistence por caso según fresh-qa.md. "
        "Guarda las capturas en validation/ui/<ID>-<NN>-<slug>.png y las salidas en "
        "validation/qa/<QA-ID>.txt; el motor las asocia por nombre, sin rutas en el JSON. "
        "Escribe en validation/notes.md una línea `<ID>: qué demuestra` por historia y caso. "
        "Declara FAIL/BLOCKED con evidencia cuando corresponda; no inventes PASS ni concedas "
        "WAIVED. No publiques, repares ni completes checkpoints: el controlador consumirá tus "
        "resultados. Informa la ruta del resultado y los findings reproducibles.\n"
    )


def register(state: dict[str, Any], implementation_session: str, review_session: str,
             *, host_receipt: str, pinned: dict[str, str] | None = None) -> dict[str, Any]:
    if not state.get("observabilityVersion"):
        raise RunBlocked("Fresh QA is required only for new runs; do not retrofit active runs")
    if not implementation_session or not review_session or implementation_session == review_session:
        raise RunBlocked("QA must use a different, fresh conversation from implementation")
    entry = {**(pinned or snapshot(state)), "implementationSessionId": implementation_session,
             "reviewSessionId": review_session, "freshContext": True,
             "registeredAt": now(), "hostReceipt": host_receipt}
    state["qaSession"] = entry
    save_state(state)
    return entry


def caller_configuration(cli: str, session: dict[str, Any]) -> dict[str, str]:
    model, effort = session.get("model"), session.get("effort")
    if not isinstance(model, str) or not model.strip():
        raise RunBlocked("Calling chat has no explicit model; select it in Conductor before starting QA")
    catalog = json.loads(run([cli, "model", "--json"]).stdout)
    matches = [item for item in catalog.get("agents", []) if model in item.get("models", [])]
    # Conductor leaves effort unset while the chat shows the agent default; that default is what it runs.
    if len(matches) == 1 and (not isinstance(effort, str) or not effort.strip()):
        effort = matches[0].get("defaultEffort")
    if len(matches) != 1 or effort not in matches[0].get("efforts", []):
        raise RunBlocked("Cannot identify the calling chat's agent/model/effort in the Conductor catalog")
    return {"agent": matches[0]["agent"], "model": model, "effort": effort}


def launch(state: dict[str, Any], workspace: str, implementation_session: str,
           urls: list[str]) -> dict[str, Any]:
    if run_mode(state) != "conductor" or not state.get("observabilityVersion"):
        raise RunBlocked("Automatic QA chat creation is supported for new Conductor runs")
    if not state.get("runtimes") or not urls:
        raise RunBlocked("Register the local runtime and its URLs before starting QA")
    pinned = snapshot(state)
    cli = conductor_cli()
    sessions = workspace_sessions(cli, workspace)
    caller = next((s for s in sessions if s.get("id") == implementation_session), None)
    if caller is None:
        raise RunBlocked("Implementation session is not in this Conductor workspace")
    configuration = caller_configuration(cli, caller)
    previous = state.get("qaSession") or {}
    if all(previous.get(key) == value for key, value in pinned.items()):
        if previous.get("implementationSessionId") != implementation_session:
            raise RunBlocked("A different implementation session owns this QA attempt")
        if any(previous.get(key) != value for key, value in configuration.items()):
            raise RunBlocked("The existing QA conversation uses a different agent/model/effort from the calling chat")
        return previous
    contract = hashlib.sha256(json.dumps({**pinned, **configuration}, sort_keys=True).encode()).hexdigest()[:8]
    name = f"QA {state['runId']} {pinned['verifiedCommit'][:12]} {contract}"
    # Recover a created session after an interrupted response without duplicating the agent.
    candidates = [s for s in sessions if s.get("name") == name]
    if not candidates:
        prompt = qa_prompt(state, pinned, urls)
        run([cli, "session", "create", "--workspace", workspace, "--agent", configuration["agent"],
             "--model", configuration["model"], "--effort", configuration["effort"], "--name", name,
             "--message-file", "-", "--json"], input_text=prompt)
        candidates = [s for s in workspace_sessions(cli, workspace) if s.get("name") == name]
    if len(candidates) != 1:
        raise RunBlocked("Cannot identify the fresh QA conversation; inspect before retrying")
    selected = candidates[0]
    if any(selected.get(key) != configuration[key] for key in ("model", "effort")):
        raise RunBlocked("QA session model/effort does not match the calling chat")
    entry = register(state, implementation_session, selected["id"],
                     host_receipt=f"conductor-workspace:{workspace}", pinned=pinned)
    entry.update({**configuration, "workspaceId": workspace})
    save_state(state)
    return entry


def validate_session(state: dict[str, Any], results: dict[str, Any]) -> None:
    if not state.get("observabilityVersion"):
        return
    entry = state.get("qaSession") or {}
    if not entry.get("freshContext") or not entry.get("reviewSessionId"):
        raise RunBlocked("Register a fresh QA conversation before accepting its results")
    if entry.get("reviewSessionId") == entry.get("implementationSessionId"):
        raise RunBlocked("The implementation conversation cannot approve its own QA")
    if results.get("reviewSessionId") != entry["reviewSessionId"]:
        raise RunBlocked("QA results must identify the registered reviewer conversation")
    if any(entry.get(key) != value for key, value in snapshot(state).items()):
        raise RunBlocked("Code or approved artifacts changed; start fresh QA for the new candidate")
