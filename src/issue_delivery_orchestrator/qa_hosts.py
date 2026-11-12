"""Where a fresh QA conversation lives: a Superset terminal, a T3 thread or a Paseo agent."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .cloud_launch import _LIMIT, _json_output
from .errors import RunBlocked
from .state import run_root
from .superset_launch import (
    accept_folder_trust,
    agent_state,
    agent_transcript,
    superset_agent_id,
    superset_cli,
    validate_model,
)
from .util import run


# QA replays a fixed script, so by default it runs on a cheaper model than the implementer.
SUPERSET_QA_DEFAULT = ("sonnet", "high")
SUPERSET_SMALL_LANE_QA_DEFAULT = ("sonnet", "medium")
SUPERSET_QA_AGENT = "claude"
SESSION_FILE = Path("validation") / "qa-session.json"


def claude_alias(model: str | None) -> str | None:
    """Specs may name a Conductor model (sonnet-5-5-1m); Claude Code takes the alias (sonnet[1m])."""
    if not model:
        return model
    match = re.match(r"^(haiku|sonnet|opus|fable)-[\d-]+?(-1m)?$", model)
    if not match:
        return model
    return match.group(1) + ("[1m]" if match.group(2) else "")


class SupersetQaHost:
    """The QA agent runs in its own terminal of the same Superset workspace, on this machine."""

    name = "superset"
    location = ["--local"]

    def __init__(self, workspace: str | None):
        self.workspace = workspace or os.environ.get("SUPERSET_WORKSPACE_ID", "").strip()
        if not self.workspace:
            raise RunBlocked("Run qa-session inside the Superset workspace or pass --workspace")
        self.cli = superset_cli()

    def _terminals(self) -> list[dict[str, Any]]:
        listing = _json_output(run([self.cli, "terminals", "list", *self.location, "--workspace",
                                    self.workspace, "--json"]).stdout)
        return [item for item in listing.get("sessions") or [] if isinstance(item, dict)]

    def caller(self, implementation_session: str | None, exclude: tuple[str, ...] = ()) -> str:
        live = [str(item["terminalId"]) for item in self._terminals()
                if not item.get("exited") and item.get("terminalId") not in exclude]
        if implementation_session:
            if implementation_session not in live:
                raise RunBlocked("Implementation terminal is not live in this Superset workspace")
            return implementation_session
        if len(live) != 1:
            raise RunBlocked(
                "Cannot tell which terminal runs the implementation; pass --implementation-session "
                f"with one of: {', '.join(live) or 'none'}"
            )
        return live[0]

    def configuration(self, agent, model, effort, caller, spec, lane):
        source = "explicit" if model else ("spec" if spec[0] else "default")
        if not model:
            model, effort = spec if spec[0] else (
                SUPERSET_SMALL_LANE_QA_DEFAULT if lane == "small" else SUPERSET_QA_DEFAULT)
        model = claude_alias(model)
        validate_model(model, effort, "QA")
        agent_id = superset_agent_id(self.cli, self.location, agent or SUPERSET_QA_AGENT)
        return {"agent": agent_id, "model": model, "effort": effort or ""}, source

    def status(self, session_id: str) -> str | None:
        return agent_state(self.cli, self.location, self.workspace, session_id)

    def create(self, state: dict[str, Any], name: str, prompt: str, configuration: dict[str, str]) -> str:
        arguments = [self.cli, "agents", "create", *self.location, "--workspace", self.workspace,
                     "--agent", configuration["agent"], "--model", configuration["model"]]
        if configuration["effort"]:
            arguments += ["--effort", configuration["effort"]]
        created = _json_output(run([*arguments, "--prompt", prompt, "--json"]).stdout)
        terminal = created.get("sessionId") or created.get("terminalId")
        if not terminal:
            raise RunBlocked("Superset did not return the QA terminal; inspect the workspace before retrying")
        # The QA agent cannot see its own terminal ID, and qa-results.json must carry it.
        path = run_root(Path(state["worktree"]), state["runId"]) / SESSION_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"reviewSessionId": terminal, "name": name}, indent=2) + "\n")
        accept_folder_trust(self.cli, self.location, self.workspace, str(terminal))
        return str(terminal)

    def receipt(self) -> str:
        return f"superset-workspace:{self.workspace}"

    def send(self, session_id: str, message: str) -> None:
        run([self.cli, "terminals", "send", *self.location, "--workspace", self.workspace,
             "--terminal", session_id, "--text", message])

    def stop_reason(self, session_id: str) -> dict[str, Any] | None:
        tail = agent_transcript(self.cli, self.location, self.workspace, session_id)[-4000:]
        match = _LIMIT.search(tail)
        if not match:
            return None
        return {"reason": "usage-limit", "resets": match.group(1).strip() if match.group(1) else None}

    def wait(self, session_id: str, timeout_seconds: float, poll_seconds: float) -> str | None:
        result = run([self.cli, "agents", "wait", *self.location, "--workspace", self.workspace,
                      "--terminal", session_id, "--until", "settled", "--timeout",
                      str(int(timeout_seconds)), "--json"], check=False)
        state = _json_output(result.stdout).get("state") or self.status(session_id)
        if state == "working":
            raise RunBlocked("The QA agent is still working; run qa-wait again")
        return state



T3_EFFORTS = ("low", "medium", "high", "xhigh", "max")
T3_LAUNCH_FILE = Path("validation") / "qa-launch.json"
T3_PROMPT_FILE = Path("validation") / "qa-prompt.md"


class QaLaunchRequired(Exception):
    """T3 opens a chat in an existing worktree only through its agents' t3_thread_launch tool."""

    def __init__(self, request: dict[str, Any]):
        super().__init__("The calling T3 agent must launch the QA thread")
        self.request = request


