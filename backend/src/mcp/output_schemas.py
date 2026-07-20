"""Closed MCP output schemas for the Directory v1 tool inventory."""

from __future__ import annotations

from typing import Any

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_NUMBER = {"type": "number"}
_NULLABLE_STRING = {"anyOf": [_STRING, {"type": "null"}]}


def _closed_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


ACCOUNT = _closed_object(
    {
        "customer_id": _STRING,
        "login_customer_id": _NULLABLE_STRING,
        "status": _STRING,
    },
    ["customer_id", "login_customer_id", "status"],
)
ACCOUNTS_OUTPUT = _closed_object({"result": {"type": "array", "items": ACCOUNT}}, ["result"])

_METRICS = {
    "impressions": _INTEGER,
    "clicks": _INTEGER,
    "cost_micros": _INTEGER,
    "conversions": _NUMBER,
}


def _report_output(row_properties: dict[str, Any]) -> dict[str, Any]:
    row = _closed_object(row_properties, list(row_properties))
    return _closed_object(
        {
            "rows": {"type": "array", "items": row},
            "next_page_token": _NULLABLE_STRING,
        },
        ["rows", "next_page_token"],
    )


CAMPAIGN_REPORT_OUTPUT = _report_output(
    {
        "date": _STRING,
        "campaign_id": _STRING,
        "campaign_name": _STRING,
        "campaign_status": _STRING,
        **_METRICS,
    }
)
AD_GROUP_REPORT_OUTPUT = _report_output(
    {
        "date": _STRING,
        "campaign_id": _STRING,
        "ad_group_id": _STRING,
        "ad_group_name": _STRING,
        "ad_group_status": _STRING,
        **_METRICS,
    }
)
KEYWORD_REPORT_OUTPUT = _report_output(
    {
        "date": _STRING,
        "campaign_id": _STRING,
        "ad_group_id": _STRING,
        "criterion_id": _STRING,
        "keyword_text": _STRING,
        "keyword_match_type": _STRING,
        "keyword_status": _STRING,
        **_METRICS,
    }
)

_STATUS_SNAPSHOT = _closed_object({"status": _STRING}, ["status"])
_BUDGET_SNAPSHOT = _closed_object({"amount_micros": _INTEGER}, ["amount_micros"])
PROPOSAL_PAYLOAD = _closed_object(
    {
        "schema_version": _INTEGER,
        "type": {
            "type": "string",
            "enum": ["campaign_pause", "campaign_enable", "campaign_budget_update"],
        },
        "campaign_id": _STRING,
        "rationale": _STRING,
        "before": {"oneOf": [_STATUS_SNAPSHOT, _BUDGET_SNAPSHOT]},
        "after": {"oneOf": [_STATUS_SNAPSHOT, _BUDGET_SNAPSHOT]},
    },
    ["schema_version", "type", "campaign_id", "rationale", "before", "after"],
)
PROPOSAL_OUTPUT = _closed_object(
    {
        "proposal_id": _STRING,
        "customer_id": _STRING,
        "status": _STRING,
        "proposal_hash": _STRING,
        "expires_at": _STRING,
        "payload": PROPOSAL_PAYLOAD,
    },
    ["proposal_id", "customer_id", "status", "proposal_hash", "expires_at", "payload"],
)
PROPOSAL_LIST_OUTPUT = _closed_object(
    {
        "proposals": {"type": "array", "items": PROPOSAL_OUTPUT},
        "has_more": {"type": "boolean"},
    },
    ["proposals", "has_more"],
)

TOOL_OUTPUT_SCHEMAS = {
    "list_accessible_accounts": ACCOUNTS_OUTPUT,
    "get_campaign_performance": CAMPAIGN_REPORT_OUTPUT,
    "get_ad_group_performance": AD_GROUP_REPORT_OUTPUT,
    "get_keyword_performance": KEYWORD_REPORT_OUTPUT,
    "prepare_proposal": PROPOSAL_OUTPUT,
    "get_proposal": PROPOSAL_OUTPUT,
    "list_proposals": PROPOSAL_LIST_OUTPUT,
}
