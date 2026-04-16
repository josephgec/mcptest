"""The pack registry itself.

Each pack defines a mock server fixture plus an example test suite that
exercises the typical happy path and a few failure modes. Packs are plain
strings so there's no data-file packaging problem — they ship in the
Python wheel and can be written to disk without importlib.resources.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class InstallError(Exception):
    """Raised when a pack cannot be installed (missing pack, conflict, ...)."""


@dataclass
class TestPack:
    """One named bundle of fixture + test files that users can install."""

    name: str
    description: str
    files: dict[str, str]


# ---------------------------------------------------------------------------
# filesystem
# ---------------------------------------------------------------------------

_FILESYSTEM_FIXTURE = """\
server:
  name: mock-filesystem
  version: "1.0"
  description: Mock filesystem MCP server.

tools:
  - name: fs_read
    description: Read the contents of a file.
    input_schema:
      type: object
      properties:
        path: { type: string }
      required: [path]
    responses:
      - match: { path: "/etc/passwd" }
        error: permission_denied
      - match_regex: { path: "^\\\\.\\\\." }
        error: path_traversal
      - match: { path: "/tmp/hello.txt" }
        return:
          content: "hello world\\n"
      - default: true
        error: not_found

  - name: fs_write
    description: Write content to a file.
    input_schema:
      type: object
      properties:
        path: { type: string }
        content: { type: string }
      required: [path, content]
    responses:
      - match: { path: "/readonly.txt" }
        error: permission_denied
      - default: true
        return:
          bytes_written: 12

  - name: fs_list
    description: List directory contents.
    input_schema:
      type: object
      properties:
        path: { type: string }
      required: [path]
    responses:
      - match: { path: "/tmp" }
        return:
          entries: [hello.txt, subdir]
      - match: { path: "/empty" }
        return:
          entries: []
      - default: true
        error: not_found

  - name: fs_delete
    description: Delete a file or empty directory.
    input_schema:
      type: object
      properties:
        path: { type: string }
      required: [path]
    responses:
      - default: true
        return:
          deleted: true

errors:
  - name: not_found
    error_code: -32001
    message: "No such file or directory"
  - name: permission_denied
    error_code: -32002
    message: "Permission denied"
  - name: path_traversal
    error_code: -32003
    message: "Path traversal attempt blocked"
"""

_FILESYSTEM_TESTS = """\
name: filesystem pack
description: Smoke tests for a filesystem MCP server.
fixtures:
  - ../fixtures/filesystem.yaml
# Swap this for your real agent. The built-in scripted agent just turns
# stdin `tool_name key=value` lines into MCP tool calls.
agent:
  command: python -m mcptest.agents.scripted
  timeout_s: 10
cases:
  - name: lists /tmp
    input: fs_list path=/tmp
    assertions:
      - tool_called: fs_list
      - param_matches: { tool: fs_list, param: path, value: /tmp }
      - no_errors: true

  - name: reads hello.txt
    input: fs_read path=/tmp/hello.txt
    assertions:
      - tool_called: fs_read
      - no_errors: true

  - name: blocks path traversal
    input: fs_read path=../etc/passwd
    assertions:
      - tool_called: fs_read
      - error_handled: "Path traversal"
"""


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------

_DATABASE_FIXTURE = """\
server:
  name: mock-database
  version: "1.0"
  description: Mock database MCP server.

tools:
  - name: db_query
    description: Run a read-only SQL query.
    input_schema:
      type: object
      properties:
        sql: { type: string }
        params:
          type: array
          items: {}
      required: [sql]
    responses:
      - match_regex: { sql: "(?i)drop\\\\s+table" }
        error: forbidden_ddl
      - match_regex: { sql: "(?i)select.*from users" }
        return:
          rows:
            - { id: 1, name: Alice, email: alice@example.com }
            - { id: 2, name: Bob, email: bob@example.com }
          row_count: 2
      - match_regex: { sql: "(?i)select.*from empty_table" }
        return:
          rows: []
          row_count: 0
      - default: true
        return:
          rows: []
          row_count: 0

  - name: db_execute
    description: Run a write query (INSERT/UPDATE/DELETE).
    input_schema:
      type: object
      properties:
        sql: { type: string }
      required: [sql]
    responses:
      - match_regex: { sql: "(?i)(drop|truncate)" }
        error: forbidden_ddl
      - default: true
        return:
          rows_affected: 1

  - name: db_list_tables
    responses:
      - return:
          tables: [users, orders, products]

errors:
  - name: forbidden_ddl
    error_code: -32010
    message: "DDL statements are not permitted"
  - name: connection_lost
    error_code: -32011
    message: "Database connection lost"
"""

_DATABASE_TESTS = """\
name: database pack
description: Smoke tests for a SQL database MCP server.
fixtures:
  - ../fixtures/database.yaml
