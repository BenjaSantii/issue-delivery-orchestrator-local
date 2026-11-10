"""Start an independent QA conversation against pinned approved artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .cloud_launch import _json_output, conductor_cli, stop_reason, workspace_sessions
from .errors import RunBlocked
from .stack import integration_ref
from .state import now, run_mode, run_root, save_state
from .util import ensure_within, run


# Above this many cases the reviewer delegates groups to fresh subagents to keep its context small.
QA_GROUP_THRESHOLD = 6


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


def reuse_plan(state: dict[str, Any], pinned: dict[str, str], requested: list[str]) -> dict[str, Any]:
    """Cases to rerun after a repair and PASS cases carried over from the previous candidate."""
    from .qa_script import approved_qa_cases

    worktree = Path(state["worktree"])
    root = run_root(worktree, state["runId"])
    previous = state.get("qaSession") or {}
    commit = previous.get("verifiedCommit")
    if not previous.get("reviewSessionId") or not commit:
        raise RunBlocked("No previous QA to reuse; run qa-session without --rerun")
    if any(previous.get(key) != pinned[key] for key in ("specSha256", "ticketsSha256")):
        raise RunBlocked("The approved spec or tickets changed; QA must rerun every case")
    if run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=worktree,
           check=False).returncode != 0:
        raise RunBlocked("The previous QA commit is not an ancestor of HEAD; QA must rerun every case")
    archived = root / "validation" / f"qa-results-{commit[:12]}-{previous['reviewSessionId'][:8]}.json"
    current = root / "validation" / "qa-results.json"
    # Archive before the new reviewer writes; a retried launch finds the archive instead.
    source = current if current.is_file() else archived
    if not source.is_file():
        raise RunBlocked("The previous QA left no results to reuse; QA must rerun every case")
    try:
        results = json.loads(source.read_text())
    except (ValueError, OSError) as error:
        raise RunBlocked("The previous QA results are unreadable; QA must rerun every case") from error
    if not isinstance(results, dict) or results.get("reviewSessionId") != previous["reviewSessionId"] or (
        results.get("verifiedCommit") != commit
    ):
        raise RunBlocked("The previous QA results belong to another conversation or commit; "
                         "QA must rerun every case")
    expected = [case["QA ID"] for case in approved_qa_cases(state) or []]
    unknown = sorted(set(requested) - set(expected))
    if unknown:
        raise RunBlocked(f"--rerun names cases outside the QA Script: {', '.join(unknown)}")
    if source == current:
        current.replace(archived)
    statuses = {str((item or {}).get("id")): (item or {}).get("status")
                for item in results.get("cases") or [] if isinstance(item, dict)}
    inherited = previous.get("carried") or {}
    relative = str(archived.resolve().relative_to(worktree.resolve()))
    carried, rerun = {}, []
    for qa_id in expected:
        if qa_id in requested:
            rerun.append(qa_id)
        elif statuses.get(qa_id) == "PASS":
            carried[qa_id] = {"verifiedCommit": commit, "results": relative}
        elif qa_id not in statuses and qa_id in inherited:
            carried[qa_id] = inherited[qa_id]
        else:
            rerun.append(qa_id)
    return {"rerun": rerun, "carried": carried}


def _base(state: dict[str, Any], commit: str) -> str | None:
    """Where the branch's own patch starts: its merge-base with the integration target."""
    result = run(["git", "merge-base", commit, integration_ref(state)],
                 cwd=Path(state["worktree"]), check=False)
    return result.stdout.strip() or None if result.returncode == 0 else None


def _patch_id(worktree: Path, base: str, commit: str) -> str:
    diff = run(["git", "diff", "--binary", base, commit], cwd=worktree).stdout
    if not diff.strip():
        return ""
    return (run(["git", "patch-id", "--stable"], cwd=worktree, input_text=diff).stdout.split() or [""])[0]


