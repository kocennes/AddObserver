"""SSRF-guarded fetch of Client ID Metadata Documents (CIMD, ADR-0002).

At ``/authorize`` time the authorization server dereferences a client-supplied
``client_id`` URL to learn the client's redirect URIs. That URL is exactly the kind
of externally-supplied target ``docs/SECURITY.md`` warns about ("modelin ürettiği
URL fetch edilmez... SSRF korumalı allowlist, DNS/IP yeniden doğrulama, redirect
sınırı ve özel ağ engeli uygulanır") -- the same guard applies here even though the
URL comes from an OAuth client rather than a model.

Document *content* validation (self-reference, redirect_uris same-origin) is pure
logic and lives in ``backend.src.auth.domain.validate_cimd_document``; this module
only handles the network fetch and its SSRF guard.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from typing import cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from .domain import AuthError, ClientIdentity, validate_cimd_document

MAX_RESPONSE_BYTES = 64 * 1024
FETCH_TIMEOUT_SECONDS = 5.0
#: A ``client_id`` is a URL a caller supplies to ``/authorize``; bounding it before
#: any parsing/DNS work rejects abusive input cheaply instead of letting an
#: attacker force wasted resolver/socket work with a megabyte-scale query value.
MAX_CLIENT_ID_URL_LENGTH = 2048

Resolver = Callable[[str], list[str]]


class CimdFetchError(AuthError):
    """A CIMD fetch/validation failure, reported as RFC 6749 ``invalid_client``."""

    def __init__(self, message: str) -> None:
        super().__init__("invalid_client", message)


def _default_resolve(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as error:
        raise CimdFetchError(f"CIMD host adi cozulemedi: {hostname}") from error
    # sockaddr[0] is typed as `str | int` because typeshed's getaddrinfo() stub covers
    # exotic address families (e.g. AF_PACKET) whose sockaddr starts with an int; DNS
    # resolution here only ever yields AF_INET/AF_INET6, whose sockaddr[0] is a str.
    return [cast(str, info[4][0]) for info in infos]


def _resolve_and_reject_private(hostname: str, resolve: Resolver) -> list[str]:
    """Resolve ``hostname`` once and reject it if any answer is a non-public address.

    Returning the exact resolved IPs (instead of only raising/passing) lets the
    caller connect to one of *these* addresses rather than re-resolving the
    hostname a second time -- a second lookup is what would let a DNS-rebinding
    attacker answer the validation query with a public IP and the connection
    query moments later with a private one (TOCTOU).

    ``is_private`` alone already rejects IPv4-mapped IPv6 answers (``::ffff:a.b.c.d``)
    whose embedded address is private/loopback/link-local -- ``IPv6Address.is_private``
    delegates to the mapped ``IPv4Address``'s own ``is_private`` (stdlib ``ipaddress``,
    all supported versions) rather than checking the outer address's IPv6 ranges.
    ``is_reserved`` already rejects the entire NAT64 Well-Known Prefix
    (``64:ff9b::/96``, RFC 6052) outright because that prefix falls inside the
    long-standing reserved ``::/8`` block, regardless of what IPv4 address it
    encodes. Both are covered by regression tests in ``test_auth_cimd.py`` rather
    than by extra code here.
    """
    resolved = resolve(hostname)
    if not resolved:
        raise CimdFetchError(f"CIMD host adi hicbir adrese cozulemedi: {hostname}")
    for ip_text in resolved:
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise CimdFetchError(f"CIMD hedefi private/loopback bir adrese cozuluyor: {hostname}")
    return resolved


def _pin_url_to_address(parsed: SplitResult, ip_text: str) -> str:
    """Rewrite ``client_id_url``'s authority to the literal, already-validated IP.

    The ``Host`` header and TLS SNI are set separately (by the caller) back to
    the original hostname so this is transparent to the server and to
    certificate hostname verification -- only the actual TCP destination is pinned.
    """
    ip = ipaddress.ip_address(ip_text)
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment))


def fetch_client_metadata(
    client_id_url: str,
    http_client: httpx.Client,
    *,
    resolve: Resolver = _default_resolve,
) -> ClientIdentity:
    """Fetch, SSRF-guard and validate a CIMD document.

    Only ``https://`` targets are accepted; the resolved IP(s) must not be
    private/loopback/link-local/reserved; the TCP connection is pinned to the
    exact validated IP (no second DNS lookup, closing the DNS-rebinding TOCTOU
    window); redirects are never followed (a redirect to an internal host would
    defeat the resolve check); the body is streamed and capped at
    ``MAX_RESPONSE_BYTES`` -- a malicious/compromised CIMD host controls its own
    response body and must not be able to force this process to buffer an
    unbounded amount of memory just because it eventually intends to reject the
    oversized result -- before being handed to the pure content validator.
    """
    if not client_id_url or len(client_id_url) > MAX_CLIENT_ID_URL_LENGTH:
        raise CimdFetchError(
            f"client_id en fazla {MAX_CLIENT_ID_URL_LENGTH} karakterlik bir URL olmalidir."
        )
    parsed = urlsplit(client_id_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CimdFetchError("client_id yalnizca https:// URL olabilir.")
    resolved_ips = _resolve_and_reject_private(parsed.hostname, resolve)
    pinned_url = _pin_url_to_address(parsed, resolved_ips[0])
    try:
        with http_client.stream(
            "GET",
            pinned_url,
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"Accept": "application/json", "Host": parsed.hostname},
            extensions={"sni_hostname": parsed.hostname},
        ) as response:
            if response.status_code != 200:
                raise CimdFetchError(
                    f"CIMD dokumani beklenmeyen durum kodu dondurdu: {response.status_code}"
                )
            content_type = response.headers.get("content-type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise CimdFetchError(
                    f"CIMD dokumani beklenmeyen Content-Type ile dondu: {content_type or '(bos)'}"
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise CimdFetchError("CIMD dokumani boyut sinirini asiyor.")
    except httpx.HTTPError as error:
        raise CimdFetchError(f"CIMD dokumani alinamadi: {error}") from error
    try:
        document = json.loads(bytes(body))
    except ValueError as error:
        raise CimdFetchError("CIMD dokumani gecerli JSON degil.") from error
    if not isinstance(document, dict):
        raise CimdFetchError("CIMD dokumani bir JSON object olmalidir.")
    return validate_cimd_document(client_id_url, document)
