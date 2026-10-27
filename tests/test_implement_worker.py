import unittest
from pathlib import Path
from unittest.mock import patch

import test_qa_script as qa_examples
from issue_delivery_orchestrator import implement_worker
from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.spec_lint import lint_spec

TICKETS = """Ticket ID: TICKET-001
Parent Spec: SPEC-LOG-1

# Backend

Hacer el endpoint.

---

Ticket ID: TICKET-002
Parent Spec: SPEC-LOG-1

# App

Mostrar el reproductor.
"""


class ImplementWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = qa_examples.ResultsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture.state
        self.state.update({"issue": {"identifier": "LOG-1"}, "base": "test",
                           "adoptedHead": self.fixture.head})
        tickets = self.fixture.root / "planning" / "tickets.md"
        tickets.write_text(TICKETS)
        self.state["artifacts"]["tickets"] = self.fixture.relative(tickets)
        saving = patch("issue_delivery_orchestrator.implement_worker.save_state")
        saving.start()
        self.addCleanup(saving.stop)

    def test_ticket_block_isolates_one_ticket_without_separator(self):
        block = implement_worker.ticket_block(TICKETS, "TICKET-001")
        self.assertIn("Hacer el endpoint.", block)
        self.assertNotIn("TICKET-002", block)
        self.assertFalse(block.endswith("---"))
        self.assertIn("Mostrar el reproductor.", implement_worker.ticket_block(TICKETS, "TICKET-002"))
        with self.assertRaisesRegex(RunBlocked, "TICKET-009"):
            implement_worker.ticket_block(TICKETS, "TICKET-009")

    def test_package_carries_contract_and_accepted_work_but_no_chat_history(self):
        qa_examples.git(self.fixture.worktree, "commit", "--allow-empty", "-m", "feat: TICKET-001 endpoint")
        self.state["implementModel"] = "sonnet"
        result = implement_worker.package(self.state, "TICKET-002")
        brief = Path(result["package"]).read_text()
        self.assertEqual(result["model"], "sonnet")
        self.assertEqual(result["spawn"]["claude"], {"subagent_type": "general-purpose", "model": "sonnet"})
        self.assertEqual(result["spawn"]["codex"]["fork_turns"], "none")
        self.assertIn("Mostrar el reproductor.", brief)
        self.assertNotIn("Hacer el endpoint.", brief)
        self.assertIn("feat: TICKET-001 endpoint", brief)
        self.assertIn(self.state["artifacts"]["spec"], brief)
        self.assertIn("issue-delivery-implement", brief)
        self.assertNotIn("Finding a reparar", brief)

    def test_each_repair_gets_a_new_attempt_with_its_finding(self):
        finding = self.fixture.root / "validation" / "qa" / "QA-002-finding.txt"
        finding.write_text("El reproductor no aparece")
        first = implement_worker.package(self.state, "TICKET-002", model="opus")
        repair = implement_worker.package(self.state, "TICKET-002", finding=finding)
        self.assertEqual((first["attempt"], repair["attempt"]), (1, 2))
        self.assertEqual((first["model"], repair["model"]), ("opus", "inherit"))
        self.assertNotIn("model", repair["spawn"]["claude"])
        self.assertIn(str(finding), Path(repair["package"]).read_text())
        with self.assertRaisesRegex(RunBlocked, "finding"):
            implement_worker.package(self.state, "TICKET-002", finding=self.fixture.root / "missing.txt")


    def test_review_repair_needs_no_ticket_but_needs_a_finding(self):
        with self.assertRaisesRegex(RunBlocked, "--finding"):
            implement_worker.package(self.state, None)
        finding = self.fixture.root / "review" / "x100-blocker-3.md"
        finding.parent.mkdir(exist_ok=True)
        finding.write_text("El endpoint devuelve 500 sin audio")
        result = implement_worker.package(self.state, None, finding=finding)
        brief = Path(result["package"]).read_text()
        self.assertIsNone(result["ticket"])
        self.assertIn("no pertenece a un solo ticket", brief)
        self.assertIn(str(finding), brief)
        self.assertNotIn("## Ticket\n", brief)

    def test_refactor_package_lists_the_branch_delta_and_receipt(self):
        with self.assertRaisesRegex(RunBlocked, "no delta"):
            implement_worker.refactor_package(self.state)
        product = self.fixture.worktree / "apps" / "api" / "service.ts"
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_text("export const x = 1;\n")
        qa_examples.git(self.fixture.worktree, "add", ".")
        qa_examples.git(self.fixture.worktree, "commit", "-m", "feat: TICKET-001 service")
        with patch("issue_delivery_orchestrator.implement_worker.integration_ref",
                   return_value="origin/missing"):
            result = implement_worker.refactor_package(self.state, model="opus")
        brief = Path(result["package"]).read_text()
        self.assertIn("apps/api/service.ts", brief)
        self.assertIn("refactor-review.md", brief)
        self.assertTrue(result["receipt"].endswith("review/refactor-review.md"))
        self.assertEqual(result["spawn"]["claude"]["model"], "opus")
        self.assertIn("feat: TICKET-001 service", brief)


class SmallLaneTests(unittest.TestCase):
    def lane_errors(self, spec):
        return [error for error in lint_spec(spec) if "Lane" in error]

    def test_small_lane_rejects_concurrency_and_unknown_lanes(self):
        self.assertEqual(self.lane_errors(qa_examples.spec()), [])
        self.assertTrue(any("concurrency" in error
                            for error in self.lane_errors("Lane: small\n" + qa_examples.spec())))
        self.assertEqual(self.lane_errors("Lane: small\n" + qa_examples.spec(concurrency="No")), [])
        self.assertTrue(self.lane_errors("Lane: tiny\n" + qa_examples.spec()))

    def test_small_lane_caps_the_qa_script(self):
        row = qa_examples.ROWS.strip().splitlines()[0]
        extra = "\n".join(row.replace("QA-001", f"QA-01{index}") for index in range(3))
        spec = "Lane: small\n" + qa_examples.spec(qa_examples.ROWS.rstrip() + "\n" + extra + "\n",
                                                  concurrency="No")
        self.assertTrue(any("at most 4" in error for error in self.lane_errors(spec)))
