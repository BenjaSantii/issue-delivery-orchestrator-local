"""Serve an HTML prototype from a Paseo workspace and anchor the approved screens in Linear."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cloud_launch import _json_output
from .errors import RunBlocked
from .util import run

SCRIPT = "prototype"
ENTRY = "index.html"


def prototype_dir(worktree: Path, identifier: str, runtime_root: str, namespace: str) -> Path:
    return worktree / runtime_root / namespace / "prototype" / identifier


def _exclude_locally(worktree: Path, name: str) -> None:
    common = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=worktree).stdout.strip()
    exclude = Path(common) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude.read_text().splitlines() if exclude.is_file() else []
    if f"/{name}" not in lines:
        exclude.write_text("\n".join([*lines, f"/{name}"]) + "\n")


def _register_script(worktree: Path, directory: Path) -> None:
    config = worktree / "paseo.json"
    tracked = run(["git", "ls-files", "--error-unmatch", "paseo.json"], cwd=worktree, check=False).returncode == 0
    if not tracked:
        _exclude_locally(worktree, "paseo.json")
    data = json.loads(config.read_text()) if config.is_file() else {}
    scripts = data.setdefault("scripts", {})
    relative = directory.relative_to(worktree)
    scripts[SCRIPT] = {
        "type": "service",
        "command": f"python3 -m http.server $PASEO_PORT --bind 127.0.0.1 --directory '{relative}'",
    }
    config.write_text(json.dumps(data, indent=2) + "\n")


def _workspace(cli: str, worktree: Path) -> str:
    listing = _json_output(run([cli, "workspace", "ls", "--json"]).stdout).get("data") or []
    wanted = worktree.resolve()
    for item in listing:
        if item.get("cwd") and Path(item["cwd"]).resolve() == wanted:
            return str(item["workspaceId"])
    raise RunBlocked(f"No Paseo workspace runs in {worktree}; open the worktree in Paseo first")


def _scripts(cli: str, workspace: str) -> list[dict[str, Any]]:
    return _json_output(run([cli, "script", "ls", "--workspace", workspace, "--json"]).stdout).get("data") or []


def viewer_url(proxy_url: str) -> str:
    """The browser runs on the user's machine; a tunnel there may publish the daemon on another port."""
    port = os.environ.get("ISSUE_DELIVERY_PREVIEW_PORT", "").strip()
    return re.sub(r":\d+(?=/|$)", f":{port}", proxy_url, count=1) if port else proxy_url


def serve(worktree: Path, directory: Path, cli: str) -> dict[str, Any]:
    if not os.environ.get("PASEO_AGENT_ID", "").strip():
        raise RunBlocked("prototype-serve publishes through Paseo; run it inside a Paseo agent")
    if not (directory / ENTRY).is_file():
        raise RunBlocked(f"Write the prototype to {directory / ENTRY} before serving it")
    workspace = _workspace(cli, worktree)
    _register_script(worktree, directory)
    current = next((item for item in _scripts(cli, workspace) if item.get("scriptName") == SCRIPT), {})
    if current.get("lifecycle") != "running":
        run([cli, "script", "start", SCRIPT, "--workspace", workspace, "--json"])
        current = next((item for item in _scripts(cli, workspace) if item.get("scriptName") == SCRIPT), {})
    proxy = current.get("proxyUrl") or current.get("localProxyUrl")
    if not proxy:
        raise RunBlocked("Paseo did not return a URL for the prototype service; check `paseo script ls`")
    return {"entry": str(directory / ENTRY), "proxyUrl": proxy, "url": viewer_url(proxy) + "/",
            "lifecycle": current.get("lifecycle"),
            "next": "Open `url` for the user with browser_new_tab and tell them which tab to look at"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_body(identifier: str, sha: str, approval: str, html_url: str,
               images: list[tuple[str, str]], approved_at: str) -> str:
    lines = [
        f"Prototype ID: PROTO-{identifier}",
        "Status: approved",
        f"SHA-256: {sha}",
        f"Approved at: {approved_at}",
        f"Approval: {approval}",
        "",
        "# Prototipo aprobado",
        "",
        "Pantallas que el usuario aprobó. Linear no muestra el HTML: descárgalo y ábrelo en un "
        "navegador. El SHA-256 identifica la versión aprobada.",
        "",
        f"HTML: [Descargar {ENTRY}]({html_url})",
    ]
    for name, url in images:
        lines += ["", f"![{name}]({url})"]
    return "\n".join(lines)


def publish(linear: Any, identifier: str, directory: Path, approval: str, marker_prefix: str) -> dict[str, Any]:
    from .linear import extract_marker_block, upsert_marker_block

    entry = directory / ENTRY
    if not entry.is_file():
        raise RunBlocked(f"No prototype at {entry}")
    if not approval.strip():
        raise RunBlocked("Pass the user's approval words with --approval")
    images = sorted(directory.glob("*.png"))
    if not images:
        raise RunBlocked("Capture the screens the user approved as PNG files in the prototype "
                         "directory (for example antes.png and despues.png) before publishing")
    sha = digest(entry)
    issue = linear.issue(identifier)
    html_url = linear.upload_file(entry)
    uploaded = [(path.stem, linear.upload_file(path)) for path in images]
    approved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = block_body(issue.identifier, sha, approval.strip(), html_url, uploaded, approved_at)
    linear.update_description(issue.id, upsert_marker_block(
        issue.description, marker_prefix=marker_prefix, block="prototype", body=body))
    verified = extract_marker_block(linear.issue(identifier).description,
                                    marker_prefix=marker_prefix, block="prototype")
    if f"SHA-256: {sha}" not in (verified or ""):
        raise RunBlocked("Linear prototype block verification failed")
    (directory / "approval.json").write_text(json.dumps(
        {"sha256": sha, "approval": approval.strip(), "approvedAt": approved_at,
         "html": html_url, "images": dict(uploaded)}, indent=2) + "\n")
    return {"issue": issue.identifier, "prototypeId": f"PROTO-{issue.identifier}", "sha256": sha,
            "html": html_url, "images": dict(uploaded), "published": True}
