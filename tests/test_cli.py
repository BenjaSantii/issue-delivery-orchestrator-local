import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from issue_delivery_orchestrator.cli import (
    _mode_decision,
    _new_run_mode,
    _path_mode_markers,
    _requested_worktree,
    parser,
    publish_linear_block,
    read_linear_issue,
)
from issue_delivery_orchestrator.config import settings
from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.linear import LinearComment, LinearIssue
from issue_delivery_orchestrator.spec_lint import RISK_CATEGORIES

VALID_SPEC_TAIL = (
    "\n## Risk Catalog\n\n"
    "| Risk category | Applies | Evidence | Coverage |\n| --- | --- | --- | --- |\n"
    + "".join(
        f"| `{category}` | No | `apps/x/src/y.ts` is read-only | — |\n"
        for category in RISK_CATEGORIES
    )
    + "\n## Testing Decisions\n\n"
    "| Risk owner | Existing evidence | Decision | Lowest sufficient seam | Unique signal | Superseded test |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "| Copy | None | none | none | none | — |\n"
)


class CliModeTests(unittest.TestCase):
    def test_sub_issue_parser_preserves_explicit_assignee(self):
        from issue_delivery_orchestrator.cli import parser

        arguments = ["LOG-347", "create-sub-issue", "--title", "Task", "--body-file", "task.md"]
        self.assertIsNone(parser().parse_args(arguments).assignee)
        for assignee in ["me", "none", "other-user-id"]:
            with self.subTest(assignee=assignee):
                parsed = parser().parse_args(arguments + ["--assignee", assignee])
                self.assertEqual(parsed.assignee, assignee)

    def test_reads_linear_issue_and_comments_with_personal_client(self):
        issue = LinearIssue(
            id="issue-id",
            identifier="SFW-1",
            title="Investigate expiry",
            branch_name="benjamin/sfw-1",
            description="Issue context",
            url="https://linear.app/turboshop/issue/SFW-1",
        )
        comment = LinearComment(
            id="comment-id",
            body="Relevant decision",
            created_at="2026-08-01T00:00:00Z",
            updated_at="2026-08-01T00:00:00Z",
            user_id="user-id",
            user_name="Benjamin",
            user_display_name="Benja",
        )
        linear = SimpleNamespace(
            issue=lambda _identifier: issue,
            issue_comments=lambda _issue_id: (comment,),
        )
        with patch(
            "issue_delivery_orchestrator.cli._verified_linear",
            return_value=(linear, "macos-keychain"),
        ):
            result = read_linear_issue("SFW-1")

        self.assertEqual(result["credentialSource"], "macos-keychain")
        self.assertEqual(result["issue"]["description"], "Issue context")
        self.assertEqual(result["comments"][0]["body"], "Relevant decision")

    def test_parses_read_linear_issue_without_run_arguments(self):
        args = parser().parse_args(["SFW-1", "read-linear-issue"])

        self.assertEqual(args.action, "read-linear-issue")

    def test_turboshop_review_waits_for_ten_quiet_minutes(self):
        profile = (
            Path(__file__).resolve().parents[1]
            / "profiles"
            / "turboshop.json"
        )
        with patch.dict(
            os.environ,
            {
                "ISSUE_DELIVERY_PROFILE": str(profile),
                "ISSUE_DELIVERY_ENV_FILE": "/missing/issue-delivery.env",
            },
            clear=True,
        ):
            args = parser().parse_args(["TS-1", "wait-review"])

        self.assertEqual(args.quiet_seconds, 600)
        self.assertEqual(args.max_seconds, 1200)
        self.assertEqual(args.poll_seconds, 15)

    def test_parses_explicit_codex_mode_and_worktree(self):
        args = parser().parse_args(
            ["TS-1", "--mode", "codex", "--worktree", "/tmp/codex-worktree"]
        )

        self.assertEqual(args.mode, "codex")
        self.assertEqual(args.worktree, Path("/tmp/codex-worktree"))

    def test_parses_explicit_vanilla_mode_and_worktree(self):
        args = parser().parse_args(
            ["TS-1", "--mode", "vanilla", "--worktree", "/tmp/checkout"]
        )

        self.assertEqual(args.mode, "vanilla")
        self.assertEqual(args.worktree, Path("/tmp/checkout"))

    def test_parses_explicit_conductor_mode_and_worktree(self):
        args = parser().parse_args(
            ["TS-1", "--mode", "conductor", "--worktree", "/tmp/conductor"]
        )

        self.assertEqual(args.mode, "conductor")
        self.assertEqual(args.worktree, Path("/tmp/conductor"))

    def test_parses_prepare_evidence_manifest(self):
        args = parser().parse_args(
            ["TS-1", "prepare-evidence", "--manifest", "validation/ui.json"]
        )

        self.assertEqual(args.action, "prepare-evidence")
        self.assertEqual(args.manifest, Path("validation/ui.json"))

    def test_publishes_linear_block_with_personal_client_without_comment(self):
        with tempfile.TemporaryDirectory() as raw:
            worktree = Path(raw)
            body = worktree / "spec.md"
            body.write_text("# Approved spec\n" + VALID_SPEC_TAIL)

            class Linear:
                def __init__(self):
                    self.description = "Keep"
                    self.comments = 0

                def issue(self, _identifier):
                    return SimpleNamespace(
                        id="issue-id",
                        identifier="SFW-1",
                        description=self.description,
                    )

                def update_description(self, _issue_id, description):
                    self.description = description

                def post_comment(self, _issue_id, _body):
                    self.comments += 1

            linear = Linear()
            configuration = SimpleNamespace(
                linear_marker_prefix="loop",
                linear_comments_enabled=False,
            )
            state = {
                "worktree": str(worktree),
                "issue": {"identifier": "SFW-1"},
            }
            with (
                patch(
                    "issue_delivery_orchestrator.cli._verified_linear",
                    return_value=(linear, "keychain"),
                ),
                patch(
                    "issue_delivery_orchestrator.cli.settings",
                    return_value=configuration,
                ),
            ):
                result = publish_linear_block(state, "spec", body)

        self.assertTrue(result["updated"])
        self.assertFalse(result["commentsEnabled"])
        self.assertIn("# Approved spec", linear.description)
        self.assertIn("loop:tickets:start", linear.description)
        self.assertEqual(linear.comments, 0)

    def test_rejects_spec_without_risk_catalog_before_writing_to_linear(self):
        with tempfile.TemporaryDirectory() as raw:
            worktree = Path(raw)
            body = worktree / "spec.md"
            body.write_text("# Approved spec\n")
            state = {"worktree": str(worktree), "issue": {"identifier": "SFW-1"}}
            with patch(
                "issue_delivery_orchestrator.cli._verified_linear",
                side_effect=AssertionError("Linear must not be contacted"),
            ):
                with self.assertRaises(RunBlocked) as raised:
                    publish_linear_block(state, "spec", body)
        self.assertIn("Risk Catalog", str(raised.exception))

    def test_accepts_linear_markdown_normalization_after_publish(self):
        with tempfile.TemporaryDirectory() as raw:
            worktree = Path(raw)
            body = worktree / "spec.md"
            body.write_text(
                "# Approved spec\n\nBlocked by:\n- Snapshot rejection\n\n"
                "| Field | Decision |\n| --- | :---: |\n| Receipt | Keep |\n"
                + VALID_SPEC_TAIL
            )

            class Linear:
                def __init__(self):
                    self.description = "Keep"

                def issue(self, _identifier):
                    return SimpleNamespace(
                        id="issue-id",
                        identifier="SFW-1",
                        description=self.description,
                    )

                def update_description(self, _issue_id, description):
                    self.description = description.replace(
                        "Blocked by:\n- Snapshot rejection",
                        "Blocked by:\n\n* Snapshot rejection",
                    ).replace(
                        "| --- | :---: |",
                        "- | -- | -- | -",
                    )

                def post_comment(self, _issue_id, _body):
                    raise AssertionError("Comments must remain disabled")

            linear = Linear()
            configuration = SimpleNamespace(
                linear_marker_prefix="loop",
                linear_comments_enabled=False,
            )
            state = {
                "worktree": str(worktree),
                "issue": {"identifier": "SFW-1"},
            }
            with (
                patch(
                    "issue_delivery_orchestrator.cli._verified_linear",
                    return_value=(linear, "keychain"),
                ),
                patch(
                    "issue_delivery_orchestrator.cli.settings",
                    return_value=configuration,
                ),
            ):
                result = publish_linear_block(state, "spec", body)

        self.assertTrue(result["updated"])
        self.assertFalse(result["commentsEnabled"])

    def test_codex_mode_does_not_adopt_superset_environment(self):
        with patch.dict(os.environ, {"SUPERSET_WORKSPACE_PATH": "/tmp/superset"}):
            self.assertIsNone(_requested_worktree(None, "codex"))

    def test_superset_mode_accepts_superset_environment(self):
        with patch.dict(os.environ, {"SUPERSET_WORKSPACE_PATH": "/tmp/superset"}):
            self.assertEqual(
                _requested_worktree(None, "superset"),
                Path("/tmp/superset"),
            )

    def test_vanilla_mode_does_not_adopt_superset_environment(self):
        with patch.dict(os.environ, {"SUPERSET_WORKSPACE_PATH": "/tmp/superset"}):
            self.assertIsNone(_requested_worktree(None, "vanilla"))

    def test_conductor_mode_does_not_adopt_superset_environment(self):
        with patch.dict(os.environ, {"SUPERSET_WORKSPACE_PATH": "/tmp/superset"}):
            self.assertIsNone(_requested_worktree(None, "conductor"))

    def test_detects_conductor_from_cloud_environment(self):
        configuration = self._settings()
        with patch.dict(os.environ, {"CONDUCTOR_IS_LOCAL": "0"}):
            self.assertEqual(
                _new_run_mode(
                    None,
                    Path("/home/vercel-sandbox/turboshop"),
                    configuration,
                ),
                ("conductor", "conductor-cloud-environment"),
            )

    def test_conductor_cloud_environment_wins_over_path_markers(self):
        configuration = self._settings()
        with patch.dict(os.environ, {"CONDUCTOR_IS_LOCAL": "0"}):
            self.assertEqual(
                _new_run_mode(
                    None,
                    Path("/tmp/.codex/worktrees/TS-1"),
                    configuration,
                ),
                ("conductor", "conductor-cloud-environment"),
            )

    def test_local_conductor_workspace_keeps_regular_detection(self):
        configuration = self._settings()
        with patch.dict(os.environ, {"CONDUCTOR_IS_LOCAL": "1"}):
            self.assertEqual(
                _new_run_mode(
                    None,
                    Path("/tmp/conductor/workspaces/TS-1"),
                    configuration,
                ),
                ("vanilla", "vanilla-fallback"),
            )

    def test_explicit_mode_has_priority_over_conductor_cloud(self):
        configuration = self._settings()
        with patch.dict(os.environ, {"CONDUCTOR_IS_LOCAL": "0"}):
            self.assertEqual(
                _new_run_mode(
                    "vanilla",
                    Path("/home/vercel-sandbox/turboshop"),
                    configuration,
                ),
                ("vanilla", "explicit"),
            )

    def test_detects_codex_from_path_marker(self):
        configuration = self._settings()

        self.assertEqual(
            _new_run_mode(
                None,
                Path("/tmp/.codex/worktrees/TS-1"),
                configuration,
            ),
            ("codex", "path-marker"),
        )

    def test_detects_superset_from_environment(self):
        configuration = self._settings()
        with patch.dict(
            os.environ,
            {"SUPERSET_WORKSPACE_PATH": "/tmp/superset/worktrees/TS-1"},
        ):
            self.assertEqual(
                _new_run_mode(
                    None,
                    Path("/tmp/superset/worktrees/TS-1"),
                    configuration,
                ),
                ("superset", "superset-environment"),
            )

    def test_detects_superset_from_path_marker_without_environment(self):
        configuration = self._settings()

        self.assertEqual(
            _new_run_mode(
                None,
                Path("/tmp/.superset/worktrees/TS-1"),
                configuration,
            ),
            ("superset", "path-marker"),
        )

    def test_configured_root_has_priority_over_path_marker(self):
        configuration = self._settings(
            ISSUE_DELIVERY_CODEX_WORKTREE_ROOTS="/tmp/custom-worktrees"
        )

        self.assertEqual(
            _new_run_mode(
                None,
                Path("/tmp/custom-worktrees/superset-project/TS-1"),
                configuration,
            ),
            ("codex", "configured-root"),
        )

    def test_explicit_mode_has_priority_over_detection(self):
        configuration = self._settings()

        self.assertEqual(
            _new_run_mode(
                "superset",
                Path("/tmp/.codex/worktrees/TS-1"),
                configuration,
            ),
            ("superset", "explicit"),
        )

    def test_explicit_vanilla_does_not_depend_on_path_markers(self):
        configuration = self._settings()

        self.assertEqual(
            _new_run_mode(
                "vanilla",
                Path("/tmp/.codex/worktrees/TS-1"),
                configuration,
            ),
            ("vanilla", "explicit"),
        )

    def test_mode_decision_exposes_values_required_for_chat_announcement(self):
        decision = _mode_decision(
            {
                "worktree": "/tmp/worktree",
                "mode": {"name": "vanilla"},
                "reviewer": {"method": "cua-driver"},
            },
            "vanilla-fallback",
        )

        self.assertEqual(
            decision,
            {
                "mode": "vanilla",
                "source": "vanilla-fallback",
                "reviewer": "cua-driver",
                "worktree": str(Path("/tmp/worktree").resolve()),
            },
        )

    def test_conductor_mode_decision_announces_playwright_reviewer(self):
        decision = _mode_decision(
            {
                "worktree": "/tmp/worktree",
                "mode": {"name": "conductor"},
                "reviewer": {"method": "playwright"},
            },
            "conductor-cloud-environment",
        )

        self.assertEqual(decision["mode"], "conductor")
        self.assertEqual(decision["reviewer"], "playwright")

    def test_ambiguous_path_requires_explicit_mode(self):
        configuration = self._settings()

        with self.assertRaisesRegex(RunBlocked, "both Codex and Superset"):
            _new_run_mode(
                None,
                Path("/tmp/codex/superset/TS-1"),
                configuration,
            )

    def test_unknown_path_falls_back_to_vanilla(self):
        configuration = self._settings()

        self.assertEqual(
            _new_run_mode(
                None,
                Path("/tmp/worktrees/TS-1"),
                configuration,
            ),
            ("vanilla", "vanilla-fallback"),
        )

    def test_path_markers_do_not_match_substrings(self):
        self.assertEqual(
            _path_mode_markers(Path("/tmp/mycodexproject/TS-1")),
            set(),
        )

    def _settings(self, **environment):
        profile = (
            Path(__file__).resolve().parents[1]
            / "profiles"
            / "turboshop.json"
        )
        values = {
            "ISSUE_DELIVERY_PROFILE": str(profile),
            "ISSUE_DELIVERY_ENV_FILE": "/missing/issue-delivery.env",
            **environment,
        }
        with patch.dict(os.environ, values, clear=True):
            return settings()


if __name__ == "__main__":
    unittest.main()