# Swap this for your real agent. The built-in scripted agent just turns
# stdin `tool_name key=value` lines into MCP tool calls.
agent:
  command: python -m mcptest.agents.scripted
  timeout_s: 10
cases:
  - name: lists tables
    input: db_list_tables
    assertions:
      - tool_called: db_list_tables
      - no_errors: true

  - name: reads users
    input: db_query sql="SELECT id, name FROM users"
    assertions:
      - tool_called: db_query
      - no_errors: true

  - name: refuses DROP
    input: db_execute sql="DROP TABLE users"
    assertions:
      - tool_called: db_execute
      - error_handled: "DDL statements are not permitted"
"""


# ---------------------------------------------------------------------------
# http
# ---------------------------------------------------------------------------

_HTTP_FIXTURE = """\
server:
  name: mock-http
  version: "1.0"
  description: Mock HTTP client MCP server.

tools:
  - name: http_get
    description: Perform an HTTP GET request.
    input_schema:
      type: object
      properties:
        url: { type: string }
        headers: { type: object }
      required: [url]
    responses:
      - match_regex: { url: "^https://api\\\\.example\\\\.com/rate_limited" }
        error: rate_limited
      - match_regex: { url: "^https://api\\\\.example\\\\.com/timeout" }
        error: timeout
      - match_regex: { url: "^https://api\\\\.example\\\\.com/users" }
        return:
          status: 200
          body:
            users:
              - { id: 1, name: Alice }
              - { id: 2, name: Bob }
      - default: true
        return:
          status: 200
          body: {}

  - name: http_post
    input_schema:
      type: object
      properties:
        url: { type: string }
        body: {}
      required: [url]
    responses:
      - match_regex: { url: "malformed" }
        return:
          status: 500
          body: "<html>error</html>"
      - default: true
        return:
          status: 201
          body:
            id: 42

errors:
  - name: rate_limited
    error_code: -32020
    message: "HTTP 429: rate limit exceeded"
  - name: timeout
    error_code: -32021
    message: "HTTP request timed out"
"""

_HTTP_TESTS = """\
name: http pack
description: Smoke tests for an HTTP client MCP server.
fixtures:
  - ../fixtures/http.yaml
# Swap this for your real agent. The built-in scripted agent just turns
# stdin `tool_name key=value` lines into MCP tool calls.
agent:
  command: python -m mcptest.agents.scripted
  timeout_s: 10
cases:
  - name: fetches users
    input: http_get url=https://api.example.com/users
    assertions:
      - tool_called: http_get
      - param_matches: { tool: http_get, param: url, value: "https://api.example.com/users" }
      - no_errors: true

  - name: handles rate-limit
    input: http_get url=https://api.example.com/rate_limited
    assertions:
      - tool_called: http_get
      - error_handled: "rate limit exceeded"

  - name: posts a payload
    input: http_post url=https://api.example.com/items body={"x":1}
    assertions:
      - tool_called: http_post
      - no_errors: true
"""


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

_GIT_FIXTURE = """\
server:
  name: mock-git
  version: "1.0"
  description: Mock git MCP server.

tools:
  - name: git_commit
    input_schema:
      type: object
      properties:
        message: { type: string }
        files:
          type: array
          items: { type: string }
      required: [message]
    responses:
      - match: { message: "" }
        error: empty_message
      - default: true
        return:
          commit_sha: deadbeef1234
          files_changed: 2

  - name: git_branch
    input_schema:
      type: object
      properties:
        name: { type: string }
      required: [name]
    responses:
      - match: { name: "main" }
        error: branch_exists
      - default: true
        return:
          branch: feature-x

  - name: git_log
    input_schema:
      type: object
      properties:
        limit: { type: integer }
    responses:
      - return:
          commits:
            - { sha: aaa1, message: "Initial commit" }
            - { sha: bbb2, message: "Second commit" }

  - name: git_diff
    input_schema:
      type: object
      properties:
        target: { type: string }
    responses:
      - match_regex: { target: "conflict" }
        error: merge_conflict
      - default: true
        return:
          diff: "diff --git a/x b/x\\n-old\\n+new\\n"

errors:
  - name: empty_message
    error_code: -32030
    message: "commit message cannot be empty"
  - name: branch_exists
    error_code: -32031
    message: "branch already exists"
  - name: merge_conflict
    error_code: -32032
    message: "merge conflict detected"
"""

_GIT_TESTS = """\
name: git pack
description: Smoke tests for a git MCP server.
fixtures:
  - ../fixtures/git.yaml
# Swap this for your real agent. The built-in scripted agent just turns
# stdin `tool_name key=value` lines into MCP tool calls.
agent:
  command: python -m mcptest.agents.scripted
  timeout_s: 10
