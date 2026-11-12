"""Start a Linear issue as a Paseo agent in a new worktree on a Paseo host, from the Grill or delivery."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .cloud_launch import _json_output
from .errors import RunBlocked
from .qa_hosts import paseo_cli, paseo_model
from .t3_launch import needs_grill, t3_launch_message
from .util import run

DEFAULT_PROVIDER = "claude-dev5"
DEFAULT_MODEL = "opus"


def build_paseo_launch(readiness: dict[str, Any], repository: Path, *, host: str, remote_repository: str,
                       base: str, stack_parent: str | None, provider: str, model: str | None,
                       effort: str | None = None, setup: str | None = None,
                       implement_model: str | None = None,
                       qa_model: str | None = None, qa_effort: str | None = None) -> dict[str, Any]:
    if not host or not remote_repository:
        raise RunBlocked("Set ISSUE_DELIVERY_PASEO_HOST and ISSUE_DELIVERY_PASEO_REPO, or pass "
                         "--paseo-host and --paseo-repo")
    if qa_effort and not qa_model:
        raise RunBlocked("--qa-effort requires --qa-model")
    grill = needs_grill(readiness)
    branch = readiness["branch"]
    if run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], cwd=repository,
           check=False).returncode == 0:
        raise RunBlocked(f"{readiness['identifier']} branch {branch} already exists on origin; "
                         "continue its Paseo agent instead of launching again")
    cli = paseo_cli()
    model_id, thinking = paseo_model(cli, provider, model or DEFAULT_MODEL, effort, host=host)
    qa = {"model": qa_model or "", "effort": qa_effort or ""}
    message = t3_launch_message(readiness, grill=grill, base=base, stack_parent=stack_parent,
                                implement_model=implement_model, qa=qa, host="paseo",
                                setup=setup)
    title = f"[{readiness['identifier']}] {readiness['title']}"[:80]
    workspace_base = stack_parent or base
    arguments = [cli, "run", "--host", host, "--no-wait", "--json", "--title", title,
                 "--provider", provider, "--model", model_id, "--mode", "bypassPermissions",
                 "--new-workspace", "worktree", "--worktree-mode", "branch-off",
                 "--new-branch", branch, "--base", f"origin/{workspace_base}",
                 "--cwd", remote_repository]
    if thinking:
        arguments += ["--thinking", thinking]
    return {"arguments": arguments, "message": message, "host": host, "provider": provider,
            "model": model_id, "effort": thinking, "title": title,
            "startsAt": "grill" if grill else "implement",
            "workspaceBase": f"origin/{workspace_base}", "qa": qa}


def create_paseo_agent(launch: dict[str, Any]) -> dict[str, Any]:
    created = _json_output(run([*launch["arguments"], launch["message"]]).stdout)
    if not created.get("agentId"):
        raise RunBlocked("Paseo did not return the new agent; check the host before launching again")
    return created