def t3_cli() -> str:
    for candidate in (shutil.which("t3"), Path.home() / ".local/bin/t3", Path.home() / ".t3/bin/t3"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RunBlocked("The t3 CLI is not installed on this host")


def t3_model(provider: str, model: str) -> str:
    """Specs and runs name models as sonnet or sonnet-5-5-1m; T3 takes its own slug."""
    alias = (claude_alias(model) or "").replace("[1m]", "")
    cache = Path(os.environ.get("T3CODE_HOME") or Path.home() / ".t3") / "caches" / f"{provider}.json"
    try:
        models = json.loads(cache.read_text()).get("models") or []
    except (OSError, ValueError):
        models = []
    for item in models:
        if alias == item.get("slug") or alias in (item.get("aliases") or []):
            return str(item["slug"])
    if models:
        slugs = ", ".join(str(item.get("slug")) for item in models)
        raise RunBlocked(f"QA model {model} is not offered by T3 provider {provider}: {slugs}")
    return alias


class T3QaHost:
    """The QA agent runs in its own T3 thread bound to the implementer's worktree."""

    name = "t3"

    def __init__(self, workspace: str | None, launched: str | None = None):
        self.workspace = workspace or os.environ.get("T3CODE_THREAD_ID", "").strip()
        if not self.workspace:
            raise RunBlocked("Run qa-session inside the T3 thread of the implementation or pass "
                             "--workspace with its thread ID")
        self.cli = t3_cli()
        self.launched = launched

    def _thread(self, thread_id: str) -> dict[str, Any]:
        shown = _json_output(run([self.cli, "thread", "show", thread_id, "--messages", "3",
                                  "--json"]).stdout)
        if not isinstance(shown.get("thread"), dict):
            raise RunBlocked(f"T3 does not know thread {thread_id}")
        return shown

    def caller(self, implementation_session: str | None, exclude: tuple[str, ...] = ()) -> str:
        caller = implementation_session or self.workspace
        if caller in exclude:
            raise RunBlocked("The implementation thread cannot be its own QA thread")
        self._thread(caller)
        return caller

    def configuration(self, agent, model, effort, caller, spec, lane):
        source = "explicit" if model else ("spec" if spec[0] else "default")
        if not model:
            model, effort = spec if spec[0] else (
                SUPERSET_SMALL_LANE_QA_DEFAULT if lane == "small" else SUPERSET_QA_DEFAULT)
        if effort and effort not in T3_EFFORTS:
            raise RunBlocked(f"QA effort {effort} is not one of {', '.join(T3_EFFORTS)}")
        provider = agent or self._thread(caller)["thread"].get("providerInstanceId")
        if not provider:
            raise RunBlocked("Cannot tell the implementation thread's T3 provider; pass --agent")
        return {"agent": str(provider), "model": t3_model(str(provider), model),
                "effort": effort or ""}, source

    def status(self, session_id: str) -> str | None:
        thread = self._thread(session_id)["thread"]
        return "working" if thread.get("activeRunId") else thread.get("status")

    def create(self, state: dict[str, Any], name: str, prompt: str, configuration: dict[str, str]) -> str:
        root = run_root(Path(state["worktree"]), state["runId"])
        if not self.launched:
            (root / T3_PROMPT_FILE).parent.mkdir(parents=True, exist_ok=True)
            (root / T3_PROMPT_FILE).write_text(prompt)
            request = {"title": name, "providerInstanceId": configuration["agent"],
                       "model": configuration["model"], "effort": configuration["effort"],
                       "workspace": {"type": "existing_worktree", "worktreePath": state["worktree"]},
                       "messageFile": str(root / T3_PROMPT_FILE)}
            (root / T3_LAUNCH_FILE).write_text(json.dumps(request, indent=2) + "\n")
            raise QaLaunchRequired({**request, "requestFile": str(root / T3_LAUNCH_FILE)})
        thread = self._thread(self.launched)["thread"]
        expected = Path(state["worktree"]).expanduser().resolve()
        if Path(str(thread.get("worktreePath") or "")).expanduser().resolve() != expected:
            raise RunBlocked("The QA thread does not run in this run's worktree; launch it with "
                             "existing_worktree")
        if thread.get("title") != name:
            raise RunBlocked(f"The QA thread must be titled {name!r}; launch it from qa-launch.json")
        if (thread.get("providerInstanceId"), thread.get("model")) != (
                configuration["agent"], configuration["model"]):
            raise RunBlocked("The QA thread's provider/model does not match the requested configuration")
        path = root / SESSION_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"reviewSessionId": self.launched, "name": name}, indent=2) + "\n")
        return self.launched

    def receipt(self) -> str:
        return f"t3-thread:{self.workspace}"

    def send(self, session_id: str, message: str) -> None:
        run([self.cli, "thread", "send", session_id, "-"], input_text=message)

    def stop_reason(self, session_id: str) -> dict[str, Any] | None:
        messages = self._thread(session_id).get("messages") or []
        tail = "\n".join(str(item.get("text") or "") for item in messages if isinstance(item, dict))[-4000:]
        match = _LIMIT.search(tail)
        if not match:
            return None
        return {"reason": "usage-limit", "resets": match.group(1).strip() if match.group(1) else None}

    def wait(self, session_id: str, timeout_seconds: float, poll_seconds: float) -> str | None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = self.status(session_id)
            if state != "working":
                return state
            if time.monotonic() >= deadline:
                raise RunBlocked("The QA agent is still working; run qa-wait again")
            time.sleep(poll_seconds)


