"""Per-attempt measurements saved in the ignored run, not in product commits."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import OrchestrationError, RunBlocked
from .metrics_receipt import observations, timestamp
from .slicing import classify
from .stack import integration_ref
from .state import now, run_root
from .usage import token_totals
from .util import atomic_write_json, read_json, run

STATUSES = ("PASS", "FAIL", "BLOCKED", "NO_OP")


def metrics_root(state: dict[str, Any]) -> Path:
    return run_root(Path(state["worktree"]), state["runId"]) / "metrics"


def record_path(state: dict[str, Any], unit: str, attempt: int) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", unit) or attempt < 1:
        raise OrchestrationError("Use a safe unit ID and a positive attempt")
    return metrics_root(state) / f"{unit}.{attempt}.json"


def records(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [read_json(path) for path in sorted(metrics_root(state).glob("*.json"))]


def _head(state: dict[str, Any]) -> str:
    worktree = Path(state["worktree"])
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=worktree).stdout.strip():
        raise RunBlocked("Record metrics at a clean tracked commit boundary")
    return run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def line_delta(state: dict[str, Any], base: str, head: str) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    # Resolve to commit IDs before passing ranges to git; no revision option injection.
    revisions = [run(["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
                     cwd=worktree).stdout.strip() for ref in (base, head)]
    if run(["git", "merge-base", "--is-ancestor", *revisions], cwd=worktree, check=False).returncode:
        raise RunBlocked("Metrics end commit must descend from its start commit")
    output = run(["git", "diff", "--numstat", "-z", "--no-renames", *revisions, "--"],
                 cwd=worktree).stdout
    result = {kind: {"added": 0, "deleted": 0} for kind in ("product", "test", "excluded")}
    for row in output.split("\0"):
        if not row:
            continue
        added, deleted, path = row.split("\t", 2)
        if added == "-":
            continue
        bucket = result[classify(path)]
        bucket["added"] += int(added)
        bucket["deleted"] += int(deleted)
    return result


def start(state: dict[str, Any], unit: str, kind: str, attempt: int,
          model: str, effort: str) -> dict[str, Any]:
    if not state.get("observabilityVersion"):
        raise RunBlocked("Metrics are enabled only for new runs; do not retrofit active runs")
    path = record_path(state, unit, attempt)
    previous = read_json(path)
    if previous:
        return previous
    if kind not in {"ticket", "phase"}:
        raise OrchestrationError("Metric kind must be ticket or phase")
    if any(r["unit"] == unit and not r.get("finishedAt") for r in records(state)):
        raise RunBlocked("The previous attempt of this unit is still running")
    record = {"unit": unit, "kind": kind, "attempt": attempt, "startedAt": now(),
              "startCommit": _head(state), "model": model, "effort": effort,
              "usage": None, "status": "RUNNING"}
    atomic_write_json(path, record)
    return record


def finish(state: dict[str, Any], unit: str, attempt: int, status: str,
           receipt: Any = None) -> dict[str, Any]:
    path = record_path(state, unit, attempt)
    record = read_json(path)
    if not record:
        raise RunBlocked("Start this metric unit before finishing it")
    if record.get("finishedAt"):
        if record["status"] != status:
            raise RunBlocked("An attempt is immutable; use a new attempt for a repair")
        return record
    if status not in STATUSES:
        raise OrchestrationError("Invalid metric completion status")
    end = now()
    dirty = bool(run(["git", "status", "--porcelain", "--untracked-files=no"],
                     cwd=Path(state["worktree"])).stdout.strip())
    head = (_head(state) if status in {"PASS", "NO_OP"} else run(
        ["git", "rev-parse", "HEAD"], cwd=Path(state["worktree"])).stdout.strip())
    elapsed = (timestamp(end) - timestamp(record["startedAt"])).total_seconds()
    if elapsed < 0:
        raise OrchestrationError("Metrics clock moved backwards")
    record.update(observations(receipt, record["startedAt"], end))
    record["executionSeconds"] = (elapsed - sum(record["waitSeconds"].values())
                                  if record["waitSeconds"] is not None else None)
    record.update({"finishedAt": end, "endCommit": head, "wallSeconds": elapsed,
                   "lines": None if dirty else line_delta(state, record["startCommit"], head),
                   "uncommittedChanges": dirty, "status": status})
    atomic_write_json(path, record)
    return record


def attach_usage(state: dict[str, Any], unit: str, attempt: int,
                 usage: dict[str, Any], attribution: str) -> dict[str, Any]:
    path = record_path(state, unit, attempt)
    record = read_json(path)
    if not record or not record.get("finishedAt"):
        raise RunBlocked("Finish the metric unit before importing complete turn usage")
    if attribution not in {"dedicated", "shared"}:
        raise OrchestrationError("Usage attribution must be dedicated or shared")
    if attribution == "dedicated" and usage["availability"] == "reported":
        left, right = usage.get("firstEventAt"), usage.get("lastEventAt")
        if not left or not right or not (timestamp(record["startedAt"]) <= timestamp(left)
                <= timestamp(right) <= timestamp(record["finishedAt"])):
            raise RunBlocked("Session spans beyond this unit; import it as shared usage")
        for other in records(state):
            prior = other.get("usage") or {}
            if (other["unit"], other["attempt"]) != (unit, attempt) and (
                prior.get("attribution") == "dedicated" and
                set(prior.get("requests", {})) & set(usage["requests"])
            ):
                raise RunBlocked("These requests already belong to a different unit")
    sources = record.setdefault("usageSources", {})
    source = {**usage, "attribution": attribution}
    prior_requests = (sources.get(usage["sessionId"]) or {}).get("requests", {})
    if any(prior_requests[k] != v for k, v in usage["requests"].items() if k in prior_requests):
        raise OrchestrationError("Conflicting previously imported usage report")
    source["requests"] = {**prior_requests, **usage["requests"]}
    sources[usage["sessionId"]] = source
    requests = {key: value for s in sources.values() for key, value in s["requests"].items()}
    record["usage"] = {"requests": requests, "sessionId": ", ".join(sources),
                       "availability": "reported" if requests else "unavailable",
                       "incomplete": any(s["availability"] != "reported" for s in sources.values()),
                       "attribution": "shared" if any(s["attribution"] == "shared" for s in sources.values()) else "dedicated"}
    if requests:
        for field in ("model", "effort"):
            observed = sorted({str(r[field]) for r in requests.values() if r.get(field)})
            record[field] = ", ".join(observed) if observed else "N/D"
    atomic_write_json(path, record)
    return record


def report(state: dict[str, Any]) -> str:
    marker = f"Métricas de entrega — {state['runId']}"
    lines = [f"### {marker}", "", "Tokens reportados; N/D significa no disponible. "
             "Caché es parte de entrada. Los consumos compartidos no se reparten entre tickets.", "",
             "| Unidad / intento | Tipo | Modelo / esfuerzo | Estado | Tiempo total (s) | "
             "Tokens entrada / salida / caché | Producto + / − | Tests + / − |",
             "| -- | -- | -- | -- | -- | -- | -- | -- |"]
    unique: dict[str, Any] = {}
    all_records = records(state)
    details = []
    for item in all_records:
        usage = item.get("usage") or {}
        tokens = "N/D"
        if usage.get("availability") == "reported":
            for key, value in usage["requests"].items():
                if key in unique and unique[key] != value:
                    raise OrchestrationError("Conflicting token reports across units")
                unique[key] = value
            totals = token_totals(usage["requests"])
            tokens = f"{totals['inputTokens']} / {totals['outputTokens']} / {totals['cachedInputTokens']}"
            tokens += f" ({usage['attribution']})"
            if usage.get("incomplete"):
                tokens += " parcial: consumo faltante N/D"
        delta = item.get("lines") or {}
        cells = [f"{item['unit']} / {item['attempt']}", item["kind"],
                 f"{item['model']} / {item['effort']}", item["status"],
                 str(round(item["wallSeconds"], 1)) if "wallSeconds" in item else "N/D", tokens]
        cells += [f"{delta[k]['added']} / {delta[k]['deleted']}" if k in delta else "N/D"
                  for k in ("product", "test")]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
        if item.get("finishedAt"):
            details.append(f"{item['unit']} / {item['attempt']}: `{item['startCommit'][:12]}` → "
                         f"`{item['endCommit'][:12]}`; " + "; ".join(
                             f"{key}={item[key] if item[key] is not None else 'N/D'}"
                             for key in ("executionSeconds", "waitSeconds", "checks", "repairCycles", "findings")) +
                         f"; sesión={(item.get('usage') or {}).get('sessionId', 'N/D')}.")
    lines += ["", *details]
    if unique:
        totals = token_totals(unique)
        lines += ["", f"Total de solicitudes únicas observadas: {totals['totalTokens']} tokens. "
                  "No incluye consumo no disponible; no representa dinero facturado."]
    head = run(["git", "rev-parse", "HEAD"], cwd=Path(state["worktree"])).stdout.strip()
    target = integration_ref(state)
    base = run(["git", "merge-base", head, target], cwd=Path(state["worktree"]), check=False)
    baseline = base.stdout.strip() if base.returncode == 0 else state["adoptedHead"]
    label = target if base.returncode == 0 else "adopción; target no disponible"
    final = line_delta(state, baseline, head)
    lines += ["", f"Delta final contra {label} (no suma de intentos): {final}.",
              "Las fases pueden contener tickets: no sumar sus tiempos o deltas como si fueran independientes."]
    return "\n".join(lines) + "\n"
