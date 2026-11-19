import unittest
from unittest.mock import patch

from issue_delivery_orchestrator import waits
from issue_delivery_orchestrator.state import resume_run


def row(at, kind="agent", raw=None, session="main"):
    return {"sessionId": session, "type": kind, "receivedAt": at,
            "content": {"rawPayload": raw or {}}}


def tool(at, part_type, **fields):
    role = "assistant" if part_type == "tool_use" else "user"
    return row(at, raw={"type": role, "message": {"content": [{"type": part_type, **fields}]}})


def trace():
    return {"data": [
        row("2026-10-20T10:00:00Z", "userMessage"),
        tool("2026-10-20T10:00:10Z", "tool_use", id="t1",
             input={"command": "cd repo && heavy pnpm exec nx test app --testFile=a.spec.ts"}),
        tool("2026-10-20T10:01:10Z", "tool_result", tool_use_id="t1"),
        tool("2026-10-20T10:01:20Z", "tool_use", id="t2", input={"command": "grep -rn test src"}),
        tool("2026-10-20T10:01:21Z", "tool_result", tool_use_id="t2"),
        row("2026-10-20T10:02:00Z", raw={"type": "result"}),
        row("2026-10-20T10:12:00Z", "userMessage"),
        row("2026-10-20T10:13:00Z", raw={"type": "result"}),
        row("2026-10-20T10:13:30Z", "userMessage", session="other"),
    ]}


class CheckCommandTests(unittest.TestCase):
    def test_counts_runners_and_ignores_commands_that_only_mention_them(self):
        for command in ("pnpm exec nx test sales-force-app --testPathPattern=login",
                        "cd repo; heavy npx jest src/a.spec.ts", "nx run app:lint",
                        "nx run-many -t typecheck", "nx run-many --targets=build,lint",
                        "pnpm lint", "CI=1 pnpm exec tsc -p tsconfig.json",
                        "PYTHONPATH=src python3 -m unittest discover -s tests",
                        "npx playwright test e2e/manager-web.spec.ts", "prettier --check a.ts"):
            self.assertTrue(waits.is_check_command(command), command)
        for command in ("cat jest.config.ts", "grep -rn \"test\" src", "nx run app:build",
                        "nx run-many --targets=build", "prettier --write a.ts", "git log --oneline"):
            self.assertFalse(waits.is_check_command(command), command)


class ConversationTests(unittest.TestCase):
    def test_turn_gaps_are_human_waits_and_check_commands_are_timed(self):
        signals = waits.conversation_signals(trace(), "main")
        self.assertEqual(signals["coverage"], ["2026-10-20T10:00:00Z", "2026-10-20T10:13:00Z"])
        self.assertEqual(signals["humanGaps"], [["2026-10-20T10:02:00Z", "2026-10-20T10:12:00Z"]])
        self.assertEqual(signals["checks"], [["2026-10-20T10:00:10Z", "2026-10-20T10:01:10Z"]])


class BreakdownTests(unittest.TestCase):
    def state(self, label="Generador"):
        state = {}
        waits.keep_conversation(state, "main", label, waits.conversation_signals(trace(), "main"))
        waits.record(state, "human", "blocked", "2026-10-20T10:11:00Z", "2026-10-20T10:12:30Z")
        waits.record(state, "infrastructure", "wait-review",
                     "2026-10-20T10:12:00Z", "2026-10-20T10:12:50Z")
        return state

    def test_overlapping_waits_count_once_and_checks_stay_inside_execution(self):
        split = waits.breakdown(self.state(), "2026-10-20T10:00:00Z", "2026-10-20T10:13:00Z")
        self.assertEqual(split, {"humanWaitSeconds": 630.0, "infrastructureWaitSeconds": 20.0,
                                 "checkSeconds": 60.0, "executionSeconds": 130.0,
                                 "conversationCovered": True})

    def test_without_a_main_chat_execution_is_unknown_but_engine_waits_still_count(self):
        split = waits.breakdown(self.state("QA"), "2026-10-20T10:00:00Z", "2026-10-20T10:13:00Z")
        self.assertFalse(split["conversationCovered"])
        self.assertIsNone(split["executionSeconds"])
        self.assertEqual(split["humanWaitSeconds"], 90.0)
        self.assertEqual(split["checkSeconds"], 60.0)

    def test_a_unit_outside_the_imported_chat_is_not_covered(self):
        split = waits.breakdown(self.state(), "2026-10-20T09:00:00Z", "2026-10-20T10:13:00Z")
        self.assertFalse(split["conversationCovered"])
        self.assertIsNone(split["executionSeconds"])


class ResumeTests(unittest.TestCase):
    def test_resume_records_the_blocked_time_as_a_human_wait(self):
        state = {"status": "needs_user_decision",
                 "blocker": {"phase": "grill", "reason": "x", "createdAt": "2026-10-20T10:00:00+00:00"}}
        with patch("issue_delivery_orchestrator.state.now", return_value="2026-10-20T10:05:00+00:00"), \
                patch("issue_delivery_orchestrator.state.save_state"):
            resume_run(state)
        self.assertEqual(state["waitIntervals"], [{
            "reason": "human", "source": "blocked", "startedAt": "2026-10-20T10:00:00+00:00",
            "finishedAt": "2026-10-20T10:05:00+00:00"}])
        self.assertEqual(state["status"], "active")


if __name__ == "__main__":
    unittest.main()
