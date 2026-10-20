import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issue_delivery_orchestrator.cli import _require_published_qa, publish_qa_results
from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked
from issue_delivery_orchestrator.linear import LinearClient
from issue_delivery_orchestrator.qa_script import (
    lint_qa_script,
    qa_cases,
    qa_comment_body,
    validate_qa_results,
)
from issue_delivery_orchestrator.state import run_root


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


HEADER = (
    "| QA ID | Type | Covers | Surface | Steps | Expected | Evidence | Regression test |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
)
RACE_TEST = "apps/api/src/routes/routes.finish.race.db.spec.ts"
ROWS = (
    "| QA-001 | happy-path | US-001 | UI | Abrir la ruta y cerrarla | Ruta cerrada con informe | Captura | — |\n"
    "| QA-002 | happy-path | US-002 | API | GET /routes/:id/report | 200 con paradas | Salida JSON | — |\n"
    "| QA-003 | concurrency | `concurrency` US-001 | API | 10 PUT /finish en paralelo | 1 cierre, 1 informe | Tabla de respuestas | "
    f"`{RACE_TEST}` |\n"
)


def spec(rows: str = ROWS, *, concurrency: str = "Yes", qa: bool = True) -> str:
    risk = "\n".join(
        f"| `{category}` | {answer} | `apps/api/src/routes.ts` | {'Race row' if answer == 'Yes' else '—'} |"
        for category, answer in (
            ("concurrency", concurrency),
            ("external-failure", "No"),
            ("async-lifecycle", "No"),
            ("edge-cases", "No"),
            ("consumers-platforms", "No"),
            ("roles-authorization", "No"),
        )
    )
    body = (
        "Spec ID: SPEC-TS-1\n\n# Cerrar rutas\n\n"
        "## User Stories\n"
        "- US-001: As a manager, I want routes to close at 19:00, so that reports exist.\n"
        "- US-002: As a manager, I want the report, so that I see skipped stops.\n\n"
        "## Risk Catalog\n\n| Risk category | Applies | Evidence | Coverage |\n"
        f"| --- | --- | --- | --- |\n{risk}\n\n"
    )
    if qa:
        body += f"## QA Script\n\n{HEADER}{rows}\n"
    return body + "## Out of Scope\n\nNone\n"


