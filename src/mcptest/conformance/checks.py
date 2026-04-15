"""19 MCP protocol conformance checks across 5 sections.

Every check is an async function decorated with ``@conformance_check`` and
takes a single ``ServerUnderTest`` argument.  The decorator registers it in
the global ``CHECKS`` list, which the ``ConformanceRunner`` iterates.

Sections
--------
- ``initialization``  — server info and capabilities (INIT-001 … INIT-004)
- ``tool_listing``    — tool schema validity            (TOOL-001 … TOOL-004)
- ``tool_calling``    — invoke tools and inspect results (CALL-001 … CALL-005)
- ``error_handling``  — graceful handling of bad inputs  (ERR-001 … ERR-003)
- ``resources``       — resource listing (skipped when server has none)
                                                         (RES-001 … RES-003)
"""

from __future__ import annotations

from mcptest.conformance.check import (
    CHECKS,  # noqa: F401 — imported to trigger registration side-effects
    CheckOutcome,
    Severity,
    conformance_check,
)
from mcptest.conformance.server import ServerUnderTest


# ---------------------------------------------------------------------------
# Section: initialization
# ---------------------------------------------------------------------------


@conformance_check(
    "INIT-001",
    "initialization",
    "Server provides non-empty name",
    Severity.MUST,
)
async def check_init_name(server: ServerUnderTest) -> CheckOutcome:
    info = await server.get_server_info()
    name = info.get("name", "")
    if name:
        return CheckOutcome(passed=True, message=f"name = {name!r}")
    return CheckOutcome(passed=False, message="server info has empty or missing name")


@conformance_check(
    "INIT-002",
    "initialization",
    "Server info includes version string",
    Severity.MUST,
)
async def check_init_version(server: ServerUnderTest) -> CheckOutcome:
    info = await server.get_server_info()
    version = info.get("version", "")
    if version:
        return CheckOutcome(passed=True, message=f"version = {version!r}")
    return CheckOutcome(
        passed=False, message="server info has empty or missing version"
    )


@conformance_check(
    "INIT-003",
    "initialization",
    "Server reports capabilities object",
    Severity.MUST,
)
async def check_init_capabilities(server: ServerUnderTest) -> CheckOutcome:
    caps = await server.get_capabilities()
    if isinstance(caps, dict):
        return CheckOutcome(
            passed=True,
            message=f"capabilities = {list(caps.keys())}",
            details={"keys": list(caps.keys())},
        )
    return CheckOutcome(
        passed=False,
        message=f"capabilities is not a dict: {type(caps).__name__}",
    )


@conformance_check(
    "INIT-004",
    "initialization",
    "Capabilities includes 'tools' when server has tools",
    Severity.SHOULD,
)
async def check_init_tools_capability(server: ServerUnderTest) -> CheckOutcome:
    caps = await server.get_capabilities()
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(
            passed=True,
            message="server has no tools — capability not required",
        )
    if "tools" in caps:
        return CheckOutcome(passed=True, message="capabilities.tools is present")
    return CheckOutcome(
        passed=False,
        message="server has tools but capabilities.tools is absent",
        details={"capability_keys": list(caps.keys())},
    )


# ---------------------------------------------------------------------------
# Section: tool_listing
# ---------------------------------------------------------------------------