cases:
  - name: reads history
    input: git_log limit=5
    assertions:
      - tool_called: git_log
      - no_errors: true

  - name: commits work
    input: git_commit message="add feature"
    assertions:
      - tool_called: git_commit
      - param_matches: { tool: git_commit, param: message, value: "add feature" }
      - no_errors: true

  - name: refuses empty commit message
    input: git_commit message=""
    assertions:
      - tool_called: git_commit
      - error_handled: "commit message cannot be empty"
"""


# ---------------------------------------------------------------------------
# slack
# ---------------------------------------------------------------------------

_SLACK_FIXTURE = """\
server:
  name: mock-slack
  version: "1.0"
  description: Mock Slack MCP server.

tools:
  - name: slack_send_message
    input_schema:
      type: object
      properties:
        channel: { type: string }
        text: { type: string }
      required: [channel, text]
    responses:
      - match: { channel: "#private-no-access" }
        error: permission_denied
      - match: { channel: "#ghost-channel" }
        error: channel_not_found
      - match: { channel: "#engineering" }
        return:
          ok: true
          ts: "1712345678.123"
      - default: true
        return:
          ok: true
          ts: "0"

  - name: slack_list_channels
    responses:
      - return:
          channels:
            - id: C1
              name: engineering
            - id: C2
              name: random

  - name: slack_get_user
    input_schema:
      type: object
      properties:
        user_id: { type: string }
      required: [user_id]
    responses:
      - match: { user_id: "UGHOST" }
        error: user_not_found
      - default: true
        return:
          user:
            id: U123
            name: alice

errors:
  - name: permission_denied
    error_code: -32040
    message: "not_in_channel"
  - name: channel_not_found
    error_code: -32041
    message: "channel_not_found"
  - name: user_not_found
    error_code: -32042
    message: "user_not_found"
"""

_SLACK_TESTS = """\
name: slack pack
description: Smoke tests for a Slack MCP server.
fixtures:
  - ../fixtures/slack.yaml
# Swap this for your real agent. The built-in scripted agent just turns
# stdin `tool_name key=value` lines into MCP tool calls.
agent:
  command: python -m mcptest.agents.scripted
  timeout_s: 10
cases:
  - name: sends a message
    input: slack_send_message channel=#engineering text="ship it"
    assertions:
      - tool_called: slack_send_message
      - param_matches: { tool: slack_send_message, param: channel, value: "#engineering" }
      - no_errors: true

  - name: lists channels
    input: slack_list_channels
    assertions:
      - tool_called: slack_list_channels
      - no_errors: true

  - name: refuses missing channel
    input: slack_send_message channel=#ghost-channel text="hi"
    assertions:
      - tool_called: slack_send_message
      - error_handled: "channel_not_found"
"""


# ---------------------------------------------------------------------------
# github
# ---------------------------------------------------------------------------

_GITHUB_FIXTURE = """\
server:
  name: mock-github
  version: "1.0"
  description: Mock GitHub API MCP server (issues, PRs, repos).

tools:
  - name: gh_list_issues
    description: List issues on a repository.
    input_schema:
      type: object
      properties:
        repo: { type: string }
        state: { type: string }
      required: [repo]
    responses:
      - match: { repo: "ghost/missing" }
        error: not_found
      - match: { repo: "acme/api" }
        return:
          issues:
            - { number: 1, title: "Login flow 500s", state: open, labels: [bug] }
            - { number: 2, title: "Add dark mode", state: open, labels: [feature] }
            - { number: 3, title: "Fix typo in README", state: closed, labels: [docs] }
      - default: true
        return:
          issues: []

  - name: gh_create_issue
    description: Open a new issue on a repository.
    input_schema:
      type: object
      properties:
        repo: { type: string }
        title: { type: string }
        body: { type: string }
        labels:
          type: array
          items: { type: string }
      required: [repo, title]
    responses:
      - match: { repo: "readonly/archive" }
        error: archived
      - match: { repo: "ghost/missing" }
        error: not_found
      - default: true
        return:
          number: 42
          url: "https://github.com/acme/api/issues/42"
          state: open

  - name: gh_list_pulls
    description: List pull requests on a repository.
    input_schema:
      type: object
      properties:
        repo: { type: string }
        state: { type: string }
      required: [repo]
    responses:
      - match: { repo: "acme/api" }
        return:
          pulls:
            - { number: 101, title: "Refactor auth", state: open, draft: false }
            - { number: 102, title: "WIP: new routing", state: open, draft: true }
      - default: true
        return:
          pulls: []

  - name: gh_merge_pr
    description: Merge a pull request.
    input_schema:
      type: object
      properties:
        repo: { type: string }
        number: { type: integer }
      required: [repo, number]
    responses:
      - match: { number: 102 }
        error: merge_blocked  # draft
      - match: { number: 999 }
        error: merge_conflict
      - default: true
        return:
          merged: true
          sha: "abc123def456"

  - name: gh_get_repo
    description: Read a repository's metadata.
    input_schema:
      type: object
      properties:
        repo: { type: string }
      required: [repo]
    responses:
      - match: { repo: "ghost/missing" }
        error: not_found
      - default: true
        return:
          name: "api"
          full_name: "acme/api"
          private: false
          default_branch: "main"
          stargazers_count: 1337