def rebase(state: dict[str, Any], *, old_base: str | None = None,
           rerun: list[str] | None = None) -> dict[str, Any]:
    """Re-pin a finished QA to the rebased HEAD when the branch's own patch did not change."""
    from .qa_script import approved_qa_cases

    worktree = Path(state["worktree"])
    root = run_root(worktree, state["runId"])
    entry = state.get("qaSession") or {}
    old = entry.get("verifiedCommit")
    if not entry.get("reviewSessionId") or not old:
        raise RunBlocked("No QA conversation is registered; run qa-session first")
    pinned = snapshot(state)
    if any(entry.get(key) != pinned[key] for key in ("specSha256", "ticketsSha256")):
        raise RunBlocked("The approved spec or tickets changed; QA must rerun every case")
    head = pinned["verifiedCommit"]
    if head == old:
        raise RunBlocked("QA is already pinned to HEAD; nothing to re-pin")
    if run(["git", "merge-base", "--is-ancestor", old, head], cwd=worktree, check=False).returncode == 0:
        raise RunBlocked("HEAD descends from the QA commit, so code changed; use qa-session --rerun")
    old_base = old_base or entry.get("baseCommit")
    new_base = _base(state, head)
    if not old_base or not new_base:
        raise RunBlocked("Cannot find the bases of the old and new patch; pass --old-base <sha>")
    if not _patch_id(worktree, old_base, old) or _patch_id(worktree, old_base, old) != _patch_id(
            worktree, new_base, head):
        raise RunBlocked("The rebase changed the branch's own patch; QA must run again for HEAD")
    current = root / "validation" / "qa-results.json"
    try:
        results = json.loads(current.read_text())
    except (ValueError, OSError) as error:
        raise RunBlocked("The QA left no readable results to re-pin") from error
    if results.get("reviewSessionId") != entry["reviewSessionId"] or results.get("verifiedCommit") != old:
        raise RunBlocked("The QA results belong to another conversation or commit")
    expected = [case["QA ID"] for case in approved_qa_cases(state) or []]
    requested = sorted(set(rerun or []))
    unknown = sorted(set(requested) - set(expected))
    if unknown:
        raise RunBlocked(f"--rerun names cases outside the QA Script: {', '.join(unknown)}")
    archived = root / "validation" / f"qa-results-{old[:12]}-{entry['reviewSessionId'][:8]}.json"
    current.replace(archived)
    relative = str(archived.resolve().relative_to(worktree.resolve()))
    statuses = {str(item.get("id")): item.get("status") for item in results.get("cases") or []
                if isinstance(item, dict)}
    inherited = entry.get("carried") or {}
    carried, pending = {}, []
    for qa_id in expected:
        if qa_id not in requested and statuses.get(qa_id) == "PASS":
            carried[qa_id] = {"verifiedCommit": old, "equivalentCommit": head, "results": relative}
        elif qa_id not in requested and qa_id not in statuses and qa_id in inherited:
            carried[qa_id] = {**inherited[qa_id], "equivalentCommit": head}
        else:
            pending.append(qa_id)
    # The engine writes HEAD's file: no case ran again, every one is carried from the patch-identical commit.
    current.write_text(json.dumps({
        "qaVersion": 1, "verifiedCommit": head, "runtimeId": results.get("runtimeId"),
        "reviewSessionId": entry["reviewSessionId"], "verifiedAt": now(), "rebasedFrom": old,
        "cases": []}, indent=2) + "\n")
    entry.update({"verifiedCommit": head, "baseCommit": new_base, "carried": carried, "rerun": pending})
    entry.setdefault("rebasedFrom", []).append(old)
    save_state(state)
    return {"verifiedCommit": head, "rebasedFrom": old, "carried": sorted(carried), "pending": pending,
            "next": "qa-session --replace to run the pending cases" if pending else "checkpoint"}


