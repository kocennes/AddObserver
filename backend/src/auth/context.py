"""Shared request context for every auth-adjacent router.

Split out of ``server.py`` so ``approvals_routes.py`` (the ``/approvals`` human
approval surface) can depend on ``AuthContext`` without importing ``server.py``
itself -- ``server.py``'s ``/google/callback`` in turn calls into
``approvals_routes.py`` for its web-login fallback branch, and a two-way import
between them would be circular.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import httpx
from fastapi import Request

from ..config import Settings
from .cimd import Resolver
from .google_oauth import GoogleOAuthClient
from .vault import VaultClient


@dataclass
class AuthContext:
    """Everything a route needs, assembled once by ``app.py`` (or a test)."""

    settings: Settings
    conn: sqlite3.Connection
    vault: VaultClient
    google_client: GoogleOAuthClient
    http_client: httpx.Client
    resolve: Resolver | None = None
    #: Separate, ``openid``+``email``-only Google client for the ``/approvals``
    #: browser login (docs/AUTH.md) -- never requests ``adwords`` and never
    #: touches ``vault``/``oauth_credential``/``oauth_client_grant``.
    login_google_client: GoogleOAuthClient | None = None


def get_context(request: Request) -> AuthContext:
    return request.app.state.auth_context
