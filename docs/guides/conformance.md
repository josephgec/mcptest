# Conformance Testing

Conformance checks verify that any MCP server correctly implements the
protocol.  19 checks across 5 sections, each tagged with an RFC 2119
severity level (MUST / SHOULD / MAY).

## Quick start

```bash
# Test a server subprocess over stdio
mcptest conformance "python my_server.py"

# Test in-process using a fixture YAML (fast, no subprocess)
mcptest conformance --fixture fixtures/my_server.yaml

# Filter by section or severity
mcptest conformance --fixture fixtures/my_server.yaml --section initialization
mcptest conformance --fixture fixtures/my_server.yaml --severity must
```

## Severity levels

| Level | Meaning |
|-------|---------|
| **MUST** | Mandatory — failing this violates the protocol |
| *SHOULD* | Strongly recommended — failing is a significant issue |
| MAY | Optional — failing is a minor concern |

Exit code is 1 when any MUST check fails (or when SHOULD checks fail with
`--fail-on-should`).

## Check sections

| Section | ID Range | Checks |
|---------|----------|--------|
| Initialization | INIT-001–004 | Server name, version, capabilities |
| Tool listing | TOOL-001–004 | Schema validity, uniqueness |
| Tool calling | CALL-001–005 | Invocation, results, error responses |
| Error handling | ERR-001–003 | Bad inputs, null arguments |
| Resources | RES-001–003 | Resource listing (auto-skipped if no resources) |

## Full reference

See the [Conformance Checks Reference](../reference/checks.md) for the full
list with per-check documentation.
