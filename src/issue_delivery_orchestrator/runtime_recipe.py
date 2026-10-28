"""A run's recorded start-up recipe for its Local Runtime, replayed after a VM restart.

Without it every resumed chat rebuilt the runtime by trial and error (manifest, binding, Redis,
secrets, start order), which took minutes and many model calls each time.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import settings
from .errors import OrchestrationError, RunBlocked
from .qa_script import _SECRET
from .runtime import register_owned_process
from .state import now, run_root, save_state
from .util import atomic_write_json, ensure_within, read_json, run

DEFAULT_TIMEOUT_SECONDS = 120
POLL_SECONDS = 2.0
LOG_TAIL_LINES = 20
# A secret-named variable assigned a literal; `X=$(command)` or `X=$OTHER` is how recipes read them.
_LITERAL_SECRET = re.compile(r"(?i)\b\w*(?:secret|token|password|passwd|api_key|apikey)\w*=(?![$\"']?\$)\S{6,}")


def _recipe_path(state: dict[str, Any], raw: Path) -> Path:
    worktree = Path(state["worktree"])
    path = ensure_within(raw if raw.is_absolute() else worktree / raw, run_root(worktree, state["runId"]))
    if not path.is_file():
        raise OrchestrationError("The runtime recipe is missing from the run")
    return path


def _validate(recipe: Any) -> list[dict[str, Any]]:
    services = recipe.get("services") if isinstance(recipe, dict) else None
    if not isinstance(services, list) or not services:
        raise OrchestrationError("The runtime recipe needs a non-empty services list")
    names: set[str] = set()
    for service in services:
        if not isinstance(service, dict):
            raise OrchestrationError("Each runtime recipe service must be an object")
        name, start, health = (str(service.get(key) or "").strip() for key in ("name", "start", "health"))
        if not name or not start or not health:
            raise OrchestrationError("Each runtime recipe service needs name, start and health")
        if name in names:
            raise OrchestrationError(f"Runtime recipe service {name} is listed twice")
        names.add(name)
        timeout = service.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise OrchestrationError(f"Runtime recipe service {name} needs a positive timeoutSeconds")
        # Secrets are read by commands at start time (export X=$(...)), never stored literally.
        values = [str(service.get(key) or "") for key in ("start", "health", "cwd")]
        if any(_SECRET.search(value) or _LITERAL_SECRET.search(value) for value in values):
            raise OrchestrationError(
                f"Runtime recipe service {name} contains what looks like a secret; read it with a command")
    return services


def register_recipe(state: dict[str, Any], raw: Path) -> dict[str, Any]:
    path = _recipe_path(state, raw)
    services = _validate(read_json(path))
    state["runtimeRecipe"] = str(path.relative_to(Path(state["worktree"]).resolve()))
    save_state(state)
    return {"recipe": state["runtimeRecipe"], "services": [service["name"] for service in services]}


def _active_runtime(state: dict[str, Any]) -> dict[str, Any]:
    runtimes = [item for item in state.get("runtimes") or [] if not item.get("cleanedAt")]
    active = next((item for item in runtimes if item["runtimeId"] == state.get("activeRuntimeId")), None)
    runtime = active or (runtimes[-1] if runtimes else None)
    if runtime is None or not Path(runtime["manifestPath"]).is_file():
        raise RunBlocked("No registered runtime with an existing manifest; run runtime-init")
    return runtime


def _restore_binding(state: dict[str, Any], runtime: dict[str, Any]) -> bool:
    binding = Path(state["worktree"]) / settings().runtime_root / "worktree.json"
    current = read_json(binding) if binding.is_file() else None
    if current and current.get("runtimeId") == runtime["runtimeId"] and current.get("manifestPath"):
        return False
    binding.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(binding, {"runtimeId": runtime["runtimeId"], "manifestPath": runtime["manifestPath"],
                                "createdAt": runtime.get("createdAt") or now()})
    return True


def _healthy(check: str, cwd: Path, env: dict[str, str]) -> bool:
    if check.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(check, timeout=5) as response:
                return response.status < 500
        except urllib.error.HTTPError as error:
            # Any HTTP answer below 500 means the server is up; a 404 root is still a live server.
            return error.code < 500
        except (urllib.error.URLError, OSError, ValueError):
            return False
    return subprocess.run(["bash", "-c", check], cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=60).returncode == 0


def _tail(path: Path) -> str:
    lines = path.read_text(errors="replace").splitlines() if path.is_file() else []
    return "\n".join(lines[-LOG_TAIL_LINES:])


def restore(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("runtimeRecipe"):
        raise RunBlocked("No runtime recipe is registered; write one and run runtime-recipe")
    worktree = Path(state["worktree"])
    services = _validate(read_json(_recipe_path(state, Path(state["runtimeRecipe"]))))
    runtime = _active_runtime(state)
    rebound = _restore_binding(state, runtime)
    env = {**os.environ, "LOCAL_RUNTIME_MANIFEST": runtime["manifestPath"]}
    logs = run_root(worktree, state["runId"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    results, failed = [], False
    for service in services:
        name = service["name"]
        cwd = ensure_within(worktree / service["cwd"], worktree) if service.get("cwd") else worktree
        started = time.monotonic()
        if _healthy(service["health"], cwd, env):
            results.append({"name": name, "status": "ok", "seconds": 0})
            continue
        log = logs / f"runtime-{name}.log"
        with log.open("a") as output:
            process = subprocess.Popen(["bash", "-c", service["start"]], cwd=cwd, env=env, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        if process.poll() is None:
            register_owned_process(state, process.pid, kind=f"runtime:{name}", command=service["start"])
        deadline = started + float(service.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS))
        healthy = False
        while time.monotonic() < deadline:
            if _healthy(service["health"], cwd, env):
                healthy = True
                break
            # A launcher may daemonize and exit 0; only a failing exit ends the wait early.
            if process.poll() not in (None, 0):
                break
            time.sleep(POLL_SECONDS)
        entry = {"name": name, "status": "started" if healthy else "failed",
                 "seconds": round(time.monotonic() - started)}
        if not healthy:
            entry["log"] = str(log.relative_to(worktree))
            entry["tail"] = _tail(log)
            failed = True
        results.append(entry)
        process.poll()
        if failed:
            # Later services usually depend on this one; stop instead of piling up failures.
            break
    head = run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    outcome = {"runtimeId": runtime["runtimeId"], "bindingRestored": rebound, "head": head, "services": results}
    state["runtimeRestoredAt"] = now()
    save_state(state)
    if failed:
        raise RunBlocked("Runtime restore failed:\n" + "\n".join(
            f"{item['name']}: {item['status']} after {item['seconds']}s\n{item.get('tail', '')}"
            for item in results if item["status"] == "failed"))
    return outcome
