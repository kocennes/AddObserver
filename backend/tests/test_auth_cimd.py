"""Tests for backend.src.auth.cimd -- SSRF guard + CIMD fetch, no real network/DNS."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from backend.src.auth.cimd import (
    MAX_CLIENT_ID_URL_LENGTH,
    MAX_RESPONSE_BYTES,
    CimdFetchError,
    fetch_client_metadata,
)
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
            fetch_client_metadata(
                "http://claude.ai/oauth/hosted-client-metadata", client, resolve=_public_resolver
            )

    def test_loopback_ip_literal_rejected_without_network(self) -> None:
        """127.0.0.1 is an IP literal -- no DNS lookup needed, so this is fully offline."""
        client = _client_returning({"client_id": "https://127.0.0.1/cimd", "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata("https://127.0.0.1/cimd", client)

    def test_link_local_metadata_ip_literal_rejected(self) -> None:
        """169.254.169.254 is the classic cloud-metadata SSRF target."""
        client = _client_returning(
            {"client_id": "https://169.254.169.254/cimd", "redirect_uris": []}
        )
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata("https://169.254.169.254/cimd", client)

    def test_hostname_resolving_to_private_ip_rejected(self) -> None:
        def private_resolver(_hostname: str) -> list[str]:
            return ["10.0.0.5"]

        client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=private_resolver)

    def test_oversized_document_rejected(self) -> None:
        huge_document = {
            "client_id": CLIENT_ID_URL,
            "redirect_uris": ["x" * (MAX_RESPONSE_BYTES + 10)],
        }
        client = _client_returning(huge_document)
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)

    def test_oversized_body_is_rejected_without_buffering_past_the_limit(self) -> None:
        """A malicious/compromised CIMD host controls its own response body -- the
        fetch must stop reading as soon as the cap is crossed instead of first
        buffering the entire (attacker-chosen, unbounded) body into memory and
        only then rejecting it. Simulated with a streaming handler that raises if
        asked for more than one cap's worth of chunks."""
        chunk = b"x" * 1024
        chunks_yielded = 0

        def handler(request: httpx.Request) -> httpx.Response:
            def body_stream():
                nonlocal chunks_yielded
                # Enough chunks to exceed MAX_RESPONSE_BYTES several times over --
                # a fetch that buffers everything before checking size would
                # exhaust this generator; a correctly bounded fetch stops early.
                for _ in range((MAX_RESPONSE_BYTES // len(chunk)) * 10 + 10):
                    chunks_yielded += 1
                    yield chunk

            return httpx.Response(200, content=body_stream())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)
        max_expected_chunks = (MAX_RESPONSE_BYTES // len(chunk)) + 2
        self.assertLess(
            chunks_yielded,
            max_expected_chunks,
            "fetch_client_metadata read far more of the body than the size cap allows -- "
            "it is buffering the full response before checking MAX_RESPONSE_BYTES",
        )

    def test_non_200_status_rejected(self) -> None:
        client = _client_returning(
            {"client_id": CLIENT_ID_URL, "redirect_uris": []}, status_code=302
        )
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)

    def test_invalid_json_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)

    def test_non_json_content_type_rejected(self) -> None:
        """A CIMD host returning valid JSON bytes but a non-JSON Content-Type (e.g. an
        HTML error page or a misconfigured static host) must not be accepted -- the
        Content-Type is part of the document contract, not just its parseable bytes."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b'{"client_id": "%s", "redirect_uris": []}' % CLIENT_ID_URL.encode(),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)

    def test_json_content_type_with_charset_parameter_accepted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json; charset=utf-8"},
                content=b'{"client_id": "%s", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}'
                % CLIENT_ID_URL.encode(),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        identity = fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)
        self.assertEqual(identity.client_id, CLIENT_ID_URL)

    def test_oversized_client_id_rejected_before_any_dns_or_network_call(self) -> None:
        calls: list[str] = []

        def counting_resolver(hostname: str) -> list[str]:
            calls.append(hostname)
            return ["93.184.216.34"]

        oversized = "https://claude.ai/" + "x" * MAX_CLIENT_ID_URL_LENGTH
        client = _client_returning({"client_id": oversized, "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(oversized, client, resolve=counting_resolver)
        self.assertEqual(calls, [])

    def test_empty_client_id_rejected(self) -> None:
        client = _client_returning(None)
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata("", client, resolve=_public_resolver)


class CimdFetchDnsRebindingTests(unittest.TestCase):
    """DNS-rebinding TOCTOU: a hostname must not be resolved twice, once to pass the
    SSRF check and again (with a different, attacker-controlled answer) to actually
    connect. ``fetch_client_metadata`` calls ``resolve`` exactly once and pins the
    outgoing request to that answer.
    """

    def test_resolver_is_called_exactly_once(self) -> None:
        calls: list[str] = []

        def counting_resolver(hostname: str) -> list[str]:
            calls.append(hostname)
            return ["93.184.216.34"]

        client = _client_returning(
            {
                "client_id": CLIENT_ID_URL,
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            }
        )
        fetch_client_metadata(CLIENT_ID_URL, client, resolve=counting_resolver)
        self.assertEqual(calls, [urlsplit(CLIENT_ID_URL).hostname])

    def test_request_is_pinned_to_the_resolved_ip_not_the_hostname(self) -> None:
        """A second, real DNS lookup performed by the HTTP transport itself would
        reopen the rebinding window; the request actually sent must already target
        the validated IP literal, with the hostname preserved only for Host/SNI."""
        seen_urls: list[str] = []
        seen_hosts: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            seen_hosts.append(request.headers.get("host"))
            return httpx.Response(
                200,
                json={
                    "client_id": CLIENT_ID_URL,
                    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)
        self.assertEqual(seen_urls, ["https://93.184.216.34/oauth/hosted-client-metadata"])
        self.assertEqual(seen_hosts, ["claude.ai"])

    def test_ipv6_resolved_address_is_bracketed_in_pinned_url(self) -> None:
        def ipv6_resolver(_hostname: str) -> list[str]:
            return ["2606:4700:4700::1111"]

        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "client_id": CLIENT_ID_URL,
                    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetch_client_metadata(CLIENT_ID_URL, client, resolve=ipv6_resolver)
        self.assertEqual(seen_urls, ["https://[2606:4700:4700::1111]/oauth/hosted-client-metadata"])

    def test_mixed_public_and_private_answers_are_rejected(self) -> None:
        """A hostname resolving to one public and one private/loopback address must
        be rejected outright, not merely connected to whichever answer looks safe."""

        def mixed_resolver(_hostname: str) -> list[str]:
            return ["93.184.216.34", "127.0.0.1"]

        client = _client_returning(
            {
                "client_id": CLIENT_ID_URL,
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            }
        )
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=mixed_resolver)

    def test_empty_resolver_answer_is_rejected(self) -> None:
        def empty_resolver(_hostname: str) -> list[str]:
            return []

        client = _client_returning(
            {
                "client_id": CLIENT_ID_URL,
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            }
        )
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=empty_resolver)


class CimdFetchIpv6SsrfBypassTests(unittest.TestCase):
    """IPv6-specific SSRF bypasses that were previously untested even though the
    existing ``is_private``/``is_loopback``/.../``is_reserved`` check in
    ``_resolve_and_reject_private`` already guards against them: literal
    special-purpose addresses, IPv4 addresses embedded inside an IPv6 address
    (IPv4-mapped or NAT64 Well-Known-Prefix), and hostname strings crafted to look
    like something other than what the resolver actually returns. These are
    regression tests for existing behaviour, not new code -- see the two cases
    verified below and documented in ``_resolve_and_reject_private``'s docstring:
    ``is_private`` already delegates to the embedded IPv4 address for mapped
    (``::ffff:a.b.c.d``) answers on every supported Python version, and
    ``is_reserved`` already rejects the entire ``64:ff9b::/96`` NAT64 range
    outright because it falls inside the long-standing reserved ``::/8`` block.
    """

    def test_ipv6_loopback_literal_rejected_without_network(self) -> None:
        client = _client_returning({"client_id": "https://[::1]/cimd", "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata("https://[::1]/cimd", client)

    def test_ipv6_unique_local_address_rejected(self) -> None:
        def unique_local_resolver(_hostname: str) -> list[str]:
            return ["fd12:3456:789a::1"]

        client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=unique_local_resolver)

    def test_ipv6_link_local_address_rejected(self) -> None:
        def link_local_resolver(_hostname: str) -> list[str]:
            return ["fe80::1"]

        client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=link_local_resolver)

    def test_ipv4_mapped_loopback_rejected(self) -> None:
        """``::ffff:127.0.0.1`` embeds 127.0.0.1 in the IPv4-mapped (RFC 4291) form.
        ``IPv6Address.is_private`` delegates to the mapped ``IPv4Address``'s own
        ``is_private`` (true on every Python 3.11+ patch release -- this delegation
        predates the unrelated 3.13 fixes to ``is_loopback``/``is_link_local``/etc
        for mapped addresses, cpython gh-117566), so the existing check already
        rejects this without needing to inspect the embedded address separately."""

        def mapped_resolver(_hostname: str) -> list[str]:
            return ["::ffff:127.0.0.1"]

        client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=mapped_resolver)

    def test_ipv4_mapped_link_local_metadata_address_rejected(self) -> None:
        def mapped_resolver(_hostname: str) -> list[str]:
            return ["::ffff:169.254.169.254"]

        client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []})
        with self.assertRaises(CimdFetchError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=mapped_resolver)

    def test_nat64_well_known_prefix_rejected_regardless_of_embedded_address(self) -> None:
        """``64:ff9b::/96`` is the NAT64 Well-Known Prefix (RFC 6052); addresses in
        it embed an IPv4 address in their low 32 bits and, on a network with a
        NAT64/DNS64 gateway (common IPv6-only cloud egress), are transparently
        translated to that embedded destination. The whole prefix falls inside the
        long-standing reserved ``::/8`` block, so ``is_reserved`` already rejects
        every address in it -- including one embedding a public IPv4 address like
        93.184.216.34, which this asserts is rejected exactly like the others
        rather than silently let through because its payload looks harmless."""
        embedding = {
            "127.0.0.1 (loopback)": "64:ff9b::7f00:1",
            "169.254.169.254 (link-local metadata)": "64:ff9b::a9fe:a9fe",
            "93.184.216.34 (public)": "64:ff9b::5db8:d822",
        }
        for label, address in embedding.items():
            with self.subTest(embeds=label):

                def nat64_resolver(_hostname: str, address: str = address) -> list[str]:
                    return [address]

                client = _client_returning({"client_id": CLIENT_ID_URL, "redirect_uris": []})
                with self.assertRaises(CimdFetchError):
                    fetch_client_metadata(CLIENT_ID_URL, client, resolve=nat64_resolver)

    def test_encoded_or_alternate_ip_hostname_text_cannot_bypass_the_resolved_check(self) -> None:
        """The guard must key off what the resolver *returns*, never the literal
        ``client_id`` hostname text -- so alternate/obfuscated numeric-IP spellings
        (hex, octal, decimal, percent-encoded dots) that a naive string-based
        allowlist might miss are rejected exactly like a plain hostname would be,
        as soon as the resolver answers with a private/loopback address."""
        alternate_forms = [
            "https://0x7f.0.0.1/cimd",
            "https://0177.0.0.1/cimd",
            "https://2130706433/cimd",
            "https://127%2e0%2e0%2e1/cimd",
        ]

        def loopback_resolver(_hostname: str) -> list[str]:
            return ["127.0.0.1"]

        for url in alternate_forms:
            with self.subTest(url=url):
                client = _client_returning({"client_id": url, "redirect_uris": []})
                with self.assertRaises(CimdFetchError):
                    fetch_client_metadata(url, client, resolve=loopback_resolver)

    def test_userinfo_authority_confusion_resolves_the_real_host_not_the_decoy(self) -> None:
        """A URL of the form ``https://decoy@attacker/x`` must be resolved and
        connected to using the actual authority host (after the last ``@``), never
        the ``decoy`` text before it -- a classic authority-parsing bypass class in
        SSRF guards that only regex/string-match the URL instead of parsing it."""
        confusing_url = "https://claude.ai@attacker.example.com/cimd"
        seen_hostnames: list[str] = []

        def recording_resolver(hostname: str) -> list[str]:
            seen_hostnames.append(hostname)
            return ["93.184.216.34"]

        client = _client_returning(
            {
                "client_id": confusing_url,
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            }
        )
        with self.assertRaises(AuthError):
            # Rejected downstream by content validation (client_id self-reference
            # mismatch), but the important assertion is *which* host was resolved.
            fetch_client_metadata(confusing_url, client, resolve=recording_resolver)
        self.assertEqual(seen_hostnames, ["attacker.example.com"])


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
            {
                "client_id": "https://attacker.example.com/cimd",
                "redirect_uris": ["https://claude.ai/x"],
            }
        )
        with self.assertRaises(AuthError):
            fetch_client_metadata(CLIENT_ID_URL, client, resolve=_public_resolver)


if __name__ == "__main__":
    unittest.main()
