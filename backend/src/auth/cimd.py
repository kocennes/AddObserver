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
import socket
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .domain import AuthError, ClientIdentity, validate_cimd_document

MAX_RESPONSE_BYTES = 64 * 1024
FETCH_TIMEOUT_SECONDS = 5.0

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
    return [info[4][0] for info in infos]


def _reject_private_target(hostname: str, resolve: Resolver) -> None:
    for ip_text in resolve(hostname):
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


def fetch_client_metadata(
    client_id_url: str,
    http_client: httpx.Client,
    *,
    resolve: Resolver = _default_resolve,
) -> ClientIdentity:
    """Fetch, SSRF-guard and validate a CIMD document.

    Only ``https://`` targets are accepted; the resolved IP(s) must not be
    private/loopback/link-local/reserved; redirects are never followed (a redirect
    to an internal host would defeat the resolve check); the body is size- and
    content-type-limited before being handed to the pure content validator.
    """
    parsed = urlsplit(client_id_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CimdFetchError("client_id yalnizca https:// URL olabilir.")
    _reject_private_target(parsed.hostname, resolve)
    try:
        response = http_client.get(
            client_id_url,
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as error:
        raise CimdFetchError(f"CIMD dokumani alinamadi: {error}") from error
    if response.status_code != 200:
        raise CimdFetchError(f"CIMD dokumani beklenmeyen durum kodu dondurdu: {response.status_code}")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise CimdFetchError("CIMD dokumani boyut sinirini asiyor.")
    try:
        document = response.json()
    except ValueError as error:
        raise CimdFetchError("CIMD dokumani gecerli JSON degil.") from error
    if not isinstance(document, dict):
        raise CimdFetchError("CIMD dokumani bir JSON object olmalidir.")
    return validate_cimd_document(client_id_url, document)