def replacement_plan(state: dict[str, Any], pinned: dict[str, str]) -> dict[str, Any] | None:
    """What a stopped reviewer of this same candidate already settled, so its successor skips it."""
    previous = state.get("qaSession") or {}
    try:
        plan = reuse_plan(state, pinned, [])
    except RunBlocked:
        # The stopped reviewer wrote nothing of its own; keep only what it had inherited.
        plan = None
    if plan is None:
        from .qa_script import approved_qa_cases
        carried = previous.get("carried") or {}
        expected = [case["QA ID"] for case in approved_qa_cases(state) or []]
        plan = {"rerun": [qa_id for qa_id in expected if qa_id not in carried], "carried": carried}
    return plan if plan["carried"] else None


def _status(cli: str, session_id: str) -> str | None:
    return _json_output(run([cli, "session", "status", session_id, "--json"]).stdout).get("status")


class ConductorQaHost:
    name = "conductor"

    def __init__(self, workspace: str | None):
        self.workspace = workspace or ""
        self.cli = conductor_cli()
        self._sessions: list[dict[str, Any]] | None = None

    @property
    def sessions(self) -> list[dict[str, Any]]:
        if self._sessions is None:
            if not self.workspace:
                raise RunBlocked("Pass --workspace with the Conductor workspace ID")
            self._sessions = workspace_sessions(self.cli, self.workspace)
        return self._sessions

    def caller(self, implementation_session: str | None, exclude: tuple[str, ...] = ()) -> str:
        if not any(s.get("id") == implementation_session for s in self.sessions):
            raise RunBlocked("Implementation session is not in this Conductor workspace")
        return str(implementation_session)

    def configuration(self, agent, model, effort, caller, spec, lane):
        if not model:
            model, effort = spec
        if model:
            return resolve_configuration(self.cli, model, effort, "Requested QA model"), "explicit"
        session = next(s for s in self.sessions if s.get("id") == caller)
        return caller_configuration(self.cli, session), "inherited"

    def status(self, session_id: str) -> str | None:
        return _status(self.cli, session_id)

    def create(self, state: dict[str, Any], name: str, prompt: str, configuration: dict[str, str]) -> str:
        candidates = [s for s in self.sessions if s.get("name") == name]
        if not candidates:
            run([self.cli, "session", "create", "--workspace", self.workspace, "--agent",
                 configuration["agent"], "--model", configuration["model"], "--effort",
                 configuration["effort"], "--name", name, "--message-file", "-", "--json"],
                input_text=prompt)
            candidates = [s for s in workspace_sessions(self.cli, self.workspace) if s.get("name") == name]
        if len(candidates) != 1:
            raise RunBlocked("Cannot identify the fresh QA conversation; inspect before retrying")
        selected = candidates[0]
        if any(selected.get(key) != configuration[key] for key in ("model", "effort")):
            raise RunBlocked("QA session model/effort does not match the requested configuration")
        return str(selected["id"])

    def receipt(self) -> str:
        return f"conductor-workspace:{self.workspace}"

    def send(self, session_id: str, message: str) -> None:
        run([self.cli, "message", "create", "--session", session_id, "--message-file", "-"],
            input_text=message)

    def stop_reason(self, session_id: str) -> dict[str, Any] | None:
        return stop_reason(self.cli, session_id)


def qa_host(mode: str, workspace: str | None, launched: str | None = None):
    if launched and mode != "t3":
        raise RunBlocked("--launched registers a QA thread only in t3 mode")
    if mode == "conductor":
        return ConductorQaHost(workspace)
    if mode == "superset":
        from .qa_hosts import SupersetQaHost

        return SupersetQaHost(workspace)
    if mode == "t3":
        from .qa_hosts import T3QaHost

        return T3QaHost(workspace, launched)
    raise RunBlocked("Automatic QA chat creation is supported for new Conductor, Superset and T3 runs")


