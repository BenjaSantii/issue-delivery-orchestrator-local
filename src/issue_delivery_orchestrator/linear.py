from __future__ import annotations

import json
import mimetypes
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OrchestrationError


@dataclass(frozen=True)
class LinearViewer:
    id: str
    name: str
    display_name: str
    email: str


@dataclass(frozen=True)
class LinearIssue:
    id: str
    identifier: str
    title: str
    branch_name: str
    description: str
    url: str


@dataclass(frozen=True)
class LinearComment:
    id: str
    body: str
    created_at: str
    updated_at: str
    user_id: str
    user_name: str
    user_display_name: str


class LinearClient:
    API_URL = "https://api.linear.app/graphql"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def viewer(self) -> LinearViewer:
        data = self._graphql(
            "query OrchestrationViewer { viewer { id name displayName email } }",
            {},
        )["viewer"]
        return LinearViewer(
            id=data["id"],
            name=data.get("name") or "",
            display_name=data.get("displayName") or "",
            email=(data.get("email") or "").lower(),
        )

    def issue(self, identifier_or_url: str) -> LinearIssue:
        identifier = normalize_issue_identifier(identifier_or_url)
        query = """
        query OrchestrationIssue($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            branchName
            description
            url
          }
        }
        """
        data = self._graphql(query, {"id": identifier}).get("issue")
        if not data:
            raise OrchestrationError(f"Linear issue not found: {identifier_or_url}")
        branch_name = (data.get("branchName") or "").strip()
        if not branch_name:
            raise OrchestrationError(f"Linear issue {data['identifier']} has no git branch name")
        return LinearIssue(
            id=data["id"],
            identifier=data["identifier"],
            title=data["title"],
            branch_name=branch_name,
            description=data.get("description") or "",
            url=data.get("url") or "",
        )

    def issue_comments(self, issue_id: str) -> tuple[LinearComment, ...]:
        query = """
        query OrchestrationIssueComments($id: String!, $after: String) {
          issue(id: $id) {
            comments(first: 50, after: $after) {
              nodes {
                id
                body
                createdAt
                updatedAt
                user { id name displayName }
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
        """
        comments: list[LinearComment] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        while True:
            issue = self._graphql(query, {"id": issue_id, "after": after}).get("issue")
            if not issue:
                raise OrchestrationError(f"Linear issue not found: {issue_id}")
            connection = issue.get("comments") or {}
            for node in connection.get("nodes") or []:
                user = node.get("user") or {}
                comments.append(
                    LinearComment(
                        id=node["id"],
                        body=node.get("body") or "",
                        created_at=node.get("createdAt") or "",
                        updated_at=node.get("updatedAt") or "",
                        user_id=user.get("id") or "",
                        user_name=user.get("name") or "",
                        user_display_name=user.get("displayName") or "",
                    )
                )
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return tuple(comments)
            cursor = (page_info.get("endCursor") or "").strip()
            if not cursor or cursor in seen_cursors:
                raise OrchestrationError(
                    "Linear comments pagination did not provide a new cursor"
                )
            seen_cursors.add(cursor)
            after = cursor

    def post_comment(self, issue_id: str, body: str) -> None:
        query = """
        mutation OrchestrationComment($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) { success }
        }
        """
        data = self._graphql(query, {"issueId": issue_id, "body": body})
        if not data.get("commentCreate", {}).get("success"):
            raise OrchestrationError("Linear commentCreate did not succeed")

    def upsert_comment(self, issue_id: str, marker: str, body: str) -> str:
        """Update the comment whose body contains `marker`, or create it."""
        for comment in self.issue_comments(issue_id):
            if marker in comment.body:
                query = """
                mutation OrchestrationCommentUpdate($id: String!, $body: String!) {
                  commentUpdate(id: $id, input: { body: $body }) { success }
                }
                """
                data = self._graphql(query, {"id": comment.id, "body": body})
                if not data.get("commentUpdate", {}).get("success"):
                    raise OrchestrationError("Linear commentUpdate did not succeed")
                return comment.id
        query = """
        mutation OrchestrationCommentCreate($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
            comment { id }
          }
        }
        """
        data = self._graphql(query, {"issueId": issue_id, "body": body})
        payload = data.get("commentCreate", {})
        if not payload.get("success"):
            raise OrchestrationError("Linear commentCreate did not succeed")
        return (payload.get("comment") or {}).get("id") or ""

    def update_description(self, issue_id: str, description: str) -> None:
        query = """
        mutation OrchestrationIssueUpdate($id: String!, $description: String!) {
          issueUpdate(id: $id, input: { description: $description }) { success }
        }
        """
        data = self._graphql(query, {"id": issue_id, "description": description})
        if not data.get("issueUpdate", {}).get("success"):
            raise OrchestrationError("Linear issueUpdate did not succeed")

    def create_sub_issue(
        self,
        parent_identifier: str,
        title: str,
        description: str,
        *,
        assignee: str | None = None,
        self_assignment_labels: tuple[str, ...] = (),
    ) -> LinearIssue:
        parent = self._graphql(
            """
            query OrchestrationParent($id: String!) {
              issue(id: $id) {
                id title team { id } project { name }
                labels { nodes { name } }
              }
            }
            """,
            {"id": normalize_issue_identifier(parent_identifier)},
        ).get("issue")
        if not parent:
            raise OrchestrationError(f"Linear issue not found: {parent_identifier}")
        scope = [label["name"] for label in (parent.get("labels") or {}).get("nodes", [])]
        scope.append((parent.get("project") or {}).get("name") or "")

        def normalize_label(value: str) -> str:
            return re.sub(r"[^a-z0-9]", "", value.casefold())

        defaults = {normalize_label(label) for label in self_assignment_labels}
        parent_title = normalize_label(parent.get("title") or "")
        assign_self = (
            any(normalize_label(label) in defaults for label in scope)
            or any(parent_title.startswith(label) for label in defaults)
        )
        if assignee == "me" or (assignee is None and assign_self):
            assignee_id = self.viewer().id
        elif assignee is None or assignee == "none":
            assignee_id = None
        else:
            assignee_id = assignee
        data = self._graphql(
            """
            mutation OrchestrationSubIssue(
              $teamId: String!, $parentId: String!, $title: String!, $description: String!,
              $assigneeId: String
            ) {
              issueCreate(input: {
                teamId: $teamId, parentId: $parentId, title: $title, description: $description,
                assigneeId: $assigneeId
              }) { success issue { identifier } }
            }
            """,
            {
                "teamId": parent["team"]["id"],
                "parentId": parent["id"],
                "title": title,
                "description": description,
                "assigneeId": assignee_id,
            },
        ).get("issueCreate") or {}
        if not data.get("success") or not data.get("issue"):
            raise OrchestrationError("Linear issueCreate did not succeed")
        return self.issue(data["issue"]["identifier"])

    def add_blocks_relation(self, blocker_id: str, blocked_id: str) -> None:
        data = self._graphql(
            """
            mutation OrchestrationBlocks($issueId: String!, $relatedIssueId: String!) {
              issueRelationCreate(input: {
                issueId: $issueId, relatedIssueId: $relatedIssueId, type: blocks
              }) { success }
            }
            """,
            {"issueId": blocker_id, "relatedIssueId": blocked_id},
        )
        if not data.get("issueRelationCreate", {}).get("success"):
            raise OrchestrationError("Linear issueRelationCreate did not succeed")

    def upload_file(self, path: Path) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        query = """
        mutation OrchestrationFileUpload($contentType: String!, $filename: String!, $size: Int!) {
          fileUpload(contentType: $contentType, filename: $filename, size: $size) {
            success
            uploadFile { uploadUrl assetUrl headers { key value } }
          }
        }
        """
        data = self._graphql(
            query,
            {"contentType": content_type, "filename": path.name, "size": path.stat().st_size},
        )
        payload = data.get("fileUpload", {})
        upload = payload.get("uploadFile") or {}
        if not payload.get("success") or not upload.get("uploadUrl") or not upload.get("assetUrl"):
            raise OrchestrationError(f"Linear fileUpload failed for {path.name}")
        headers = {item["key"]: item["value"] for item in upload.get("headers", [])}
        headers.setdefault("Content-Type", content_type)
        headers.setdefault("Cache-Control", "public, max-age=31536000")
        request = urllib.request.Request(
            upload["uploadUrl"],
            data=path.read_bytes(),
            headers=headers,
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=60):
                pass
        except urllib.error.URLError as error:
            raise OrchestrationError(f"Linear upload failed for {path.name}: {error}") from error
        return upload["assetUrl"]

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode()
        request = urllib.request.Request(
            self.API_URL,
            data=body,
            headers={"Authorization": self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.URLError as error:
            raise OrchestrationError(f"Linear request failed: {error}") from error
        if payload.get("errors"):
            raise OrchestrationError(json.dumps(payload["errors"], sort_keys=True))
        return payload.get("data") or {}


def normalize_issue_identifier(value: str) -> str:
    candidate = value.strip().rstrip("/")
    direct = re.fullmatch(r"([A-Za-z][A-Za-z0-9]+-\d+)", candidate)
    match = direct or re.search(
        r"/issue/([A-Za-z][A-Za-z0-9]+-\d+)(?:/|$)",
        candidate,
        re.IGNORECASE,
    )
    if not match:
        raise OrchestrationError(f"Expected a Linear issue ID or URL, got: {value}")
    return match.group(1).upper()


def upsert_markdown_section(document: str, heading: str, body: str) -> str:
    heading_pattern = re.compile(rf"(?im)^## {re.escape(heading)}\s*$")
    match = heading_pattern.search(document)
    replacement = f"## {heading}\n\n{body.rstrip()}\n"
    if not match:
        return f"{document.rstrip()}\n\n{replacement}" if document.strip() else replacement
    following = re.search(r"(?m)^## .+$", document[match.end() :])
    end = match.end() + (following.start() if following else len(document[match.end() :]))
    return document[: match.start()] + replacement + document[end:].lstrip("\n")


def upsert_marker_block(
    document: str,
    *,
    marker_prefix: str,
    block: str,
    body: str,
) -> str:
    start = f"<!-- {marker_prefix}:{block}:start -->"
    end = f"<!-- {marker_prefix}:{block}:end -->"
    start_count = document.count(start)
    end_count = document.count(end)
    if start_count != end_count or start_count > 1:
        raise OrchestrationError(
            f"Linear description has inconsistent {block} markers"
        )
    replacement = f"{start}\n{body.rstrip()}\n{end}"
    if start_count == 0:
        separator = "\n\n" if document.strip() else ""
        return f"{document.rstrip()}{separator}{replacement}\n"
    start_index = document.index(start)
    end_index = document.index(end, start_index) + len(end)
    return document[:start_index] + replacement + document[end_index:]


def extract_marker_block(
    document: str,
    *,
    marker_prefix: str,
    block: str,
) -> str:
    start = f"<!-- {marker_prefix}:{block}:start -->"
    end = f"<!-- {marker_prefix}:{block}:end -->"
    if document.count(start) != 1 or document.count(end) != 1:
        raise OrchestrationError(
            f"Linear description must contain exactly one {block} marker pair"
        )
    start_index = document.index(start) + len(start)
    end_index = document.index(end, start_index)
    return document[start_index:end_index].strip()


def normalize_linear_markdown(markdown: str) -> str:
    normalized_lines: list[str] = []
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        bullet = re.match(r"^(\s*)[*-]\s+(.*)$", line)
        if bullet:
            line = f"{bullet.group(1)}- {bullet.group(2)}"
        table_columns = _markdown_table_separator_column_count(line)
        if table_columns is not None:
            line = "| " + " | ".join("---" for _ in range(table_columns)) + " |"
        normalized_lines.append(line)
    return "\n".join(_normalize_linear_list_spacing(normalized_lines)).strip()


def linear_markdown_equivalent(expected: str, actual: str) -> bool:
    return normalize_linear_markdown(expected) == normalize_linear_markdown(actual)


def _normalize_linear_list_spacing(lines: list[str]) -> list[str]:
    normalized: list[str] = []
    for index, line in enumerate(lines):
        if (
            not line
            and normalized
            and normalized[-1].rstrip().endswith(":")
            and index + 1 < len(lines)
            and re.match(r"^\s*-\s+", lines[index + 1])
        ):
            continue
        normalized.append(line)
    return normalized


def _markdown_table_separator_column_count(line: str) -> int | None:
    stripped = line.strip()
    if "|" not in stripped or re.fullmatch(r"[\s|:-]+", stripped) is None:
        return None

    cells = [cell.strip() for cell in stripped.split("|")]
    nonempty_cells = [cell for cell in cells if cell]
    if not nonempty_cells or any(
        re.fullmatch(r":?-+:?", cell) is None for cell in nonempty_cells
    ):
        return None

    real_columns: list[str] = []
    for index, cell in enumerate(nonempty_cells):
        hyphen_count = cell.count("-")
        if hyphen_count >= 2:
            real_columns.append(cell)
            continue
        if index not in (0, len(nonempty_cells) - 1):
            return None

    return len(real_columns) if len(real_columns) >= 2 else None
