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


class UsageTests(unittest.TestCase):
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