def resume_message(pinned: dict[str, str], note: str | None) -> str:
    return (
        "Tu sesión de QA se detuvo (límite de uso, reinicio de la VM u otro corte). Retoma el QA "
        f"donde quedaste, sobre el mismo commit {pinned['verifiedCommit']}. Antes de seguir, "
        "comprueba que los servicios del runtime respondan y ejecuten ese SHA; si no, devuelve "
        "BLOCKED de entorno. No repitas casos que ya tengan resultado en "
        "validation/qa-results.json o evidencia completa; para los pendientes vuelve a tomar el "
        "estado \"antes\" de la DB, porque pudo cambiar. Actualiza qa-results.json después de "
        "cada caso y al final con todos.\n" + (f"\nNota del controlador:\n{note.strip()}\n" if note else "")
    )


def resume(state: dict[str, Any], note: Path | None = None) -> dict[str, Any]:
    """Wake the registered reviewer instead of starting another QA from zero."""
    entry = state.get("qaSession") or {}
    review = entry.get("reviewSessionId")
    if not review:
        raise RunBlocked("No QA conversation is registered; run qa-session first")
    pinned = snapshot(state)
    if any(entry.get(key) != value for key, value in pinned.items()):
        raise RunBlocked("Code or approved artifacts changed since this QA; create a new one with "
                         "qa-session --rerun")
    host = qa_host(run_mode(state), entry.get("workspaceId"))
    if host.status(review) == "working":
        raise RunBlocked("The QA conversation is still working; wait for it with qa-wait")
    text = None
    if note is not None:
        worktree = Path(state["worktree"])
        path = ensure_within(note if note.is_absolute() else worktree / note,
                             run_root(worktree, state["runId"]))
        if not path.is_file():
            raise RunBlocked("The resume note is missing from the run")
        text = path.read_text()
    host.send(review, resume_message(pinned, text))
    entry.setdefault("resumedAt", []).append(now())
    save_state(state)
    return {"reviewSessionId": review, "resumed": True, "resumes": len(entry["resumedAt"])}


