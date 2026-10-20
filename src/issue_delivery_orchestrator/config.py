from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import OrchestrationError


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = PLUGIN_ROOT / "profiles" / "turboshop.json"
DEFAULT_CONFIG_HOME = Path.home() / ".config" / "issue-delivery-orchestrator"


@dataclass(frozen=True)
class Settings:
    profile_name: str
    product_name: str
    profile_path: Path
    env_file: Path | None
    repository: Path | None
    default_base_branch: str
    pr_target_branch: str
    evidence_branch: str
    runtime_root: str
    runtime_namespace: str
    runtime_init_command: tuple[str, ...]
    runtime_cleanup_command: tuple[str, ...]
    linear_expected_email: str
    github_expected_login: str
    linear_keychain_service: str
    bot_names: tuple[str, ...]
    blocker_bots: tuple[str, ...]
    automated_signatures: tuple["AutomatedSignature", ...]
    maximum_review_rounds: int
    quiet_seconds: int
    maximum_wait_seconds: int
    poll_seconds: int
    linear_marker_prefix: str
    linear_ui_section: str
    slicing: "SlicingSettings"
    linear_comments_enabled: bool
    browser_binary: str
    codex_worktree_roots: tuple[str, ...]
    superset_worktree_roots: tuple[str, ...]
    linear_self_assignment_labels: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["automated_signatures"] = [
            signature.name for signature in self.automated_signatures
        ]
        payload["profile_path"] = str(self.profile_path)
        payload["env_file"] = str(self.env_file) if self.env_file else None
        payload["repository"] = str(self.repository) if self.repository else None
        return payload


