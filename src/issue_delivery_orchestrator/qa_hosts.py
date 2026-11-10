"""Where a fresh QA conversation lives: a Conductor workspace or a Superset workspace."""
from __future__ import annotations

import json
import os
import re
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