def qa_prompt(state: dict[str, Any], pinned: dict[str, str], urls: list[str],
              plan: dict[str, Any] | None = None, brief: str | None = None,
              host: str = "conductor") -> str:
    root = run_root(Path(state["worktree"]), state["runId"])
    if host in ("superset", "t3"):
        identity = (f"reviewSessionId igual al valor de {root}/validation/qa-session.json, que el "
                    "controlador escribe al crear esta conversación (si aún no existe, espera unos "
                    "segundos y vuelve a leerlo)")
        surface = ("En este host sin pantalla la revisión móvil SIEMPRE es simulación web; no pidas "
                   "teléfono, ADB ni emulador nativo. " if host == "superset" else
                   "En T3 la revisión móvil SIEMPRE es simulación web; no pidas teléfono, ADB ni "
                   "emulador nativo. ")
    else:
        identity = "reviewSessionId igual al ID real de ESTA nueva conversación"
        surface = ("En Conductor la revisión móvil SIEMPRE es simulación web; no pidas teléfono, ADB "
                   "ni emulador nativo. ")
    ports = ("Lee conductor-validation.md: abre las vistas web en http://localhost:3001 a :3004, "
             "con el servidor realmente en ese puerto; las APIs conservan sus puertos internos. "
             if host == "conductor" else "")
    # Without these facts the reviewer reads product code to find them, a large share of QA tokens.
    operations = (
        f"Brief operativo con rutas, cuentas, datos sembrados y cómo llegar a cada estado: {brief}. "
        "Úsalo en vez de leer código del producto. " if brief else "")
    from .qa_script import approved_qa_cases

    scope = "Ejecuta todos los casos del QA Script. "
    pending = len(plan["rerun"]) if plan and plan["carried"] else len(approved_qa_cases(state) or [])
    # Each case adds screenshots, script output and code it read; later cases re-read all of it.
    groups = (
        f"Son {pending} casos: coordínalos por grupos. Agrupa 3 a 5 casos por historia o "
        "superficie y ejecuta cada grupo con un subagente nuevo (Claude: herramienta Agent, "
        "subagent_type general-purpose, sin model; Codex: spawn_agent con fork_turns=\"none\"), "
        "UNO A LA VEZ y nunca en paralelo, porque comparten runtime, DB y datos sembrados. Dale el "
        "spec, el brief, la skill de revisión, sus casos, runtime, URLs, SHA y las rutas de "
        "evidencia; que devuelva por caso estado, resumen, capturas, salidas y persistencia. Tú no "
        "ejecutas los casos: juntas los resultados y escribes qa-results.json tras cada grupo.\n"
        if pending > QA_GROUP_THRESHOLD else "")
    if plan and plan["carried"]:
        carried = ", ".join(f"{qa_id} ({item['verifiedCommit'][:12]})"
                            for qa_id, item in sorted(plan["carried"].items()))
        scope = (
            f"Ejecuta sólo estos casos del QA Script: {', '.join(plan['rerun'])}. Los demás "
            f"quedaron PASS en un commit anterior y se heredan: {carried}. No los ejecutes ni "
            "toques su evidencia (capturas, salidas ni carpetas de persistencia); qa-results.json "
            "lista sólo los casos que ejecutes. "
        )
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
        f"{operations}Prueba como caja negra: lee código del producto sólo para desbloquear un "
        "paso concreto (una ruta, selector o dato que no encuentras) y nunca para juzgar. Tus "
        "scripts imprimen sólo un resumen corto por assertion (resultado, URL y ruta de la "
        "captura o salida); el DOM, la consola y las respuestas completas van a archivos bajo "
        "validation/, que lees sólo si una assertion falla.\n"
        f"{groups}{scope}UI con la skill de revisión del modo y "
        "mobile-web-reviewer para teléfono; API/concurrencia con scripts contra el runtime. "
        f"{surface}Registra límites del SO como notas no bloqueantes, sin decir "
        "que validaste nativo. Reutiliza el flag local E2E existente para vendedor si aplica. "
        f"{ports}Valida GPS, Alert.alert y demás contratos nativos con Jest o scripts contra código "
        "real, sustituyendo sólo adaptadores del SO. Adjunta comandos, SHA y assertions. "
        "Esos casos pueden ser PASS sin dispositivo ni WAIVED si las pruebas y la web "
        "aplicable pasan; no declares observado el SO. Fallos reales siguen siendo FAIL. "
        "Comprueba consecuencias: recarga/relectura por la app y, para persistencia, consulta "
        "la DB LOCAL del runtime con transacciones READ ONLY. Guarda antes, después, query "
        "redactada y prueba de transaction_read_only. Comprueba IDs, relaciones, auditoría, "
        "ausencia de duplicados y no-cambios ante rechazos cuando el caso lo requiera. No "
        "fabriques resultados mediante writes directos a DB ni uses producción. Una respuesta "
        "200 o un toast no demuestran persistencia.\n"
        f"Escribe {root}/validation/qa-results.json con {identity} y un campo persistence por caso según fresh-qa.md. "
        "Actualízalo después de cada caso terminado, con los casos hechos hasta ahora, y al "
        "final con todos: si tu sesión se corta, lo ya resuelto no se repite. "
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


def resolve_configuration(cli: str, model: Any, effort: Any, subject: str) -> dict[str, str]:
    if not isinstance(model, str) or not model.strip():
        raise RunBlocked(f"{subject} has no explicit model; select it in Conductor before starting QA")
    catalog = json.loads(run([cli, "model", "--json"]).stdout)
    matches = [item for item in catalog.get("agents", []) if model in item.get("models", [])]
    # Conductor leaves effort unset while the chat shows the agent default; that default is what it runs.
    if len(matches) == 1 and (not isinstance(effort, str) or not effort.strip()):
        effort = matches[0].get("defaultEffort")
    if len(matches) != 1 or effort not in matches[0].get("efforts", []):
        raise RunBlocked(f"Cannot identify the {subject.lower()}'s agent/model/effort in the Conductor catalog")
    return {"agent": matches[0]["agent"], "model": model, "effort": effort}