class LintTests(unittest.TestCase):
    def test_complete_script_passes(self):
        self.assertEqual(lint_qa_script(spec()), [])

    def test_missing_section_is_reported(self):
        self.assertEqual(lint_qa_script(spec(qa=False)), ["Missing section `## QA Script`"])

    def test_every_story_needs_a_happy_path(self):
        rows = ROWS.replace("| QA-002 | happy-path |", "| QA-002 | edge-case |")
        errors = lint_qa_script(spec(rows))

        self.assertIn("`US-002` needs at least one happy-path QA case", errors)

    def test_unknown_story_is_rejected(self):
        rows = ROWS + "| QA-004 | edge-case | US-009 | UI | x | y | z | — |\n"

        self.assertIn("QA Script covers unknown user story `US-009`", lint_qa_script(spec(rows)))

    def test_applicable_risk_needs_a_case(self):
        rows = ROWS.replace("`concurrency` US-001", "US-001")
        errors = lint_qa_script(spec(rows))

        self.assertIn("Risk `concurrency` applies, so a QA case must cover it", errors)

    def test_concurrency_case_needs_committed_race_test_and_backend_surface(self):
        rows = ROWS.replace(f"`{RACE_TEST}`", "—").replace("| API | 10 PUT", "| UI | 10 PUT")
        errors = lint_qa_script(spec(rows))

        self.assertTrue(any("needs the committed race test path" in e for e in errors))
        self.assertTrue(any("must be exercised through API or command" in e for e in errors))

    def test_concurrency_risk_without_concurrency_case(self):
        rows = ROWS.replace("| QA-003 | concurrency |", "| QA-003 | edge-case |")
        errors = lint_qa_script(spec(rows))

        self.assertIn(
            "Risk `concurrency` applies, so the QA Script needs a concurrency case", errors
        )

    def test_invalid_fields_and_duplicates(self):
        rows = ROWS + "| QA-001 | smoke | US-001 | mobile | x | y | z | — |\n| Q-9 | happy-path | US-001 | UI | | y | z | — |\n"
        errors = lint_qa_script(spec(rows))

        self.assertIn("Duplicate QA ID `QA-001`", errors)
        self.assertTrue(any("Type must be one of" in e for e in errors))
        self.assertTrue(any("Surface must be one of" in e for e in errors))
        self.assertIn("QA ID `Q-9` must look like QA-001", errors)
        self.assertIn("`Q-9` needs Steps", errors)

    def test_stories_with_a_surface_tag_are_detected(self):
        tagged = spec().replace("- US-001: As", "* US-001 `UI`: As").replace(
            "- US-002: As", "* US-002 `API-assembled`: As"
        )

        self.assertEqual(lint_qa_script(tagged), [])
        self.assertIn(
            "`US-002` needs at least one happy-path QA case",
            lint_qa_script(tagged.replace("| QA-002 | happy-path |", "| QA-002 | edge-case |")),
        )

    def test_scope_limits_coverage_to_the_slice(self):
        extra = spec().replace(
            "- US-002: As a manager, I want the report, so that I see skipped stops.\n",
            "- US-002: As a manager, I want the report, so that I see skipped stops.\n"
            "- US-003: As a manager, I want transfers, so that work moves.\n",
        )
        self.assertIn("`US-003` needs at least one happy-path QA case", lint_qa_script(extra))

        scoped = extra.replace("## QA Script\n\n", "## QA Script\n\nScope: US-001, US-002\n\n")
        self.assertEqual(lint_qa_script(scoped), [])

        outside = scoped.replace(
            "| QA-002 | happy-path | US-002 |", "| QA-002 | happy-path | US-002 US-003 |"
        )
        self.assertIn("QA Script covers `US-003`, outside its scope", lint_qa_script(outside))

    def test_scope_without_user_stories_section_still_needs_happy_paths(self):
        slice_block = (
            "Spec ID: SPEC-TS-1\n\n### Entrega 2 de 3\n\n## QA Script\n\nScope: US-001\n\n"
            + HEADER
            + "| QA-001 | edge-case | US-001 | UI | x | y | z | — |\n"
        )

        self.assertEqual(
            lint_qa_script(slice_block), ["`US-001` needs at least one happy-path QA case"]
        )

    def test_legacy_spec_has_no_cases(self):
        self.assertIsNone(qa_cases(spec(qa=False)))


class ResultsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(
            os.environ, {"ISSUE_DELIVERY_CONFIG_HOME": self.temporary.name}
        )
        self.environment.start()
        self.worktree = Path(self.temporary.name) / "repo"
        self.worktree.mkdir()
        git(self.worktree, "init", "-b", "test")
        git(self.worktree, "config", "user.email", "test@example.com")
        git(self.worktree, "config", "user.name", "Test")
        (self.worktree / ".gitignore").write_text(".local-runtime/\n")
        race = self.worktree / RACE_TEST
        race.parent.mkdir(parents=True)
        race.write_text("test('race', () => {});\n")
        git(self.worktree, "add", ".")
        git(self.worktree, "commit", "-m", "base")
        self.head = git(self.worktree, "rev-parse", "HEAD")
        self.state = {
            "runId": "run-1",
            "worktree": str(self.worktree),
            "runtimes": [{"runtimeId": "rt-1"}],
            "artifacts": {},
        }
        self.root = run_root(self.worktree, "run-1")
        (self.root / "planning").mkdir(parents=True)
        self.write_spec(spec())
        (self.root / "validation" / "ui").mkdir(parents=True)
        (self.root / "validation" / "qa").mkdir(parents=True)
        self.screenshot = self.root / "validation" / "ui" / "QA-001.png"
        self.screenshot.write_bytes(b"\x89PNG fake")
        self.output = self.root / "validation" / "qa" / "QA-002.json"
        self.output.write_text('{"status": 200, "stops": 3}\n')
        self.race_output = self.root / "validation" / "qa" / "QA-003.txt"
        self.race_output.write_text("200 x1\n409 x9\nreports=1\n")

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def write_spec(self, body: str) -> None:
        path = self.root / "planning" / "spec.md"
        path.write_text(body)
        self.state["artifacts"]["spec"] = str(path.relative_to(self.worktree))

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.worktree))

    def results(self, **overrides):
        cases = {
            "QA-001": {
                "id": "QA-001",
                "status": "PASS",
                "summary": "La ruta quedó cerrada con informe",
                "screenshots": [self.relative(self.screenshot)],
            },
            "QA-002": {
                "id": "QA-002",
                "status": "PASS",
                "summary": "200 con 3 paradas",
                "output": self.relative(self.output),
            },
            "QA-003": {
                "id": "QA-003",
                "status": "PASS",
                "summary": "1 cierre, 9 respuestas idempotentes",
                "output": self.relative(self.race_output),
            },
        }
        for qa_id, change in overrides.items():
            if change is None:
                cases.pop(qa_id)
            else:
                cases[qa_id] = {**cases[qa_id], **change}
        payload = {
            "qaVersion": 1,
            "verifiedCommit": self.head,
            "runtimeId": "rt-1",
            "verifiedAt": "2026-10-20T15:00:00Z",
            "cases": list(cases.values()),
        }
        path = self.root / "validation" / "qa-results.json"
        path.write_text(json.dumps(payload))
        return path

    def test_all_passing_cases_are_normalized(self):
        results = validate_qa_results(self.state, self.results())

        self.assertEqual([case["status"] for case in results["cases"]], ["PASS"] * 3)
        self.assertEqual(results["cases"][0]["screenshots"], [self.relative(self.screenshot)])

    def test_legacy_spec_skips_validation(self):
        self.write_spec(spec(qa=False))

        self.assertIsNone(validate_qa_results(self.state, self.results()))

    def test_missing_case_is_rejected(self):
        with self.assertRaisesRegex(OrchestrationError, "missing cases: QA-002"):
            validate_qa_results(self.state, self.results(**{"QA-002": None}))

    def test_failing_case_blocks(self):
        path = self.results(**{"QA-003": {"status": "FAIL", "summary": "2 informes creados"}})

        with self.assertRaisesRegex(RunBlocked, "QA-003 FAIL: 2 informes creados"):
            validate_qa_results(self.state, path)

    def test_waiver_needs_the_users_instruction(self):
        path = self.results(**{"QA-002": {"status": "WAIVED", "waiver": {"reason": "ERP caído"}}})
        with self.assertRaisesRegex(OrchestrationError, "waiver.userInstruction"):
            validate_qa_results(self.state, path)

        path = self.results(
            **{
                "QA-002": {
                    "status": "WAIVED",
                    "waiver": {"reason": "ERP caído", "userInstruction": "sáltalo, lo veo yo"},
                }
            }
        )
        results = validate_qa_results(self.state, path)
        self.assertEqual(results["cases"][1]["status"], "WAIVED")

    def test_ui_pass_needs_a_screenshot(self):
        with self.assertRaisesRegex(OrchestrationError, "needs at least one screenshot"):
            validate_qa_results(self.state, self.results(**{"QA-001": {"screenshots": []}}))

    def test_backend_pass_needs_its_output(self):
        with self.assertRaisesRegex(OrchestrationError, "needs its command output file"):
            validate_qa_results(self.state, self.results(**{"QA-002": {"output": None}}))

    def test_evidence_must_live_in_the_run(self):
        outside = self.worktree / "notes.txt"
        outside.write_text("x")
        with self.assertRaisesRegex(OrchestrationError, "inside the worktree"):
            validate_qa_results(
                self.state, self.results(**{"QA-002": {"output": self.relative(outside)}})
            )

    def test_concurrency_regression_test_must_be_committed(self):
        self.write_spec(spec(ROWS.replace(RACE_TEST, "apps/api/src/new.race.db.spec.ts")))

        with self.assertRaisesRegex(OrchestrationError, "is not committed"):
            validate_qa_results(self.state, self.results())

    def test_stale_commit_is_rejected(self):
        (self.worktree / "new.txt").write_text("x")
        git(self.worktree, "add", "new.txt")
        git(self.worktree, "commit", "-m", "later")

        with self.assertRaisesRegex(OrchestrationError, "QA results are stale"):
            validate_qa_results(self.state, self.results())

    def test_secrets_in_output_are_rejected(self):
        self.output.write_text("Authorization: lin_api_abcdefghijklmnop\n")

        with self.assertRaisesRegex(OrchestrationError, "looks like a secret"):
            validate_qa_results(self.state, self.results())

    def test_comment_lists_cases_images_and_output(self):
        results = validate_qa_results(self.state, self.results())
        body = qa_comment_body(
            "run-1",
            results,
            {self.relative(self.screenshot): "https://uploads.linear.app/qa-001.png"},
            self.worktree,
        )

        self.assertIn("### Resultado QA — run-1", body)
        self.assertIn("| QA-003 | concurrency | API |", body)
        self.assertIn("![QA-001](https://uploads.linear.app/qa-001.png)", body)
        self.assertIn("409 x9", body)

    def test_publish_uploads_screenshots_and_upserts_comment(self):
        linear = FakeLinear()
        with patch(
            "issue_delivery_orchestrator.cli._verified_linear",
            return_value=(linear, "env"),
        ), patch("issue_delivery_orchestrator.cli.save_state"):
            published = publish_qa_results(
                {**self.state, "issue": {"identifier": "TS-1"}},
                Path(self.relative(self.results())),
            )

        self.assertEqual(linear.uploads, [self.worktree / self.relative(self.screenshot)])
        marker, body = linear.comments[0]
        self.assertEqual(marker, "Resultado QA — run-1")
        self.assertIn("https://uploads.linear.app/QA-001.png", body)
        self.assertEqual(published["qa"]["verifiedCommit"], self.head)
        self.assertEqual(published["qa"]["linear"], {"commentId": "comment-1"})

    def test_publish_writes_description_block_when_comments_are_disabled(self):
        linear = FakeLinear()
        with patch.dict(os.environ, {"LINEAR_COMMENTS_ENABLED": "false"}), patch(
            "issue_delivery_orchestrator.cli._verified_linear",
            return_value=(linear, "env"),
        ), patch("issue_delivery_orchestrator.cli.save_state"):
            published = publish_qa_results(
                {**self.state, "issue": {"identifier": "TS-1"}},
                Path(self.relative(self.results())),
            )

        self.assertEqual(linear.comments, [])
        self.assertIn(":qa-results:start -->", linear.description)
        self.assertIn("### Resultado QA — run-1", linear.description)
        self.assertEqual(published["qa"]["linear"], {"descriptionBlock": "qa-results"})

    def test_checkpoint_requires_published_results_for_current_commit(self):
        path = self.relative(self.results())
        with self.assertRaisesRegex(RunBlocked, "manual revision requires qa-results"):
            _require_published_qa(self.state, None)
        with self.assertRaisesRegex(RunBlocked, "Publish the QA results"):
            _require_published_qa(self.state, path)

        self.state["qa"] = {"verifiedCommit": self.head}
        _require_published_qa(self.state, path)

    def test_checkpoint_ignores_legacy_specs(self):
        self.write_spec(spec(qa=False))

        _require_published_qa(self.state, None)


