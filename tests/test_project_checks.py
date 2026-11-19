import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from issue_delivery_orchestrator import project_checks
from issue_delivery_orchestrator.config import ProjectCheck, _project_checks
from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked

SCRIPT = "tools/check.py"
CHECK = ProjectCheck(
    name="app",
    script=SCRIPT,
    command=("python3", SCRIPT, "--base", "{base}"),
)


class ProjectChecksTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)
        self.state = {"worktree": str(self.worktree), "runId": "run-1"}
        for target, value in (
            ("settings", lambda: SimpleNamespace(project_checks=(CHECK,))),
            ("integration_ref", lambda state: "origin/test"),
            ("save_state", lambda state: None),
        ):
            patcher = patch.object(project_checks, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _script(self, findings=None, raw=None):
        path = self.worktree / SCRIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        output = raw if raw is not None else json.dumps({"findings": findings or []})
        path.write_text(
            "import sys\n"
            "assert sys.argv[1:] == ['--base', 'origin/test'], sys.argv\n"
            f"print({output!r})\n"
        )

    def test_skips_a_check_whose_script_is_not_in_the_worktree(self):
        report = project_checks.assert_project_checks(self.state, "implement")
        self.assertEqual(report["skipped"], ["app"])
        self.assertEqual(report["findings"], [])

    def test_blocks_the_checkpoint_with_every_finding(self):
        self._script([{"id": "comment", "file": "a.ts", "line": 3, "message": "New comment."}])
        with self.assertRaises(RunBlocked) as raised:
            project_checks.assert_project_checks(self.state, "implement")
        self.assertIn("[app/comment] a.ts:3 New comment.", str(raised.exception))

    def test_ignores_phases_that_do_not_write_code(self):
        self._script([{"id": "comment", "message": "New comment."}])
        self.assertIsNone(project_checks.assert_project_checks(self.state, "manual-revision"))

    def test_an_accepted_exception_unblocks_only_its_finding(self):
        self._script(
            [
                {"id": "e2e", "message": "No suite changed.", "acceptable": True},
                {"id": "docs", "message": "No doc changed.", "acceptable": True},
            ]
        )
        entry = project_checks.accept(self.state, "app", "e2e", "Copy change only")
        self.assertEqual(entry["reason"], "Copy change only")
        report = project_checks.evaluate(self.state)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["docs"])
        self.assertEqual(report["exceptions"][0]["id"], "e2e")

    def test_refuses_an_exception_for_a_finding_that_must_be_fixed(self):
        self._script([{"id": "escape", "message": "New eslint-disable."}])
        with self.assertRaises(RunBlocked) as raised:
            project_checks.accept(self.state, "app", "escape", "Needed")
        self.assertIn("does not allow exceptions", str(raised.exception))
        self.assertNotIn("projectCheckExceptions", self.state)

    def test_refuses_an_exception_without_reason_or_current_finding(self):
        self._script([])
        with self.assertRaises(RunBlocked):
            project_checks.accept(self.state, "app", "e2e", "  ")
        with self.assertRaises(RunBlocked):
            project_checks.accept(self.state, "app", "e2e", "Not reported")

    def test_blocks_when_the_check_does_not_print_its_report(self):
        self._script(raw="Traceback: boom")
        with self.assertRaises(RunBlocked) as raised:
            project_checks.assert_project_checks(self.state, "implement")
        self.assertIn("did not return its JSON report", str(raised.exception))


class ProjectCheckProfileTests(unittest.TestCase):
    def test_parses_checks_and_rejects_unsafe_scripts(self):
        self.assertEqual(_project_checks(None), ())
        parsed = _project_checks(
            [{"name": "app", "script": SCRIPT, "command": ["python3", SCRIPT]}]
        )
        self.assertEqual(parsed[0].command, ("python3", SCRIPT))
        with self.assertRaises(OrchestrationError):
            _project_checks([{"name": "app", "script": "../x.py", "command": ["x"]}])
        with self.assertRaises(OrchestrationError):
            _project_checks(
                [
                    {"name": "app", "script": SCRIPT, "command": ["a"]},
                    {"name": "app", "script": SCRIPT, "command": ["b"]},
                ]
            )


if __name__ == "__main__":
    unittest.main()