def spec_qa_configuration(state: dict[str, Any]) -> tuple[str | None, str | None]:
    """The QA model the approved spec recommends by difficulty, for runs launched without one."""
    relative = (state.get("artifacts") or {}).get("spec")
    path = Path(state["worktree"]) / relative if relative else None
    if not path or not path.is_file():
        return None, None
    text = path.read_text()
    values = [re.search(rf"(?m)^{key}:[ \t]*(\S+)", text) for key in ("QA Model", "QA Effort")]
    return tuple(match.group(1) if match else None for match in values)


def caller_configuration(cli: str, session: dict[str, Any]) -> dict[str, str]:
    return resolve_configuration(cli, session.get("model"), session.get("effort"), "Calling chat")


def _lane(state: dict[str, Any]) -> str:
    relative = (state.get("artifacts") or {}).get("spec")
    path = Path(state["worktree"]) / relative if relative else None
    match = re.search(r"(?m)^Lane:[ \t]*(\S+)", path.read_text()) if path and path.is_file() else None
    return match.group(1).lower() if match else "standard"


def _brief_path(state: dict[str, Any], brief: Path | None) -> str | None:
    if brief is None:
        return None
    worktree = Path(state["worktree"])
    path = ensure_within(brief if brief.is_absolute() else worktree / brief,
                         run_root(worktree, state["runId"]))
    if not path.is_file():
        raise RunBlocked("The QA operational brief is missing from the run")
    return str(path)


def launch(state: dict[str, Any], workspace: str | None, implementation_session: str | None,
           urls: list[str], model: str | None = None, effort: str | None = None,
           rerun: list[str] | None = None, brief: Path | None = None,
           replace: bool = False, user_approval: str | None = None,
           agent: str | None = None, launched: str | None = None) -> dict[str, Any]:
    if effort and not model:
        raise RunBlocked("--effort for QA requires --model; without both, QA inherits the calling chat")
    # After manual revision, blocker fixes are covered by tests and CI; QA runs only on the user's say.
    if state.get("currentPhase") in ("pr-creation", "review-convergence") and not (user_approval or "").strip():
        raise RunBlocked("QA after manual revision runs only when the user allows a round: tell them "
                         "which cases the change can affect and pass their words with --user-approved")
    if not state.get("observabilityVersion"):
        raise RunBlocked("Automatic QA chat creation is supported only for new runs")
    if not state.get("runtimes") or not urls:
        raise RunBlocked("Register the local runtime and its URLs before starting QA")
    pinned = snapshot(state)
    previous = state.get("qaSession") or {}
    host = qa_host(run_mode(state), workspace or previous.get("workspaceId"), launched)
    exclude = (previous["reviewSessionId"],) if previous.get("reviewSessionId") else ()
    # Earlier QA terminals stay live, so the implementer registered last time is the safe default.
    caller = host.caller(implementation_session or previous.get("implementationSessionId"), exclude)
    stored = state.get("qaConfiguration") or {}
    # An explicit QA model decouples the reviewer's cost from the implementer's.
    if not model and stored.get("model"):
        model, effort = stored["model"], stored.get("effort")
    agent = agent or stored.get("agent")
    spec = spec_qa_configuration(state)
    configuration, source = host.configuration(agent, model, effort, caller, spec, _lane(state))
    requested = sorted(set(rerun or []))
    if "all" in requested:
        if len(requested) > 1:
            raise RunBlocked("--rerun all cannot be combined with case IDs")
        requested = []
    same_candidate = all(previous.get(key) == value for key, value in pinned.items())
    if replace and not same_candidate:
        raise RunBlocked("--replace only replaces a stopped QA of this same commit; use --rerun")
    if replace and requested:
        raise RunBlocked("--replace already reruns only what the stopped QA left pending")
    if replace and host.status(previous["reviewSessionId"]) == "working":
        raise RunBlocked("The QA conversation is still working; wait for it with qa-wait")
    if same_candidate and not replace:
        if requested and previous.get("rerunRequest") != requested:
            raise RunBlocked("A QA conversation already exists for this commit with another --rerun set")
        if previous.get("implementationSessionId") != caller:
            raise RunBlocked("A different implementation session owns this QA attempt")
        if any(previous.get(key) != value for key, value in configuration.items()):
            raise RunBlocked(
                f"The existing QA conversation uses a different agent/model/effort from the {source} configuration")
        return previous
    # Only a partial rerun reuses evidence; --rerun all and no --rerun keep the full QA.
    if replace:
        plan = replacement_plan(state, pinned)
    else:
        plan = reuse_plan(state, pinned, requested) if requested else None
    if plan and not plan["carried"]:
        plan = None
    contract = hashlib.sha256(json.dumps({**pinned, **configuration, "rerun": plan and plan["rerun"],
                                          "replaces": previous.get("reviewSessionId") if replace else None},
                                         sort_keys=True).encode()).hexdigest()[:8]
    name = f"QA {state['runId']} {pinned['verifiedCommit'][:12]} {contract}"
    prompt = qa_prompt(state, pinned, urls, plan, _brief_path(state, brief), host=host.name)
    from .qa_hosts import QaLaunchRequired

    try:
        review = host.create(state, name, prompt, configuration)
    except QaLaunchRequired as required:
        return {"status": "needs_agent_launch", "launch": required.request,
                "next": "Call t3_thread_launch with this title, provider, model, effort, workspace and "
                        "the messageFile text as message; then rerun this qa-session command adding "
                        "--launched <threadId>"}
    entry = register(state, caller, review, host_receipt=host.receipt(), pinned=pinned)
    entry.update({**configuration, "configurationSource": source, "workspaceId": host.workspace,
                  "baseCommit": _base(state, "HEAD")})
    if requested:
        entry["rerunRequest"] = requested
    if replace:
        entry["replaces"] = previous.get("reviewSessionId")
    if (user_approval or "").strip():
        entry["userApproval"] = user_approval.strip()
    if plan:
        entry.update({"rerun": plan["rerun"], "carried": plan["carried"]})
    save_state(state)
    return entry


