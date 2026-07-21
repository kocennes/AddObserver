"""RFC 9457-style problem response helpers for public HTTP surfaces."""

from __future__ import annotations

from fastapi.responses import JSONResponse

PROBLEM_JSON = "application/problem+json"


def problem_body(
    *,
    status_code: int,
    title: str,
    detail: str,
    code: str,
    correlation_id: str | None = None,
) -> dict[str, object]:
    """Build the stable, safe error body used at public HTTP boundaries."""
    content: dict[str, object] = {
        "type": "about:blank",
        "title": title,
        "status": status_code,
        "detail": detail,
        "code": code,
    }
    if correlation_id is not None:
        content["correlation_id"] = correlation_id
    return content


def problem_response(
    *,
    status_code: int,
    title: str,
    detail: str,
    code: str,
    correlation_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Return a JSONResponse with the connector's public problem+json shape."""
    return JSONResponse(
        status_code=status_code,
        media_type=PROBLEM_JSON,
        content=problem_body(
            status_code=status_code,
            title=title,
            detail=detail,
            code=code,
            correlation_id=correlation_id,
        ),
        headers=headers,
    )
