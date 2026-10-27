import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from issue_delivery_orchestrator import metrics
from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked
from issue_delivery_orchestrator.metrics_receipt import observations
from issue_delivery_orchestrator.observability_cli import phase_completed, publish
from issue_delivery_orchestrator.usage import conductor_usage, token_totals


def usage_trace():
    report = {"threadId": "thread", "turnId": "turn", "cumulativeTotalTokens": 110,
              "model": "gpt-6.1-sol", "usage": {"inputTokens": 100, "outputTokens": 10,
              "totalTokens": 110, "cachedInputTokens": 80, "cacheWriteInputTokens": 20,
              "reasoningOutputTokens": 3}}
    complete = {"receivedAt": "2026-10-20T10:00:09+00:00", "content": {"rawPayload": {
        "event": {"type": "turn.completed", "usage": {"input_tokens": 999999},
        "conductor_sdk_metadata": {"reasoningEffort": "high", "codexUsage": {
            "schemaVersion": 1, "reports": [report, report]}}}}}}
    complete["sessionId"] = "session"
    return {"data": [{"sessionId": "session", "type": "userMessage", "receivedAt": "2026-10-20T10:00:01+00:00"}, complete, complete]}


def claude_result(uuid, cumulative, turn, at, session="claude-session"):
    counters = lambda values: dict(zip(("inputTokens", "cacheReadInputTokens",
                                        "cacheCreationInputTokens", "outputTokens", "thinkingTokens"), values))
    return {"sessionId": session, "type": "agent", "receivedAt": at, "content": {"rawPayload": {
        "type": "result", "uuid": uuid, "total_cost_usd": 9.99,
        "usage": dict(zip(("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                           "output_tokens"), turn)),
        "modelUsage": {model: counters(values) for model, values in cumulative.items()}}}}


def claude_trace():
    user = lambda at: {"sessionId": "claude-session", "type": "userMessage", "receivedAt": at,
                       "content": {"config": {"model": "opus-5-5-1m", "thinkingLevel": "max"}}}
    first = claude_result("r1", {"claude-opus-5-5[1m]": (4, 100, 20, 10, 2),
                                 "claude-haiku-5-5": (50, 0, 0, 5, 0)}, (4, 100, 20, 10),
                          "2026-10-20T10:00:02+00:00")
    second = claude_result("r2", {"claude-opus-5-5[1m]": (6, 300, 30, 40, 5),
                                  "claude-haiku-5-5": (50, 0, 0, 5, 0)}, (2, 200, 10, 30),
                           "2026-10-20T10:00:04+00:00")
    restarted = claude_result("r3", {"claude-opus-5-5[1m]": (7, 400, 40, 50, 0)}, (7, 400, 40, 50),
                              "2026-10-20T10:00:06+00:00")
    return {"data": [user("2026-10-20T10:00:01+00:00"), first, second, second,
                     user("2026-10-20T10:00:05+00:00"), restarted]}


class UsageTests(unittest.TestCase):
    def test_claude_turns_use_cumulative_model_deltas_and_detect_a_new_process(self):
        usage = conductor_usage(claude_trace(), "claude-session")
        self.assertEqual(usage["availability"], "reported")
        by_model = {}
        for request in usage["requests"].values():
            self.assertEqual(request["provider"], "claude")
            by_model.setdefault(request["model"], []).append(request["totalTokens"])
        self.assertEqual(sorted(by_model["claude-opus-5-5[1m]"]), [134, 242, 497])
        self.assertEqual(by_model["claude-haiku-5-5"], [55])
        totals = token_totals(usage["requests"])
        self.assertEqual(totals["cachedInputTokens"], 700)
        self.assertEqual(totals["totalTokens"], 134 + 242 + 497 + 55)
        trace = claude_trace()
        trace["data"].append({"sessionId": "claude-session", "type": "userMessage"})
        open_turn = conductor_usage(trace, "claude-session")
        self.assertEqual(open_turn["availability"], "reported")
        self.assertTrue(open_turn["incomplete"])
        self.assertEqual(open_turn["requests"], usage["requests"])
        only_open = {"data": [{"sessionId": "claude-session", "type": "userMessage"}]}
        self.assertEqual(conductor_usage(only_open, "claude-session")["availability"], "unavailable")

    def test_deduplicates_requests_and_does_not_sum_cache_or_cumulative_counters(self):
        trace = usage_trace()
        usage = conductor_usage(trace, "session")
        self.assertEqual(usage["availability"], "reported")
        self.assertEqual(len(usage["requests"]), 1)
        self.assertEqual(token_totals(usage["requests"])["totalTokens"], 110)
        changed = json.loads(json.dumps(trace["data"][1]))
        report = changed["content"]["rawPayload"]["event"]["conductor_sdk_metadata"]["codexUsage"]["reports"][0]
        report["usage"]["outputTokens"] = 11
        report["usage"]["totalTokens"] = 111
        trace["data"].append(changed)
        with self.assertRaisesRegex(OrchestrationError, "Conflicting duplicate"):
            conductor_usage(trace, "session")

    def test_unfinished_and_last_request_only_usage_are_unavailable(self):
        for trace in [
            {"data": []},
            {"data": usage_trace()["data"] + [{"sessionId": "session", "type": "userMessage"}]},
            {"data": [{"sessionId": "session", "content": {"rawPayload": {"event": {
                "type": "turn.completed", "usage": {"input_tokens": 100}}}}}]},
        ]:
            with self.subTest(trace=trace):
                result = conductor_usage(trace, "session")
                self.assertEqual(result["availability"], "unavailable")
                self.assertEqual(result["requests"], {})

    def test_rejects_double_counted_waits_and_keeps_unknown_counts_unknown(self):
        interval = {"reason": "human", "startedAt": "2026-10-20T10:00:01Z",
                    "finishedAt": "2026-10-20T10:00:09Z"}
        with self.assertRaisesRegex(OrchestrationError, "overlap"):
            observations({"waitingIntervals": [interval, interval]},
                         "2026-10-20T10:00:00Z", "2026-10-20T10:00:10Z")
        self.assertIsNone(observations(None, "unused", "unused")["checks"])


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.work / ".gitignore").write_text(".local-runtime/\n")
        (self.work / "app.ts").write_text("old\n")
        self.commit()
        self.state = {"runId": "metrics-test", "worktree": str(self.work), "observabilityVersion": 1,
                      "adoptedHead": self.git("rev-parse", "HEAD"), "issue": {"identifier": "LOG-1"}}

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.work, text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-q", "-m", "change")

    def start(self, unit="TICKET-001", kind="ticket"):
        with patch("issue_delivery_orchestrator.metrics.now", return_value="2026-10-20T10:00:00+00:00"):
            return metrics.start(self.state, unit, kind, 1, "gpt-6.1-sol", "high")

    def finish(self, unit="TICKET-001"):
        with patch("issue_delivery_orchestrator.metrics.now", return_value="2026-10-20T10:00:10+00:00"):
            return metrics.finish(self.state, unit, 1, "PASS")

    def test_records_real_commit_delta_and_idempotent_finish(self):
        self.start()
        (self.work / "app.ts").write_text("new\nmore\n")
        (self.work / "app.spec.ts").write_text("test\n")
        (self.work / "notes.md").write_text("docs\n")
        with self.assertRaisesRegex(RunBlocked, "clean tracked"):
            self.finish()
        self.commit()
        result = self.finish()
        self.assertEqual(result["lines"], {"product": {"added": 2, "deleted": 1},
                         "test": {"added": 1, "deleted": 0}, "excluded": {"added": 1, "deleted": 0}})
        self.assertEqual(result["wallSeconds"], 10)
        self.assertIsNone(result["checks"])
        self.assertEqual(self.finish(), result)
        with self.assertRaisesRegex(RunBlocked, "immutable"):
            metrics.finish(self.state, "TICKET-001", 1, "FAIL")

    def test_shared_usage_is_not_added_twice_to_run_total(self):
        for unit in ("TICKET-001", "phase-implement"):
            self.start(unit)
            self.finish(unit)
            usage = conductor_usage(usage_trace(), "session")
            metrics.attach_usage(self.state, unit, 1, usage, "shared")
        report = metrics.report(self.state)
        self.assertIn("110 tokens", report)
        self.assertNotIn("220 tokens", report)
        self.assertIn("shared", report)

    def test_report_names_the_provider_of_each_unit_and_splits_the_total(self):
        for unit, trace, session in (("TICKET-001", usage_trace(), "session"),
                                     ("TICKET-002", claude_trace(), "claude-session")):
            self.start(unit)
            self.finish(unit)
            metrics.attach_usage(self.state, unit, 1, conductor_usage(trace, session), "shared")
        report = metrics.report(self.state)
        self.assertIn("| TICKET-001 / 1 | ticket | Codex | gpt-6.1-sol / high |", report)
        self.assertIn("| TICKET-002 / 1 | ticket | Claude | claude-haiku-5-5, claude-opus-5-5[1m] / max |",
                      report)
        self.assertIn("- Claude · claude-opus-5-5[1m]: 873 tokens", report)
        self.assertIn("- Codex · gpt-6.1-sol: 110 tokens", report)
        self.assertIn("1038 tokens", report)

    def test_session_usage_gets_its_own_row_and_reimports_without_double_counting(self):
        trace = claude_trace()
        trace["data"].append({"sessionId": "claude-session", "type": "userMessage"})
        first = metrics.record_session(self.state, "Generador", conductor_usage(trace, "claude-session"))
        self.assertTrue(first["usage"]["incomplete"])
        metrics.record_session(self.state, "Generador", conductor_usage(claude_trace(), "claude-session"))
        report = metrics.report(self.state)
        self.assertIn("| session-Generador / 1 | session | Claude |", report)
        self.assertIn("928 tokens", report)
        self.assertNotIn("parcial", report)

    def test_failed_dirty_attempt_keeps_time_without_inventing_a_committed_delta(self):
        self.start()
        (self.work / "app.ts").write_text("work still in progress\n")
        with patch("issue_delivery_orchestrator.metrics.now", return_value="2026-10-20T10:00:10+00:00"):
            item = metrics.finish(self.state, "TICKET-001", 1, "BLOCKED")
        self.assertEqual(item["wallSeconds"], 10)
        self.assertTrue(item["uncommittedChanges"])
        self.assertIsNone(item["lines"])

    def test_dedicated_usage_cannot_span_units_or_be_allocated_twice(self):
        self.start()
        self.finish()
        usage = conductor_usage(usage_trace(), "session")
        metrics.attach_usage(self.state, "TICKET-001", 1, usage, "dedicated")
        self.start("TICKET-002")
        self.finish("TICKET-002")
        with self.assertRaisesRegex(RunBlocked, "already belong"):
            metrics.attach_usage(self.state, "TICKET-002", 1, usage, "dedicated")
        usage["firstEventAt"] = "2026-10-19T10:00:00Z"
        with self.assertRaisesRegex(RunBlocked, "spans beyond"):
            metrics.attach_usage(self.state, "TICKET-001", 1, usage, "dedicated")

    def test_imports_multiple_child_sessions_without_losing_prior_usage(self):
        self.start()
        self.finish()
        for sid in ("implementation-child", "test-child"):
            trace = usage_trace()
            for row in trace["data"]:
                row["sessionId"] = sid
            usage = conductor_usage(trace, sid)
            metrics.attach_usage(self.state, "TICKET-001", 1, usage, "dedicated")
            metrics.attach_usage(self.state, "TICKET-001", 1, usage, "dedicated")
        self.assertIn("220 tokens", metrics.report(self.state))

    def test_one_linear_comment_is_updated_and_disabled_comments_preserve_description(self):
        self.start()
        self.finish()
        comments = {}
        linear = SimpleNamespace(issue=lambda _: SimpleNamespace(id="issue", description="Keep this"),
            upsert_comment=lambda _, marker, body: comments.update({marker: body}) or "comment",
            update_description=lambda _, body: comments.update({"description": body}))
        config = SimpleNamespace(linear_comments_enabled=True, linear_marker_prefix="delivery")
        factory = lambda: (linear, "test")
        publish(self.state, factory, config)
        publish(self.state, factory, config)
        self.assertEqual(len(comments), 1)
        config.linear_comments_enabled = False
        publish(self.state, factory, config)
        self.assertIn("Keep this", comments["description"])
        self.assertIn(":metrics:start", comments["description"])

    def test_legacy_runs_are_not_retrofitted_and_phase_boundary_is_recorded(self):
        legacy = {k: v for k, v in self.state.items() if k != "observabilityVersion"}
        with self.assertRaisesRegex(RunBlocked, "new runs"):
            metrics.start(legacy, "TICKET-001", "ticket", 1, "model", "effort")
        self.assertIsNone(phase_completed(legacy, "implement", None, None, None, None))
        started = {"startedAt": "2026-10-20T10:00:00+00:00", "startCommit": self.state["adoptedHead"]}
        with patch("issue_delivery_orchestrator.observability_cli._publish_safely", return_value={"published": True}):
            result = phase_completed(self.state, "implement", started, None, None, None)
        self.assertEqual(result["metric"]["kind"], "phase")
        self.assertEqual(result["metric"]["startCommit"], started["startCommit"])
        self.assertIn("phaseMeasurement", self.state)
