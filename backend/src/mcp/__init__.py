"""Public Streamable HTTP MCP server (docs/MCP.md, docs/CONNECTOR_SUBMISSION.md).

Faz 1: read-only reporting tools plus draft proposal preparation; no tool ever
writes to a live Google Ads account yet (docs/PRODUCT.md -- the Faz 1.1 apply
tool stays blocked on docs/GOOGLE_API_ACCESS.md). This package never resolves
*which* Google credential belongs to a request on its own trust -- every tool
call's ``principal_id`` comes from a verified connector access token
(``backend.src.auth.deps``), never from a tool argument (docs/MCP.md -- "Tool
kimlik baglamini argumandan almaz").
"""