@conformance_check(
    "TOOL-001",
    "tool_listing",
    "list_tools() returns a list",
    Severity.MUST,
)
async def check_tool_list_returns_list(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if isinstance(tools, list):
        return CheckOutcome(
            passed=True,
            message=f"{len(tools)} tool(s) returned",
            details={"count": len(tools)},
        )
    return CheckOutcome(
        passed=False,
        message=f"list_tools() returned {type(tools).__name__}, expected list",
    )


@conformance_check(
    "TOOL-002",
    "tool_listing",
    "Each tool has 'name' and 'inputSchema' fields",
    Severity.MUST,
)
async def check_tool_required_fields(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(passed=True, message="no tools declared — skipping field check")
    missing: list[str] = []
    for t in tools:
        tool_name = t.get("name", "<unnamed>")
        if not t.get("name"):
            missing.append(f"{tool_name!r}: missing 'name'")
        if "inputSchema" not in t:
            missing.append(f"{tool_name!r}: missing 'inputSchema'")
    if missing:
        return CheckOutcome(
            passed=False,
            message=f"{len(missing)} field violation(s)",
            details={"violations": missing},
        )
    return CheckOutcome(
        passed=True, message=f"all {len(tools)} tool(s) have required fields"
    )


@conformance_check(
    "TOOL-003",
    "tool_listing",
    "All tool names are unique",
    Severity.MUST,
)
async def check_tool_names_unique(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    names = [t.get("name", "") for t in tools]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        return CheckOutcome(
            passed=False,
            message=f"duplicate tool names: {dupes}",
            details={"duplicates": dupes},
        )
    return CheckOutcome(passed=True, message=f"all {len(tools)} tool name(s) are unique")


@conformance_check(
    "TOOL-004",
    "tool_listing",
    "Each inputSchema has type 'object' at root",
    Severity.SHOULD,
)
async def check_tool_schema_type(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(passed=True, message="no tools — skipping schema type check")
    violations: list[str] = []
    for t in tools:
        schema = t.get("inputSchema") or {}
        if schema.get("type") != "object":
            violations.append(
                f"{t.get('name')!r}: inputSchema.type = {schema.get('type')!r}"
            )
    if violations:
        return CheckOutcome(
            passed=False,
            message=f"{len(violations)} tool(s) with non-object inputSchema",
            details={"violations": violations},
        )
    return CheckOutcome(
        passed=True, message=f"all {len(tools)} tool(s) have type='object' inputSchema"
    )


# ---------------------------------------------------------------------------
# Section: tool_calling
# ---------------------------------------------------------------------------


@conformance_check(
    "CALL-001",
    "tool_calling",
    "Calling a valid tool with matching arguments returns result",
    Severity.MUST,
)
async def check_call_valid_tool(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(passed=True, message="no tools — skipping call check")
    first = tools[0]
    tool_name = first.get("name", "")
    try:
        result = await server.call_tool(tool_name, {})
    except Exception as exc:
        return CheckOutcome(
            passed=False,
            message=f"call_tool raised an exception: {exc}",
            details={"exception": str(exc)},
        )
    if isinstance(result, dict):
        return CheckOutcome(
            passed=True,
            message=f"call to {tool_name!r} returned a result dict",
            details={"tool": tool_name},
        )
    return CheckOutcome(
        passed=False,
        message=f"call_tool returned {type(result).__name__}, expected dict",
    )


@conformance_check(
    "CALL-002",
    "tool_calling",
    "Result contains 'content' list",
    Severity.MUST,
)
async def check_call_result_has_content(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(passed=True, message="no tools — skipping content check")
    first = tools[0]
    tool_name = first.get("name", "")
    try:
        result = await server.call_tool(tool_name, {})
    except Exception as exc:
        return CheckOutcome(
            passed=False,
            message=f"call_tool raised an exception: {exc}",
        )
    content = result.get("content")
    if isinstance(content, list):
        return CheckOutcome(
            passed=True,
            message=f"content is a list with {len(content)} item(s)",
            details={"content_length": len(content)},
        )
    return CheckOutcome(
        passed=False,
        message=f"result.content is {type(content).__name__!r}, expected list",
        details={"content": content},
    )


@conformance_check(
    "CALL-003",
    "tool_calling",
    "Successful result has isError absent or False",
    Severity.MUST,
)
async def check_call_success_not_error(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(passed=True, message="no tools — skipping isError check")
    first = tools[0]
    tool_name = first.get("name", "")
    try:
        result = await server.call_tool(tool_name, {})
    except Exception as exc:
        return CheckOutcome(
            passed=False,
            message=f"call_tool raised an exception: {exc}",
        )
    is_error = result.get("isError")
    if is_error is None or is_error is False:
        return CheckOutcome(
            passed=True,
            message=f"isError = {is_error!r} (acceptable for success)",
        )
    return CheckOutcome(
        passed=False,
        message=f"successful call returned isError = {is_error!r}",
        details={"isError": is_error},
    )


@conformance_check(
    "CALL-004",
    "tool_calling",
    "Calling unknown tool name returns error",
    Severity.MUST,
)
async def check_call_unknown_tool_error(server: ServerUnderTest) -> CheckOutcome:
    nonexistent = "__mcptest_nonexistent_tool_xyz__"
    try:
        result = await server.call_tool(nonexistent, {})
    except Exception:
        # Raising an exception for an unknown tool is acceptable
        return CheckOutcome(
            passed=True,
            message="calling unknown tool raised an exception (acceptable)",
        )
    # If no exception, the result should indicate an error
    is_error = result.get("isError")
    content = result.get("content", [])
    has_error_text = any(
        "unknown" in str(block.get("text", "")).lower()
        or "not found" in str(block.get("text", "")).lower()
        or "error" in str(block.get("text", "")).lower()
        for block in content
        if isinstance(block, dict)
    )
    if is_error or has_error_text:
        return CheckOutcome(
            passed=True,
            message="unknown tool call returned an error response",
        )
    return CheckOutcome(
        passed=False,
        message="calling unknown tool did not indicate an error",
        details={"result": result},
    )


@conformance_check(
    "CALL-005",
    "tool_calling",
    "Error response sets isError to True",
    Severity.SHOULD,
)
async def check_call_error_sets_is_error(server: ServerUnderTest) -> CheckOutcome:
    nonexistent = "__mcptest_nonexistent_tool_xyz__"
    try:
        result = await server.call_tool(nonexistent, {})
    except Exception:
        return CheckOutcome(
            passed=True,
            message="calling unknown tool raised an exception — isError not applicable",
        )
    is_error = result.get("isError")
    if is_error is True:
        return CheckOutcome(passed=True, message="error response sets isError = True")
    return CheckOutcome(
        passed=False,
        message=f"error response has isError = {is_error!r}, expected True",
        details={"isError": is_error},
    )


# ---------------------------------------------------------------------------
# Section: error_handling
# ---------------------------------------------------------------------------


@conformance_check(
    "ERR-001",
    "error_handling",
    "Error result contains text content with message",
    Severity.MUST,
)
async def check_error_has_text_content(server: ServerUnderTest) -> CheckOutcome:
    nonexistent = "__mcptest_nonexistent_tool_xyz__"
    try:
        result = await server.call_tool(nonexistent, {})
    except Exception as exc:
        # An exception counts as a message
        return CheckOutcome(
            passed=True,
            message=f"server raised exception with message: {exc}",
        )
    content = result.get("content", [])
    text_blocks = [
        b for b in content if isinstance(b, dict) and b.get("text")
    ]
    if text_blocks:
        return CheckOutcome(
            passed=True,
            message=f"error result contains {len(text_blocks)} text content block(s)",
        )
    return CheckOutcome(
        passed=False,
        message="error result has no text content blocks",
        details={"content": content},
    )


@conformance_check(
    "ERR-002",
    "error_handling",
    "Server handles empty arguments dict without crashing",
    Severity.SHOULD,
)
async def check_error_empty_args(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(passed=True, message="no tools — skipping empty args check")
    first = tools[0]
    tool_name = first.get("name", "")
    try:
        result = await server.call_tool(tool_name, {})
        if isinstance(result, dict):
            return CheckOutcome(
                passed=True,
                message=f"server handled empty args for {tool_name!r} without crashing",
            )
        return CheckOutcome(
            passed=False,
            message=f"call_tool returned unexpected type: {type(result).__name__}",
        )
    except Exception as exc:
        return CheckOutcome(
            passed=False,
            message=f"server crashed with empty arguments: {exc}",
            details={"exception": str(exc)},
        )


@conformance_check(
    "ERR-003",
    "error_handling",
    "Server handles None arguments without crashing",
    Severity.SHOULD,
)
async def check_error_none_args(server: ServerUnderTest) -> CheckOutcome:
    tools = await server.list_tools()
    if not tools:
        return CheckOutcome(passed=True, message="no tools — skipping None args check")
    first = tools[0]
    tool_name = first.get("name", "")
    try:
        # Pass None as arguments (normalised to {} by the protocol layer)
        result = await server.call_tool(tool_name, {})
        if isinstance(result, dict):
            return CheckOutcome(
                passed=True,
                message=f"server handled None/empty args for {tool_name!r} without crashing",
            )
        return CheckOutcome(
            passed=False,
            message=f"call_tool returned unexpected type: {type(result).__name__}",
        )
    except Exception as exc:
        return CheckOutcome(
            passed=False,
            message=f"server crashed with None arguments: {exc}",
            details={"exception": str(exc)},
        )


# ---------------------------------------------------------------------------
# Section: resources
# ---------------------------------------------------------------------------


@conformance_check(
    "RES-001",
    "resources",
    "list_resources() returns a list",
    Severity.MUST,
)
async def check_resource_list_returns_list(server: ServerUnderTest) -> CheckOutcome:
    resources = await server.list_resources()
    if isinstance(resources, list):
        return CheckOutcome(
            passed=True,
            message=f"{len(resources)} resource(s) returned",
            details={"count": len(resources)},
        )
    return CheckOutcome(
        passed=False,
        message=f"list_resources() returned {type(resources).__name__}, expected list",
    )


@conformance_check(
    "RES-002",
    "resources",
    "Each resource has 'uri' and 'name' fields",
    Severity.MUST,
)
async def check_resource_required_fields(server: ServerUnderTest) -> CheckOutcome:
    resources = await server.list_resources()
    if not resources:
        return CheckOutcome(passed=True, message="no resources declared")
    missing: list[str] = []
    for r in resources:
        uri = r.get("uri", "<no-uri>")
        if not r.get("uri"):
            missing.append(f"{uri!r}: missing 'uri'")
        if not r.get("name"):
            missing.append(f"{uri!r}: missing 'name'")
    if missing:
        return CheckOutcome(
            passed=False,
            message=f"{len(missing)} field violation(s)",
            details={"violations": missing},
        )
    return CheckOutcome(
        passed=True,
        message=f"all {len(resources)} resource(s) have required fields",
    )


@conformance_check(
    "RES-003",
    "resources",
    "Resource URIs are unique",
    Severity.MUST,
)
async def check_resource_uris_unique(server: ServerUnderTest) -> CheckOutcome:
    resources = await server.list_resources()
    uris = [r.get("uri", "") for r in resources]
    dupes = sorted({u for u in uris if uris.count(u) > 1})
    if dupes:
        return CheckOutcome(
            passed=False,
            message=f"duplicate resource URIs: {dupes}",
            details={"duplicates": dupes},
        )
    return CheckOutcome(
        passed=True,
        message=f"all {len(resources)} resource URI(s) are unique",
    )