class FakeLinear:
    def __init__(self):
        self.uploads = []
        self.comments = []

    def issue(self, identifier):
        return type("Issue", (), {"id": "issue-1", "description": ""})()

    def upload_file(self, path):
        self.uploads.append(path)
        return f"https://uploads.linear.app/{path.name}"

    def upsert_comment(self, issue_id, marker, body):
        self.comments.append((marker, body))
        return "comment-1"

    def update_description(self, issue_id, description):
        self.description = description


class LinearUpsertCommentTests(unittest.TestCase):
    def client(self, existing):
        client = LinearClient("key")
        calls = []

        def graphql(query, variables):
            calls.append((query, variables))
            if "commentUpdate" in query:
                return {"commentUpdate": {"success": True}}
            return {"commentCreate": {"success": True, "comment": {"id": "new-1"}}}

        client._graphql = graphql
        client.issue_comments = lambda issue_id: existing
        return client, calls

    def comment(self, comment_id, body):
        from issue_delivery_orchestrator.linear import LinearComment

        return LinearComment(comment_id, body, "", "", "", "", "")

    def test_updates_the_comment_that_carries_the_marker(self):
        client, calls = self.client([self.comment("c-1", "otro"), self.comment("c-2", "### Resultado QA — run-1\nviejo")])

        self.assertEqual(client.upsert_comment("i-1", "### Resultado QA — run-1", "nuevo"), "c-2")
        self.assertIn("commentUpdate", calls[0][0])
        self.assertEqual(calls[0][1], {"id": "c-2", "body": "nuevo"})

    def test_creates_the_comment_when_missing(self):
        client, calls = self.client([self.comment("c-1", "otro")])

        self.assertEqual(client.upsert_comment("i-1", "### Resultado QA — run-1", "nuevo"), "new-1")
        self.assertIn("commentCreate", calls[0][0])


if __name__ == "__main__":
    unittest.main()
