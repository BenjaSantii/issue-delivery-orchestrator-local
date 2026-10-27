import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from issue_delivery_orchestrator.errors import OrchestrationError
from issue_delivery_orchestrator.evidence import upsert_ui_run
from issue_delivery_orchestrator.linear import (
    LinearClient,
    extract_marker_block,
    linear_markdown_equivalent,
    normalize_issue_identifier,
    normalize_linear_markdown,
    upsert_markdown_section,
    upsert_marker_block,
)


class LinearHelpersTests(unittest.TestCase):
    def test_sub_issue_assignment_uses_app_default_and_explicit_override(self):
        cases = [
            ("Sales Force", None, "Route work", None, ("Sales Force",), "viewer-id"),
            (None, "Salesforce", "Route work", None, ("Sales Force",), "viewer-id"),
            (None, None, "Sales Force: route work", None, ("Sales Force",), "viewer-id"),
            ("Shipment App", None, "Shipment work", None, ("Sales Force",), None),
            ("Frontend Sales", None, "Sales work", None, ("Sales Force",), None),
            (None, None, "Integrate Shipment with Sales Force", None, ("Sales Force",), None),
            ("Sales Force", None, "Route work", "other-id", ("Sales Force",), "other-id"),
            ("Sales Force", None, "Route work", "none", ("Sales Force",), None),
            ("Shipment App", None, "Shipment work", "me", ("Sales Force",), "viewer-id"),
            ("Sales Force", None, "Route work", None, (), None),
        ]
        for label, project, title, explicit, defaults, expected in cases:
            with self.subTest(label=label, project=project, title=title, explicit=explicit):
                client = LinearClient("secret")
                parent = {
                    "id": "parent-id", "title": title, "team": {"id": "team-id"},
                    "project": {"name": project} if project else None,
                    "labels": {"nodes": [{"name": label}] if label else []},
                }
                with patch.object(client, "_graphql", side_effect=[
                    {"issue": parent},
                    {"issueCreate": {"success": True, "issue": {"identifier": "LOG-2"}}},
                ]) as graphql, patch.object(
                    client, "viewer", return_value=SimpleNamespace(id="viewer-id")
                ) as viewer, patch.object(client, "issue", return_value="created"):
                    result = client.create_sub_issue(
                        "LOG-1", "Child task", "Sales Force is mentioned here",
                        assignee=explicit, self_assignment_labels=defaults,
                    )
                self.assertEqual(result, "created")
                self.assertEqual(graphql.call_args_list[1].args[1]["assigneeId"], expected)
                self.assertEqual(viewer.call_count, int(expected == "viewer-id"))

    def test_start_issue_moves_only_unstarted_issues_to_the_first_started_state(self):
        states = {"nodes": [
            {"id": "review", "name": "In Review", "type": "started", "position": 1002},
            {"id": "progress", "name": "In Progress", "type": "started", "position": 2},
            {"id": "todo", "name": "Todo", "type": "unstarted", "position": 1},
        ]}
        client = LinearClient("secret")
        with patch.object(client, "_graphql", side_effect=[
            {"issue": {"state": {"name": "Todo", "type": "unstarted"}, "team": {"states": states}}},
            {"issueUpdate": {"success": True}},
        ]) as graphql:
            result = client.start_issue("issue-id")
        self.assertEqual(result, {"changed": True, "from": "Todo", "state": "In Progress"})
        self.assertEqual(graphql.call_args_list[1].args[1], {"id": "issue-id", "stateId": "progress"})
        with patch.object(client, "_graphql", return_value={"issue": {
            "state": {"name": "In Review", "type": "started"}, "team": {"states": states}}}) as graphql:
            result = client.start_issue("issue-id")
        self.assertEqual(result, {"changed": False, "state": "In Review"})
        self.assertEqual(graphql.call_count, 1)

    def test_fetches_all_issue_comments_with_cursor_pagination(self):
        class Client(LinearClient):
            def __init__(self):
                super().__init__("secret")
                self.afters = []

            def _graphql(self, query, variables):
                self.assert_query(query)
                self.afters.append(variables["after"])
                if variables["after"] is None:
                    return {
                        "issue": {
                            "comments": {
                                "nodes": [
                                    {
                                        "id": "comment-1",
                                        "body": "First",
                                        "createdAt": "2026-08-01T00:00:00Z",
                                        "updatedAt": "2026-08-01T00:00:00Z",
                                        "user": {
                                            "id": "user-1",
                                            "name": "Benjamin",
                                            "displayName": "Benja",
                                        },
                                    }
                                ],
                                "pageInfo": {
                                    "hasNextPage": True,
                                    "endCursor": "cursor-2",
                                },
                            }
                        }
                    }
                return {
                    "issue": {
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-2",
                                    "body": "Second",
                                    "createdAt": "2026-08-02T00:00:00Z",
                                    "updatedAt": "2026-08-02T00:00:00Z",
                                    "user": None,
                                }
                            ],
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": None,
                            },
                        }
                    }
                }

            def assert_query(self, query):
                if "comments(first: 50, after: $after)" not in query:
                    raise AssertionError(query)

        client = Client()

        comments = client.issue_comments("issue-id")

        self.assertEqual(client.afters, [None, "cursor-2"])
        self.assertEqual([comment.id for comment in comments], ["comment-1", "comment-2"])
        self.assertEqual(comments[0].user_display_name, "Benja")
        self.assertEqual(comments[1].user_id, "")

    def test_normalizes_identifier_and_linear_url(self):
        self.assertEqual(normalize_issue_identifier("ts-123"), "TS-123")
        self.assertEqual(
            normalize_issue_identifier("https://linear.app/turboshop/issue/TS-456/a-title"),
            "TS-456",
        )

    def test_rejects_non_issue_value(self):
        with self.assertRaises(OrchestrationError):
            normalize_issue_identifier("not-an-issue")

    def test_upserts_named_section_without_touching_following_section(self):
        source = "# Issue\n\n## Spec\n\nOld\n\n## Tickets\n\nKeep\n"
        updated = upsert_markdown_section(source, "Spec", "New")
        self.assertIn("## Spec\n\nNew", updated)
        self.assertIn("## Tickets\n\nKeep", updated)
        self.assertNotIn("\nOld\n", updated)

    def test_upserts_only_current_ui_run(self):
        source = (
            "Intro\n\n## UI enhancements\n\n"
            "### Issue Delivery old\n\nold-image\n\n"
            "### Issue Delivery current\n\nstale\n\n"
            "## Tickets\n\nKeep\n"
        )
        updated = upsert_ui_run(
            source,
            "current",
            "### Issue Delivery current\n\nfresh",
        )
        self.assertIn("old-image", updated)
        self.assertIn("fresh", updated)
        self.assertNotIn("stale", updated)
        self.assertIn("## Tickets\n\nKeep", updated)

    def test_upserts_marker_block_without_touching_other_content(self):
        source = (
            "Intro\n\n<!-- loop:spec:start -->\nOld\n<!-- loop:spec:end -->"
            "\n\nKeep"
        )
        updated = upsert_marker_block(
            source,
            marker_prefix="loop",
            block="spec",
            body="New",
        )

        self.assertIn("<!-- loop:spec:start -->\nNew\n<!-- loop:spec:end -->", updated)
        self.assertIn("Keep", updated)
        self.assertNotIn("\nOld\n", updated)

    def test_rejects_inconsistent_marker_block(self):
        with self.assertRaisesRegex(OrchestrationError, "inconsistent spec markers"):
            upsert_marker_block(
                "<!-- loop:spec:start -->\nMissing end",
                marker_prefix="loop",
                block="spec",
                body="New",
            )

    def test_extracts_only_content_between_marker_pair(self):
        document = (
            "Before\n<!-- loop:spec:start -->\nExpected\n"
            "<!-- loop:spec:end -->\nAfter"
        )

        extracted = extract_marker_block(
            document,
            marker_prefix="loop",
            block="spec",
        )

        self.assertEqual(extracted, "Expected")

    def test_normalizes_linear_bullets_and_table_separators(self):
        local = (
            "- First\n  - Nested\n\n"
            "| Name | Decision |\n| --- | :---: |\n| Snapshot | Keep |"
        )
        remote = (
            "* First\n  * Nested\n\n"
            "| Name | Decision |\n|:-----|------:|\n| Snapshot | Keep |"
        )

        self.assertTrue(linear_markdown_equivalent(local, remote))
        self.assertEqual(
            normalize_linear_markdown(local),
            normalize_linear_markdown(remote),
        )

    def test_normalizes_linear_short_table_separators_and_edge_fragments(self):
        local = "- | --- | --- | --- | --- | -"
        remote = " | -- | -- | -- | -- | "

        self.assertTrue(linear_markdown_equivalent(local, remote))
        self.assertEqual(
            normalize_linear_markdown(local),
            "| --- | --- | --- | --- |",
        )

    def test_table_separator_normalization_preserves_column_count(self):
        expected = "| --- | --- | --- |"
        changed = "| -- | -- |"

        self.assertFalse(linear_markdown_equivalent(expected, changed))

    def test_table_separator_normalization_rejects_content_cells(self):
        expected = "| --- | changed | --- |"

        self.assertEqual(normalize_linear_markdown(expected), expected)

    def test_normalizes_linear_blank_line_before_list_after_label(self):
        local = "Blocked by:\n- TICKET-001"
        remote = "Blocked by:\n\n* TICKET-001"

        self.assertTrue(linear_markdown_equivalent(local, remote))

    def test_list_spacing_normalization_preserves_unrelated_blank_lines(self):
        expected = "Paragraph\n- Item"
        changed = "Paragraph\n\n- Item"

        self.assertFalse(linear_markdown_equivalent(expected, changed))

    def test_normalization_preserves_material_markdown_differences(self):
        expected = "- Persist rejected snapshots\n| Field | Value |\n| --- | --- |"
        changed = "* Persist every snapshot\n| Field | Value |\n| --- | --- |"

        self.assertFalse(linear_markdown_equivalent(expected, changed))


if __name__ == "__main__":
    unittest.main()