PASEO_QA_PROVIDER = "claude-dev5"
PASEO_WORKING = ("initializing", "running")


def paseo_cli() -> str:
    for candidate in (shutil.which("paseo"), "/usr/bin/paseo", Path.home() / ".local/bin/paseo"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RunBlocked("The paseo CLI is not installed on this host")


def paseo_model(cli: str, provider: str, model: str, effort: str | None,
                host: str | None = None) -> tuple[str, str]:
    """Specs name models as sonnet or sonnet-5-5-1m; Paseo takes the provider's model ID."""
    remote = ["--host", host] if host else []
    listing = _json_output(run([cli, "provider", "models", provider, *remote, "--json"]).stdout).get("data")
    models = [item for item in listing or [] if isinstance(item, dict)]
    if not models:
        raise RunBlocked(f"Paseo provider {provider} lists no models; check `paseo provider ls`")
    alias = (claude_alias(model) or "").replace("[1m]", "")
    chosen = next((item for item in models if item.get("id") == model), None) or next(
        (item for item in models if str(item.get("id", "")).startswith(f"claude-{alias}-")), None)
    if not chosen:
        ids = ", ".join(str(item.get("id")) for item in models)
        raise RunBlocked(f"Model {model} is not offered by Paseo provider {provider}: {ids}")
    options = chosen.get("thinkingOptionIds") or []
    if effort and effort not in options:
        raise RunBlocked(f"Effort {effort} is not offered by {chosen['id']}: {', '.join(options)}")
    return str(chosen["id"]), effort or ""


class PaseoQaHost:
    """The QA agent runs as another Paseo agent in the implementer's workspace and worktree."""

    name = "paseo"

    def __init__(self, workspace: str | None):
        self.workspace = workspace or os.environ.get("PASEO_AGENT_ID", "").strip()
        if not self.workspace:
            raise RunBlocked("Run qa-session inside the Paseo agent of the implementation or pass "
                             "--workspace with its agent ID")
        self.cli = paseo_cli()

    def _agent(self, agent_id: str) -> dict[str, Any]:
        result = run([self.cli, "inspect", agent_id, "--json"], check=False)
        shown = _json_output(result.stdout) if result.returncode == 0 else {}
        if not isinstance(shown, dict) or not shown.get("Id"):
            raise RunBlocked(f"Paseo does not know agent {agent_id}")
        return shown

    def caller(self, implementation_session: str | None, exclude: tuple[str, ...] = ()) -> str:
        caller = implementation_session or self.workspace
        if caller in exclude:
            raise RunBlocked("The implementation agent cannot be its own QA agent")
        self._agent(caller)
        return caller

    def configuration(self, agent, model, effort, caller, spec, lane):
        source = "explicit" if model else ("spec" if spec[0] else "default")
        if not model:
            model, effort = spec if spec[0] else (
                SUPERSET_SMALL_LANE_QA_DEFAULT if lane == "small" else SUPERSET_QA_DEFAULT)
        provider = agent or PASEO_QA_PROVIDER
        model_id, thinking = paseo_model(self.cli, provider, model, effort)
        return {"agent": provider, "model": model_id, "effort": thinking}, source

    def status(self, session_id: str) -> str | None:
        status = self._agent(session_id).get("Status")
        return "working" if status in PASEO_WORKING else status

    def create(self, state: dict[str, Any], name: str, prompt: str, configuration: dict[str, str]) -> str:
        arguments = [self.cli, "run", "--no-wait", "--json", "--title", name,
                     "--provider", configuration["agent"], "--model", configuration["model"],
                     "--mode", "bypassPermissions", "--cwd", str(state["worktree"]),
                     "--label", "issue-delivery=qa", "--label", f"run={state['runId']}"]
        if configuration["effort"]:
            arguments += ["--thinking", configuration["effort"]]
        created = _json_output(run([*arguments, prompt]).stdout)
        agent_id = created.get("agentId") if isinstance(created, dict) else None
        if not agent_id:
            raise RunBlocked("Paseo did not return the QA agent; check the workspace before retrying")
        agent = self._agent(str(agent_id))
        expected = Path(state["worktree"]).expanduser().resolve()
        if Path(str(agent.get("Cwd") or "")).expanduser().resolve() != expected:
            raise RunBlocked("The QA agent does not run in this run's worktree")
        path = run_root(Path(state["worktree"]), state["runId"]) / SESSION_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"reviewSessionId": agent_id, "name": name}, indent=2) + "\n")
        return str(agent_id)

    def receipt(self) -> str:
        return f"paseo-agent:{self.workspace}"

    def send(self, session_id: str, message: str) -> None:
        run([self.cli, "send", session_id, "--no-wait", "--prompt", message])

    def stop_reason(self, session_id: str) -> dict[str, Any] | None:
        result = run([self.cli, "logs", session_id, "--tail", "20"], check=False)
        match = _LIMIT.search((result.stdout or "")[-4000:])
        if not match:
            return None
        return {"reason": "usage-limit", "resets": match.group(1).strip() if match.group(1) else None}

    def wait(self, session_id: str, timeout_seconds: float, poll_seconds: float) -> str | None:
        run([self.cli, "wait", session_id, "--timeout", str(int(timeout_seconds)), "--json"], check=False)
        state = self.status(session_id)
        if state == "working":
            raise RunBlocked("The QA agent is still working; run qa-wait again")
        return state
