"""Tests for backend.src.auth.cimd -- SSRF guard + CIMD fetch, no real network/DNS."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx

from backend.src.auth.cimd import CimdFetchError, MAX_RESPONSE_BYTES, fetch_client_metadata
from backend.src.auth.domain import AuthError

CLIENT_ID_URL = "https://claude.ai/oauth/hosted-client-metadata"


def _public_resolver(_hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _client_returning(document: object | None, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if document is None:
            return httpx.Response(status_code)
        return httpx.Response(status_code, json=document)

    return httpx.Client(transport=httpx.MockTransport(handler))


class CimdFetchSecurityTests(unittest.TestCase):
    def test_non_https_scheme_rejected(self) -> None:
        client = _client_returning({"client_id": "http://claude.ai/x", "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata("http://claude.ai/oauth/hosted-client-metadata", client, resolve=_public_resolver)

    def test_loopback_ip_literal_rejected_without_network(self) -> None:
        """127.0.0.1 is an IP literal -- no DNS lookup needed, so this is fully offline."""
        client = _client_returning({"client_id": "https://127.0.0.1/cimd", "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata("https://127.0.0.1/cimd", client)

    def test_link_local_metadata_ip_literal_rejected(self) -> None:
        """169.254.169.254 is the classic cloud-metadata SSRF target."""
        client = _client_returning({"client_id": "https://169.254.169.254/cimd", "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata("https://169.254.169.254/cimd", client)

    def test_hostname_resolving_to_private_ip_rejected(self) -> None:
        def private_resolver(_hostname: str) -> list[str]:
            return ["10.0.0.5"]

        client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=private_resolver)

    def test_oversized_document_rejected(self) -> None:
        huge_document = {"client_id": CLIENT_ID_URL, "redirect_uris": ["x" * (MAX_RESPONSE_BYTES + 10)]}
        client = _client_returning(huge_document)
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)

    def test_non_200_status_rejected(self) -> None:
        client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []}, status_code=302)
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)

    def test_invalid_json_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)


class CimdFetchHappyPathTests(unittest.TestCase):
    def test_valid_document_resolves_to_client_identity(self) -> None:
        client = _client_returning(
            {
                "client_id": CLIENT_ID_URL,
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
            }
        )
        identity = fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)
        self.assertEqual(identity.client_id, CLIENT_ID_URL)
        self.assertIn("https://claude.ai/api/mcp/auth_callback", identity.redirect_uris)

    def test_non_self_referential_document_rejected(self) -> None:
        """Content validation is delegated to auth.domain, which raises the base AuthError."""
        client = _client_returning(
            {"client_id": "https://attacker.example.com/cimd", "redirect_uris": ["https://claude.ai/x"]}
        )
        with self.assertRaises(AuthError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)


if __name__ == "__main__":
    unittest.main()