def settings() -> Settings:
    env_file = _load_environment()
    profile_path = Path(
        os.environ.get("ISSUE_DELIVERY_PROFILE", str(DEFAULT_PROFILE))
    ).expanduser().resolve()
    try:
        data = json.loads(profile_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise OrchestrationError(
            f"Could not load issue-delivery profile {profile_path}: {error}"
        ) from error
    if not isinstance(data, dict):
        raise OrchestrationError(f"Profile must contain a JSON object: {profile_path}")

    git = _object(data, "git")
    runtime = _object(data, "runtime")
    identity = _object(data, "identity")
    review = _object(data, "review")
    linear = _object(data, "linear")
    repository_value = os.environ.get("ISSUE_DELIVERY_REPOSITORY", "").strip()
    repository = Path(repository_value).expanduser().resolve() if repository_value else None
    runtime_root = _required_string(runtime, "root")
    runtime_namespace = _required_string(runtime, "namespace")
    if Path(runtime_root).is_absolute() or ".." in Path(runtime_root).parts:
        raise OrchestrationError("runtime.root must be a safe relative path")
    if Path(runtime_namespace).is_absolute() or ".." in Path(runtime_namespace).parts:
        raise OrchestrationError("runtime.namespace must be a safe relative path")

    return Settings(
        profile_name=_required_string(data, "name"),
        product_name=_required_string(data, "productName"),
        profile_path=profile_path,
        env_file=env_file,
        repository=repository,
        default_base_branch=_required_string(git, "defaultBase"),
        pr_target_branch=_required_string(git, "prTarget"),
        evidence_branch=_required_string(git, "evidenceBranch"),
        runtime_root=runtime_root,
        runtime_namespace=runtime_namespace,
        runtime_init_command=_string_tuple(runtime, "initCommand"),
        runtime_cleanup_command=_string_tuple(runtime, "cleanupCommand"),
        linear_expected_email=(
            os.environ.get("LINEAR_EXPECTED_EMAIL", "").strip()
            or str(identity.get("linearExpectedEmail") or "").strip()
        ),
        github_expected_login=(
            os.environ.get("GITHUB_EXPECTED_LOGIN", "").strip()
            or str(identity.get("githubExpectedLogin") or "").strip()
        ),
        linear_keychain_service=(
            os.environ.get("LINEAR_KEYCHAIN_SERVICE", "").strip()
            or _required_string(identity, "linearKeychainService")
        ),
        bot_names=tuple(name.lower() for name in _string_tuple(review, "botNames")),
        blocker_bots=_blocker_bots(review),
        automated_signatures=_automated_signatures(review),
        maximum_review_rounds=_positive_integer(review, "maximumRounds"),
        quiet_seconds=_positive_integer(review, "quietSeconds"),
        maximum_wait_seconds=_positive_integer(review, "maximumWaitSeconds"),
        poll_seconds=_positive_integer(review, "pollSeconds"),
        linear_marker_prefix=_required_string(linear, "markerPrefix"),
        linear_ui_section=_required_string(linear, "uiSection"),
        linear_self_assignment_labels=(
            _string_tuple(linear, "selfAssignmentLabels")
            if linear.get("selfAssignmentLabels") != [] and "selfAssignmentLabels" in linear
            else ()
        ),
        slicing=_slicing(data.get("slicing")),
        linear_comments_enabled=_environment_boolean(
            "LINEAR_COMMENTS_ENABLED",
            default=True,
        ),
        browser_binary=os.environ.get("ISSUE_DELIVERY_BROWSER", "").strip(),
        codex_worktree_roots=_environment_paths(
            "ISSUE_DELIVERY_CODEX_WORKTREE_ROOTS"
        ),
        superset_worktree_roots=_environment_paths(
            "ISSUE_DELIVERY_SUPERSET_WORKTREE_ROOTS"
        ),
    )


def _load_environment() -> Path | None:
    explicit = os.environ.get("ISSUE_DELIVERY_ENV_FILE", "").strip()
    config_home = Path(
        os.environ.get("ISSUE_DELIVERY_CONFIG_HOME", str(DEFAULT_CONFIG_HOME))
    ).expanduser()
    candidates = [Path(explicit).expanduser()] if explicit else [
        config_home / ".env",
        PLUGIN_ROOT / ".env",
    ]
    path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
    if not path:
        return None
    for number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise OrchestrationError(f"Invalid .env entry at {path}:{number}")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return path


def _object(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise OrchestrationError(f"Profile field {key} must be an object")
    return value


@dataclass(frozen=True)
class SlicingSettings:
    max_product_lines: int = 4000
    estimate_deviation: float = 1.5
    max_stack_depth: int = 3


def _slicing(raw: Any) -> SlicingSettings:
    if raw is None:
        return SlicingSettings()
    if not isinstance(raw, dict):
        raise OrchestrationError("Profile field slicing must be an object")
    defaults = SlicingSettings()
    deviation = raw.get("estimateDeviation", defaults.estimate_deviation)
    if isinstance(deviation, bool) or not isinstance(deviation, (int, float)) or deviation <= 1:
        raise OrchestrationError("Profile field estimateDeviation must be a number above 1")
    return SlicingSettings(
        max_product_lines=(
            _positive_integer(raw, "maxProductLines")
            if "maxProductLines" in raw
            else defaults.max_product_lines
        ),
        estimate_deviation=float(deviation),
        max_stack_depth=(
            _positive_integer(raw, "maxStackDepth")
            if "maxStackDepth" in raw
            else defaults.max_stack_depth
        ),
    )


@dataclass(frozen=True)
class AutomatedSignature:
    name: str
    pattern: "re.Pattern[str]"


DEFAULT_AUTOMATED_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("codex-review", r"(?im)^\s*#{0,4}\s*codex code review\b"),
    ("claude-review", r"(?im)^\s*#{0,4}\s*claude code review\b"),
    ("review-task-header", r"(?im)^\s*\**task\**\s*:\s*[A-Z][A-Z0-9]+-\d+\b"),
    ("coderabbit", r"(?m)^_[^_\n]*_\s*\|\s*_[^_\n]*_"),
)


def _automated_signatures(review: dict[str, Any]) -> tuple[AutomatedSignature, ...]:
    raw = review.get("automatedSignatures")
    if raw is None:
        entries = DEFAULT_AUTOMATED_SIGNATURES
    else:
        if not isinstance(raw, list):
            raise OrchestrationError("Profile field automatedSignatures must be an array")
        entries = tuple(
            (str(item.get("name") or ""), str(item.get("pattern") or ""))
            for item in raw
            if isinstance(item, dict)
        )
    signatures = []
    for name, pattern in entries:
        if not name or not pattern:
            raise OrchestrationError(
                "Each automatedSignatures entry needs a non-empty name and pattern"
            )
        try:
            signatures.append(AutomatedSignature(name, re.compile(pattern)))
        except re.error as error:
            raise OrchestrationError(
                f"automatedSignatures entry {name} has an invalid pattern: {error}"
            ) from error
    return tuple(signatures)


def _blocker_bots(review: dict[str, Any]) -> tuple[str, ...]:
    """Accept `blockerBots` (array) or the legacy single `blockerBot` string."""
    if "blockerBots" in review:
        return tuple(name.lower() for name in _string_tuple(review, "blockerBots"))
    return (_required_string(review, "blockerBot").lower(),)


def _required_string(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise OrchestrationError(f"Profile field {key} must be a non-empty string")
    return value


def _string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise OrchestrationError(f"Profile field {key} must be a non-empty array")
    items = tuple(str(item).strip() for item in value if str(item).strip())
    if len(items) != len(value):
        raise OrchestrationError(f"Profile field {key} contains an empty value")
    return items


def _positive_integer(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise OrchestrationError(f"Profile field {key} must be a positive integer")
    return value


def _environment_boolean(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise OrchestrationError(
        f"Environment variable {name} must be a boolean value"
    )


def _environment_paths(key: str) -> tuple[str, ...]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return ()
    values = [item.strip() for item in raw.split(os.pathsep)]
    if any(not item for item in values):
        raise OrchestrationError(
            f"{key} must contain non-empty paths separated by {os.pathsep!r}"
        )
    return tuple(str(Path(item).expanduser().resolve()) for item in values)
