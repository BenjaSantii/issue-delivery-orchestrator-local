import unittest

from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.spec_lint import assert_spec_publishable, lint_spec

TESTING = """## Testing Decisions

| Risk owner | Existing evidence | Decision | Lowest sufficient seam | Unique signal | Superseded test |
| --- | --- | --- | --- | --- | --- |
| Approval race | `trip-dispatch.service.spec.ts` | create | Postgres integration | stale stop set rejected | None |
"""

CONSUMERS = """### Consumer inventory

| Changed concept | Consumer | Surface | Handled by |
| --- | --- | --- | --- |
| `slaTiming` | `trip-batch-row.tsx` | web | TICKET-002 |
"""

STATES = """### State model

| State | Entered when | User sees | Allowed actions | Exit |
| --- | --- | --- | --- | --- |
| Saved | ERP confirms | Horario guardado | Continue | — |
| Unknown | Timeout after PUT | Resultado incierto | Retry read | Canonical read matches |
"""


QA_HEADER = (
    "| QA ID | Type | Covers | Surface | Steps | Expected | Evidence | Regression test |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
)
QA_BASIC = "| QA-001 | happy-path | Horarios | UI | Abrir ficha | Se ve el horario | Captura | — |\n"


def spec(rows: str, extra: str = "", qa: str = QA_BASIC) -> str:
    return (
        "## Implementation Decisions\n\nx\n\n"
        "## Risk Catalog\n\n"
        "| Risk category | Applies | Evidence | Coverage |\n"
        "| --- | --- | --- | --- |\n"
        f"{rows}\n{extra}\n{TESTING}\n## QA Script\n\n{QA_HEADER}{qa}\n"
        "## Out of Scope\n\nNone\n"
    )


ALL_NO = "\n".join(
    f"| `{category}` | No | `apps/x/src/y.ts` only reads config | — |"
    for category in (
        "concurrency",
        "external-failure",
        "async-lifecycle",
        "edge-cases",
        "consumers-platforms",
        "roles-authorization",
    )
)


class SpecLintTests(unittest.TestCase):
    def test_accepts_complete_catalog(self):
        self.assertEqual(lint_spec(spec(ALL_NO)), [])

    def test_rejects_missing_catalog_and_wrong_testing_header(self):
        errors = lint_spec("## Testing Decisions\n\n| Risk | Decision |\n| - | - |\n| a | b |\n")
        self.assertTrue(any("Missing section `## Risk Catalog`" in e for e in errors))
        self.assertTrue(any("Testing Decisions" in e for e in errors))
        with self.assertRaises(RunBlocked):
            assert_spec_publishable("no spec")

    def test_rejects_missing_category_and_unevidenced_no(self):
        rows = ALL_NO.replace(
            "| `roles-authorization` | No | `apps/x/src/y.ts` only reads config | — |", ""
        ).replace(
            "| `edge-cases` | No | `apps/x/src/y.ts` only reads config |",
            "| `edge-cases` | No | no aplica |",
        )
        errors = lint_spec(spec(rows))
        self.assertTrue(any("missing categories: roles-authorization" in e for e in errors))
        self.assertTrue(any("`edge-cases` needs repository evidence" in e for e in errors))

    def test_applied_risks_require_coverage_inventory_and_state_model(self):
        rows = ALL_NO.replace(
            "| `external-failure` | No | `apps/x/src/y.ts` only reads config | — |",
            "| `external-failure` | Yes | `workshops.gateway.ts` calls ERP | — |",
        ).replace(
            "| `consumers-platforms` | No | `apps/x/src/y.ts` only reads config | — |",
            "| `consumers-platforms` | Yes | `rg slaTiming apps` | Approval race |",
        )
        errors = lint_spec(spec(rows))
        self.assertTrue(any("`external-failure` applies, so Coverage" in e for e in errors))
        self.assertTrue(any("### Consumer inventory" in e for e in errors))
        self.assertTrue(any("### State model" in e for e in errors))

        fixed = rows.replace(
            "| `workshops.gateway.ts` calls ERP | — |",
            "| `workshops.gateway.ts` calls ERP | Approval race |",
        )
        covered = (
            QA_BASIC
            + "| QA-002 | failure | `external-failure` | API | ERP caído | 503 sin perder captura | Salida | — |\n"
            + "| QA-003 | edge-case | `consumers-platforms` | UI | Abrir lote | slaTiming visible | Captura | — |\n"
        )
        self.assertEqual(lint_spec(spec(fixed, CONSUMERS + "\n" + STATES, covered)), [])

        no_unknown = STATES.replace(
            "| Unknown | Timeout after PUT | Resultado incierto | Retry read | Canonical read matches |\n",
            "",
        )
        errors = lint_spec(spec(fixed, CONSUMERS + "\n" + no_unknown, covered))
        self.assertTrue(any("unknown/uncertain outcome state" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
