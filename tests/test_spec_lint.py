import unittest

from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.spec_lint import (
    assert_express_tickets,
    assert_spec_publishable,
    lint_express_tickets,
    lint_spec,
)

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


    def test_prototype_line_accepts_only_yes_or_no(self):
        self.assertEqual(lint_spec("Prototype: yes\n" + spec(ALL_NO)), [])
        self.assertEqual(lint_spec("Prototype: no\n" + spec(ALL_NO)), [])
        self.assertIn("Unknown Prototype `maybe`; use `yes` or `no`",
                      lint_spec("Prototype: maybe\n" + spec(ALL_NO)))
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


class LaneTests(unittest.TestCase):
    def test_express_lane_accepts_a_low_risk_spec(self):
        self.assertEqual(lint_spec("Lane: express\n" + spec(ALL_NO)), [])

    def test_express_lane_rejects_concurrency_authorization_and_long_scripts(self):
        for risk in ("concurrency", "roles-authorization"):
            rows = ALL_NO.replace(f"| `{risk}` | No |", f"| `{risk}` | Yes |").replace(
                "only reads config | — |", "only reads config | Approval race |")
            with self.subTest(risk=risk):
                self.assertIn(f"Lane express cannot apply {risk}; drop the Lane line",
                              lint_spec("Lane: express\n" + spec(rows)))
        many = "".join(QA_BASIC.replace("QA-001", f"QA-00{n}") for n in range(1, 6))
        self.assertIn("Lane express allows at most 4 QA cases, got 5",
                      lint_spec("Lane: express\n" + spec(ALL_NO, qa=many)))
        self.assertIn("Unknown Lane `fast`; use `small`, `express` or omit the line",
                      lint_spec("Lane: fast\n" + spec(ALL_NO)))

    def test_express_tickets_must_be_one_small_ticket(self):
        ticket = "Ticket ID: TICKET-001\nEstimated size: 60 product lines\n\n# Fix\n"
        self.assertEqual(lint_express_tickets(ticket), [])
        self.assertIn("exactly one ticket, got 2",
                      lint_express_tickets(ticket + ticket.replace("001", "002"))[0])
        self.assertIn("at most 150 estimated product lines, got 220",
                      lint_express_tickets(ticket.replace("60", "220"))[0])
        self.assertIn("numeric `Estimated size`",
                      lint_express_tickets(ticket.replace("60 product lines", "small"))[0])
        two = ticket + ticket.replace("001", "002")
        with self.assertRaisesRegex(RunBlocked, "express lane"):
            assert_express_tickets("Lane: express\n", two)
        assert_express_tickets("Lane: small\n", two)
        assert_express_tickets("Spec ID: SPEC-1\n", two)


if __name__ == "__main__":
    unittest.main()