def outcome(state: dict[str, Any], host: Any) -> dict[str, Any]:
    # Conductor callers pass the CLI path; Superset passes its QA host.
    """Whether an idle reviewer finished every pending case or stopped before writing them all."""
    from .qa_script import approved_qa_cases

    entry = state.get("qaSession") or {}
    summary = results_summary(state)
    path = run_root(Path(state["worktree"]), state["runId"]) / "validation" / "qa-results.json"
    written: set[str] = set()
    if summary.get("readable") and summary.get("reviewSessionId") == entry.get("reviewSessionId"):
        cases = json.loads(path.read_text()).get("cases") or []
        written = {str(case.get("id")) for case in cases if isinstance(case, dict)}
    expected = {case["QA ID"] for case in approved_qa_cases(state) or []}
    missing = sorted(expected - written - set(entry.get("carried") or {}))
    if not missing:
        return {"state": "complete", "results": summary}
    reason = (host.stop_reason(entry["reviewSessionId"]) if hasattr(host, "stop_reason")
              else stop_reason(host, entry["reviewSessionId"])) or {"reason": "unknown"}
    return {"state": "stopped", "missing": missing, **reason, "results": summary}


def results_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Light view of the QA results so the implementer can react; the checkpoint validates them fully."""
    path = run_root(Path(state["worktree"]), state["runId"]) / "validation" / "qa-results.json"
    if not path.is_file():
        return {"exists": False}
    try:
        results = json.loads(path.read_text())
    except (ValueError, OSError):
        return {"exists": True, "readable": False}
    cases = results.get("cases") if isinstance(results, dict) else None
    counts: dict[str, int] = {}
    for case in cases if isinstance(cases, list) else []:
        status = case.get("status") if isinstance(case, dict) else None
        counts[str(status)] = counts.get(str(status), 0) + 1
    return {"exists": True, "readable": True, "reviewSessionId": results.get("reviewSessionId"),
            "statusCounts": counts}


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