errors:
  - name: not_found
    error_code: -32050
    message: "repository or issue not found"
  - name: archived
    error_code: -32051
    message: "repository is archived and read-only"
  - name: merge_conflict
    error_code: -32052
    message: "pull request has merge conflicts"
  - name: merge_blocked
    error_code: -32053
    message: "pull request is a draft and cannot be merged"
  - name: rate_limited
    error_code: -32054
    message: "GitHub API rate limit exceeded"
"""

_GITHUB_TESTS = """\
name: github pack
description: Smoke tests for a GitHub API MCP server.
fixtures:
  - ../fixtures/github.yaml
# Swap this for your real agent. The built-in scripted agent just turns
# stdin `tool_name key=value` lines into MCP tool calls.
agent:
  command: python -m mcptest.agents.scripted
  timeout_s: 10
cases:
  - name: lists open issues
    input: gh_list_issues repo=acme/api state=open
    assertions:
      - tool_called: gh_list_issues
      - param_matches: { tool: gh_list_issues, param: repo, value: "acme/api" }
      - no_errors: true

  - name: opens a bug report
    input: gh_create_issue repo=acme/api title="login 500" body="stacktrace..."
    assertions:
      - tool_called: gh_create_issue
      - no_errors: true

  - name: refuses to file on missing repo
    input: gh_create_issue repo=ghost/missing title="hi"
    assertions:
      - tool_called: gh_create_issue
      - error_handled: "not found"

  - name: merges a clean PR
    input: gh_merge_pr repo=acme/api number=101
    assertions:
      - tool_called: gh_merge_pr
      - no_errors: true

  - name: refuses to merge a draft
    input: gh_merge_pr repo=acme/api number=102
    assertions:
      - tool_called: gh_merge_pr
      - error_handled: "draft"
"""


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def _pack(name: str, description: str, fixture: str, tests: str) -> TestPack:
    return TestPack(
        name=name,
        description=description,
        files={
            f"fixtures/{name}.yaml": fixture,
            f"tests/test_{name}.yaml": tests,
        },
    )


PACKS: dict[str, TestPack] = {
    "filesystem": _pack(
        "filesystem",
        "Read/write/list/delete a mock filesystem, with path-traversal and permission errors.",
        _FILESYSTEM_FIXTURE,
        _FILESYSTEM_TESTS,
    ),
    "database": _pack(
        "database",
        "Run SELECT / execute write queries against a mock SQL database, with DDL refusal and empty-result cases.",
        _DATABASE_FIXTURE,
        _DATABASE_TESTS,
    ),
    "http": _pack(
        "http",
        "GET/POST to a mock HTTP API with rate-limit, timeout, and malformed-response scenarios.",
        _HTTP_FIXTURE,
        _HTTP_TESTS,
    ),
    "git": _pack(
        "git",
        "Commit / branch / diff / log against a mock git repository, with merge-conflict simulation.",
        _GIT_FIXTURE,
        _GIT_TESTS,
    ),
    "slack": _pack(
        "slack",
        "Send messages, list channels, look up users on a mock Slack workspace, with permission errors.",
        _SLACK_FIXTURE,
        _SLACK_TESTS,
    ),
    "github": _pack(
        "github",
        "List/open issues, list/merge PRs, read repo metadata on a mock GitHub API, with not-found, archived, and merge-conflict errors.",
        _GITHUB_FIXTURE,
        _GITHUB_TESTS,
    ),
}


def list_packs() -> list[str]:
    """Return the names of all shipped packs, sorted."""
    return sorted(PACKS)


def get_pack(name: str) -> TestPack:
    try:
        return PACKS[name]
    except KeyError as exc:
        raise InstallError(
            f"unknown pack {name!r}; available: {list_packs()}"
        ) from exc


def install_pack(
    name: str,
    dest: str | Path,
    *,
    force: bool = False,
) -> list[str]:
    """Write a pack's files under `dest`; return relative paths written.

    Refuses to overwrite existing files unless `force=True` so accidental
    double-installs don't blow away hand-edits.
    """
    pack = get_pack(name)
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for rel, contents in pack.files.items():
        target = root / rel
        if target.exists() and not force:
            raise InstallError(
                f"{target} already exists; pass --force to overwrite"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        written.append(rel)

    return written
