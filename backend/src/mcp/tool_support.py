"""Shared MCP tool infrastructure used by every tool-registration module.

Kept separate from ``tools.py``/``proposals.py`` so the annotation constants,
principal lookup and schema-closing helper have exactly one definition each --
docs/MCP.md's closed-schema and "tool kimlik baglamini argumandan almaz"
rules apply identically to every tool, read or write.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from .auth_bridge import get_authenticated_principal_from_request

#: Reads a live Google Ads account through the adapter (``openWorldHint=True``).
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)

#: Reads only our own DB -- no Google Ads call (``openWorldHint=False``).
READ_ONLY_LOCAL = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

#: Creates a new local record (e.g. a draft proposal). Never touches Google Ads,
#: never overwrites or deletes an existing resource, and each call is a distinct
#: creation, so it is neither read-only, destructive, nor idempotent.
LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

#: Reads a live Google Ads account (``openWorldHint=True``) and mirrors the result
#: into our own local ``ads_account`` bookkeeping. Never mutates an actual Google
#: Ads resource (``destructiveHint=False``), and repeated calls with the same
#: underlying Google state converge to the same local snapshot
#: (``idempotentHint=True``) -- but it does write locally, so ``readOnlyHint=False``.
LOCAL_SYNC = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)


def authenticated_principal_id(ctx: Context) -> str:
    """Return the caller's principal id from its verified connector access token.

    Never derived from a tool argument (docs/MCP.md -- "Tool kimlik baglamini
    argumandan almaz; audience-bound connector token subject'inden turetir").
    """
    request = ctx.request_context.request
    if request is None:
        raise RuntimeError("MCP istegi bir HTTP Request'e bagli degil.")
    return get_authenticated_principal_from_request(request).principal_id


def close_input_schema(mcp: FastMCP, tool_name: str) -> None:
    """Force ``additionalProperties: false`` on a registered tool's input schema.

    docs/MCP.md requires closed input/output schemas; FastMCP's auto-generated
    argument model has no ``extra='forbid'`` by default, so this is the one
    place that gap is closed, right after registration -- and it is real
    enforcement, not cosmetic: the low-level server validates every call's
    arguments against this exact schema via ``jsonschema`` before invoking the
    tool function.
    """
    tool = mcp._tool_manager.get_tool(tool_name)  # noqa: SLF001 -- FastMCP has no public schema-mutation API
    assert tool is not None, f"Tool {tool_name!r} kayitli degil."  # nosec B101 -- startup-time registration invariant
    tool.parameters["additionalProperties"] = False


def set_output_schema(mcp: FastMCP, tool_name: str, schema: dict[str, object]) -> None:
    """Replace the SDK's permissive inferred output schema with a closed contract."""
    tool = mcp._tool_manager.get_tool(tool_name)  # noqa: SLF001 -- no public schema API
    assert tool is not None, f"Tool {tool_name!r} kayitli degil."  # nosec B101
    if tool.fn_metadata.output_model is None:
        raise RuntimeError(f"Tool {tool_name!r} structured output etkin degil")
    tool.fn_metadata.output_schema = schema
