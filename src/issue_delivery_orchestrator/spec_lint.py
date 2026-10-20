"""Structural checks for a spec before it is published to Linear.

The risk catalog forces every spec to state, with repository evidence, whether each
recurring blocker category applies. Categories come from the blockers that most
often reached PR review: they are cheap to model in the spec and expensive to
discover one review round at a time.
"""

from __future__ import annotations

import re

from .errors import RunBlocked

RISK_CATEGORIES = (
    "concurrency",
    "external-failure",
    "async-lifecycle",
    "edge-cases",
    "consumers-platforms",
    "roles-authorization",
)
RISK_CATALOG_HEADER = ("Risk category", "Applies", "Evidence", "Coverage")
TESTING_DECISIONS_HEADER = (
    "Risk owner",
    "Existing evidence",
    "Decision",
    "Lowest sufficient seam",
    "Unique signal",
    "Superseded test",
)
CONSUMER_INVENTORY_HEADER = ("Changed concept", "Consumer", "Surface", "Handled by")
STATE_MODEL_HEADER = ("State", "Entered when", "User sees", "Allowed actions", "Exit")
_EMPTY = {"", "-", "—", "n/a", "na", "none", "ninguno", "tbd", "por confirmar"}
_UNCERTAIN = re.compile(r"(?i)\b(unknown|uncertain|incierto|desconocido)\b")
_EVIDENCE = re.compile(r"`[^`]+`|\b[\w.-]+/[\w./-]+|\b\w+\.(ts|tsx|js|py|sql|json)\b")


def _section(markdown: str, heading: str) -> str | None:
    level = len(heading) - len(heading.lstrip("#"))
    pattern = re.compile(
        rf"(?ms)^{re.escape(heading)}[ \t]*\n(.*?)(?=^#{{1,{level}}}[ \t]+\S|\Z)"
    )
    match = pattern.search(markdown)
    return match.group(1) if match else None


def _table(section: str) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    rows: list[tuple[str, ...]] = []
    for line in section.splitlines():
        if line.strip().startswith("|"):
            rows.append(tuple(cell.strip() for cell in line.strip().strip("|").split("|")))
        elif rows:
            break
    if not rows:
        return (), []
    body = [row for row in rows[1:] if not all(set(cell) <= set("-: ") for cell in row)]
    return rows[0], body


def _require_table(
    markdown: str, heading: str, header: tuple[str, ...], errors: list[str]
) -> list[tuple[str, ...]]:
    section = _section(markdown, heading)
    if section is None:
        errors.append(f"Missing section `{heading}`")
        return []
    found, rows = _table(section)
    if found != header:
        errors.append(f"`{heading}` table header must be: | {' | '.join(header)} |")
        return []
    if not rows:
        errors.append(f"`{heading}` table has no rows")
    return rows


def _blank(value: str) -> bool:
    return value.strip().strip("`*_").lower() in _EMPTY


def lint_spec(markdown: str) -> list[str]:
    from .qa_script import lint_qa_script

    errors: list[str] = []
    _require_table(markdown, "## Testing Decisions", TESTING_DECISIONS_HEADER, errors)
    rows = _require_table(markdown, "## Risk Catalog", RISK_CATALOG_HEADER, errors)

    applies: dict[str, bool] = {}
    for row in rows:
        if len(row) != len(RISK_CATALOG_HEADER):
            errors.append(f"Risk Catalog row has {len(row)} cells: {' | '.join(row)}")
            continue
        category = row[0].strip("` ").lower()
        if category not in RISK_CATEGORIES:
            errors.append(f"Unknown risk category `{row[0]}`")
            continue
        answer = row[1].strip().lower()
        if answer not in {"yes", "no"}:
            errors.append(f"`{category}` Applies must be Yes or No, got `{row[1]}`")
            continue
        applies[category] = answer == "yes"
        if _blank(row[2]) or not _EVIDENCE.search(row[2]):
            errors.append(
                f"`{category}` needs repository evidence (path, symbol or command in backticks)"
            )
        if applies[category] and _blank(row[3]):
            errors.append(f"`{category}` applies, so Coverage must name its Testing Decisions row")

    missing = [category for category in RISK_CATEGORIES if category not in applies]
    if rows and missing:
        errors.append(f"Risk Catalog is missing categories: {', '.join(missing)}")

    if applies.get("consumers-platforms"):
        _require_table(
            markdown, "### Consumer inventory", CONSUMER_INVENTORY_HEADER, errors
        )
    if applies.get("external-failure") or applies.get("concurrency"):
        state_rows = _require_table(
            markdown, "### State model", STATE_MODEL_HEADER, errors
        )
        if applies.get("external-failure") and state_rows and not any(
            _UNCERTAIN.search(row[0]) for row in state_rows
        ):
            errors.append(
                "`### State model` must include an unknown/uncertain outcome state "
                "because external-failure applies"
            )
    errors += lint_qa_script(markdown)
    return errors


def assert_spec_publishable(markdown: str) -> None:
    errors = lint_spec(markdown)
    if errors:
        raise RunBlocked(
            "Spec is not publishable:\n- " + "\n- ".join(errors)
        )
