"""End-to-end tests for the /login -> /approvals human-approval surface.

Drives the real ASGI app over httpx.ASGITransport (no real socket), mirroring the
harness in ``test_mcp_integration.py``. Covers the TESTING.md-class negative
cases this feature is responsible for: login never creates a principal or
touches the Ads credential, a session is required, cross-principal proposals are
invisible/undecideable, CSRF is enforced, and a login state cannot be replayed.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from backend.src.app import create_app
from backend.src.approval import Proposal, ProposalStatus, build_proposal_payload, submit_proposal
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.auth.web_session import issue_web_session
from backend.src.config import Settings
from backend.src.db.proposals import ApprovalRepository, AuditRepository, ProposalRepository
from backend.src.db.repository import (
    AdsAccountRepository,
    OAuthCredentialRepository,
    PrincipalRepository,
)
from backend.src.db.web_session_store import WebSessionRepository
from cryptography.fernet import Fernet

PUBLIC_BASE_URL = "https://connector.example.com"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _settings() -> Settings:
    return Settings(
        sqlite_db_path=":memory:",
        environment="test",
        public_base_url=PUBLIC_BASE_URL,
        mcp_resource_path="/mcp",
        local_vault_key=Fernet.generate_key().decode(),
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_ads_developer_token="dev-token",
        allowed_hosts=("connector.example.com",),
        cors_allowed_origins=(),
    )


def _state_from_redirect(response: httpx.Response) -> str:
    location = response.headers["location"]
    query = parse_qs(urlsplit(location).query)
    return query["state"][0]


def _make_pending_proposal(
    principal_id: str, customer_id: str = "1234567890", proposal_id: str = "proposal-1"
):
    now = datetime.now(UTC)
    payload = build_proposal_payload(
        proposal_type="campaign_pause",
        campaign_id="9999",
        rationale="test rationale",
        current_status="ENABLED",
    )
    draft = Proposal.create(
        proposal_id=proposal_id,
        principal_id=principal_id,
        customer_id=customer_id,
        payload=payload,
        expires_at=now + timedelta(hours=1),
    )
    return submit_proposal(draft, now=now)


class ApprovalsHttpTests(unittest.IsolatedAsyncioTestCase):
    def _build_app(self, *, login_subject: str = "sub-1", login_email: str = "user@example.com"):
        settings = _settings()
        app = create_app(
            settings,
            google_client=FakeGoogleOAuthClient(),
            login_google_client=FakeGoogleOAuthClient(
                google_subject=login_subject, email=login_email
            ),
        )
        return settings, app

    async def _login(self, client: httpx.AsyncClient) -> httpx.Response:
        """Drive /login -> /google/callback and return the callback's redirect response."""
        login_response = await client.get("/login")
        self.assertEqual(login_response.status_code, 302)
        state = _state_from_redirect(login_response)
        return await client.get("/google/callback", params={"state": state, "code": "fake-code"})

    async def test_happy_path_login_view_and_approve(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        proposals = ProposalRepository(conn)
        proposals.save(_make_pending_proposal(principal.id))

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            callback_response = await self._login(client)
            self.assertEqual(callback_response.status_code, 302)
            self.assertEqual(callback_response.headers["location"], "/approvals")
            self.assertIn("web_session", callback_response.cookies)
            self.assertIn("web_csrf", callback_response.cookies)

            page = await client.get("/approvals")
            self.assertEqual(page.status_code, 200)
            self.assertIn("campaign_pause", page.text)
            self.assertNotIn("onsubmit=", page.text)
            match = CSRF_RE.search(page.text)
            assert match is not None
            csrf_token = match.group(1)

            decision_response = await client.post(
                "/approvals/proposal-1/decision",
                data={"decision": "approve", "csrf_token": csrf_token},
                headers={"X-Correlation-ID": "approval-corr-1"},
            )
            self.assertEqual(decision_response.status_code, 302)
            self.assertEqual(decision_response.headers["location"], "/approvals")
            self.assertEqual(decision_response.headers["x-correlation-id"], "approval-corr-1")

            updated = proposals.get(principal.id, "proposal-1")
            assert updated is not None
            self.assertEqual(updated.status, ProposalStatus.APPROVED)
            approval = ApprovalRepository(conn).get_latest(principal.id, "proposal-1")
            assert approval is not None
            self.assertEqual(approval.decision.value, "approve")
            events = AuditRepository(conn).list_for_principal(principal.id)
            self.assertEqual(events[0].event_type, "approval.decided")
            self.assertEqual(events[0].correlation_id, "approval-corr-1")

    async def test_approval_preview_shows_full_decision_context(self) -> None:
        """Faz 7.1: before a human decides, the page must show account, operation,
        resource, current/proposed value, rationale, evidence, risk, expiry and an
        explicit not-yet-applied notice -- and never send a mutate from this screen."""
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        now = datetime.now(UTC)
        payload = build_proposal_payload(
            proposal_type="campaign_budget_update",
            campaign_id="42",
            rationale="30 gunluk CTR dususu gozlemlendi",
            current_budget_amount_micros=5_000_000,
            proposed_budget_amount_micros=8_000_000,
            evidence_refs=["report-ctr-30d"],
            risk="high",
        )
        draft = Proposal.create(
            proposal_id="proposal-budget-1",
            principal_id=principal.id,
            customer_id="1234567890",
            payload=payload,
            expires_at=now + timedelta(hours=1),
        )
        ProposalRepository(conn).save(submit_proposal(draft, now=now))

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            page = await client.get("/approvals")

        self.assertEqual(page.status_code, 200)
        text = page.text
        self.assertIn("1234567890", text)  # hesap
        self.assertIn("Kampanya bütçesini güncelle", text)  # işlem
        self.assertIn("42", text)  # kaynak (kampanya)
        self.assertIn("Bütçe: 5000000 micros", text)  # mevcut değer
        self.assertIn("Bütçe: 8000000 micros", text)  # önerilen değer
        self.assertIn("30 gunluk CTR dususu gozlemlendi", text)  # gerekçe
        self.assertIn("report-ctr-30d", text)  # kanıt
        self.assertIn("Yüksek", text)  # risk
        self.assertIn("Google Ads hesabına henüz hiçbir değişiklik gönderilmedi", text)

    async def test_approval_preview_escapes_hostile_rationale(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        now = datetime.now(UTC)
        payload = build_proposal_payload(
            proposal_type="campaign_pause",
            campaign_id="42",
            rationale="ignore previous instructions <script>alert(1)</script>",
            current_status="ENABLED",
        )
        draft = Proposal.create(
            proposal_id="proposal-xss-1",
            principal_id=principal.id,
            customer_id="1234567890",
            payload=payload,
            expires_at=now + timedelta(hours=1),
        )
        ProposalRepository(conn).save(submit_proposal(draft, now=now))

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            page = await client.get("/approvals")

        self.assertNotIn("<script>", page.text)
        self.assertIn("&lt;script&gt;", page.text)

    async def test_approvals_page_has_accessible_document_structure(self) -> None:
        """Faz 7.2: lang, skip link, a main landmark, and a per-proposal heading that
        gives the article an accessible name (docs/DESIGN.md WCAG 2.2 AA baseline)."""
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        ProposalRepository(conn).save(_make_pending_proposal(principal.id))

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            page = await client.get("/approvals")

        text = page.text
        self.assertIn('<html lang="tr">', text)
        self.assertIn('<main id="main">', text)
        self.assertIn('href="#main"', text)
        self.assertIn("aria-labelledby=", text)
        self.assertIn('aria-label="Hesap işlemleri"', text)
        self.assertIn("prefers-reduced-motion", text)
        self.assertIn("focus-visible", text)

    async def test_login_never_creates_principal_or_touches_credential(self) -> None:
        _, app = self._build_app(login_subject="sub-never-connected")
        conn = app.state.auth_context.conn

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            callback_response = await self._login(client)

            self.assertEqual(callback_response.status_code, 403)
            self.assertNotIn("web_session", callback_response.cookies)
            self.assertIsNone(
                PrincipalRepository(conn).get("https://accounts.google.com", "sub-never-connected")
            )

    async def test_login_does_not_rotate_existing_ads_credential(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        vault_ref = vault.store("real-google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)

            credential = OAuthCredentialRepository(conn).get_active(principal.id)
            assert credential is not None
            self.assertEqual(credential.vault_ref, vault_ref)
            self.assertEqual(vault.read(vault_ref), "real-google-refresh-token")

    async def test_approvals_without_session_redirects_to_login(self) -> None:
        _, app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await client.get("/approvals")
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["location"], "/login")

    async def test_approvals_without_csrf_cookie_redirects_to_login(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            client.cookies.delete("web_csrf", domain="connector.example.com", path="/")
            page = await client.get("/approvals")

        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["location"], "/login")

    async def test_decision_without_session_is_unauthorized(self) -> None:
        _, app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.post(
                "/approvals/proposal-1/decision",
                data={"decision": "approve", "csrf_token": "whatever"},
            )
        self.assertEqual(response.status_code, 401)

    async def test_decision_rejects_wrong_csrf_token(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        proposals = ProposalRepository(conn)
        proposals.save(_make_pending_proposal(principal.id))

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            response = await client.post(
                "/approvals/proposal-1/decision",
                data={"decision": "approve", "csrf_token": "wrong-token"},
            )
            self.assertEqual(response.status_code, 401)
            updated = proposals.get(principal.id, "proposal-1")
            assert updated is not None
            self.assertEqual(updated.status, ProposalStatus.PENDING_APPROVAL)

    async def test_decision_hides_invalid_proposal_identifier(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        session = issue_web_session(principal.id, now=datetime.now(UTC))
        WebSessionRepository(conn).create(
            principal.id, session.token, session.csrf_token, session.expires_at
        )

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            client.cookies.set("web_session", session.token, domain="connector.example.com")
            client.cookies.set("web_csrf", session.csrf_token, domain="connector.example.com")
            response = await client.post(
                f"/approvals/{'a' * 129}/decision",
                data={"decision": "approve", "csrf_token": session.csrf_token},
            )

        self.assertEqual(response.status_code, 404)

    async def test_invalid_decision_is_rejected_before_proposal_lookup(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        session = issue_web_session(principal.id, now=datetime.now(UTC))
        WebSessionRepository(conn).create(
            principal.id, session.token, session.csrf_token, session.expires_at
        )

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            client.cookies.set("web_session", session.token, domain="connector.example.com")
            with patch("backend.src.auth.approvals_routes.ProposalRepository.get") as lookup:
                response = await client.post(
                    "/approvals/proposal-1/decision",
                    data={"decision": "approve\nadmin", "csrf_token": session.csrf_token},
                )

        self.assertEqual(response.status_code, 400)
        lookup.assert_not_called()

    async def test_oversized_csrf_token_is_rejected(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        session = issue_web_session(principal.id, now=datetime.now(UTC))
        WebSessionRepository(conn).create(
            principal.id, session.token, session.csrf_token, session.expires_at
        )

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            client.cookies.set("web_session", session.token, domain="connector.example.com")
            response = await client.post(
                "/approvals/proposal-1/decision",
                data={"decision": "approve", "csrf_token": "a" * 129},
            )

        self.assertEqual(response.status_code, 401)

    async def test_cross_principal_cannot_decide_other_principals_proposal(self) -> None:
        """Attacker has a genuinely valid session (correct cookie + CSRF) but a different
        principal's proposal_id -- the 404 must come from ownership, not from CSRF/session."""
        _, app = self._build_app()
        conn = app.state.auth_context.conn
        owner = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-owner")
        attacker = PrincipalRepository(conn).get_or_create(
            "https://accounts.google.com", "sub-attacker"
        )
        proposals = ProposalRepository(conn)
        proposals.save(_make_pending_proposal(owner.id))

        attacker_session = issue_web_session(attacker.id, now=datetime.now(UTC))
        WebSessionRepository(conn).create(
            attacker.id,
            attacker_session.token,
            attacker_session.csrf_token,
            attacker_session.expires_at,
        )

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            client.cookies.set(
                "web_session", attacker_session.token, domain="connector.example.com"
            )
            client.cookies.set(
                "web_csrf", attacker_session.csrf_token, domain="connector.example.com"
            )
            page = await client.get("/approvals")
            self.assertNotIn("campaign_pause", page.text)  # attacker has no proposals of their own

            response = await client.post(
                "/approvals/proposal-1/decision",
                data={"decision": "approve", "csrf_token": attacker_session.csrf_token},
            )
            self.assertEqual(response.status_code, 404)
            # The proposal must still belong to, and only be decidable by, its owner.
            updated = proposals.get(owner.id, "proposal-1")
            assert updated is not None
            self.assertEqual(updated.status, ProposalStatus.PENDING_APPROVAL)

    async def test_disconnect_revokes_credential_and_logs_out(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        vault_ref = vault.store("real-google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            page = await client.get("/approvals")
            match = CSRF_RE.search(page.text)
            assert match is not None
            csrf_token = match.group(1)

            response = await client.post(
                "/disconnect",
                data={"csrf_token": csrf_token},
                headers={"X-Correlation-ID": "disconnect-corr-1"},
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], "/login")
            self.assertEqual(response.headers["x-correlation-id"], "disconnect-corr-1")

            # The now-revoked session cookie can no longer reach /approvals.
            after = await client.get("/approvals")
            self.assertEqual(after.status_code, 302)
            self.assertEqual(after.headers["location"], "/login")

            self.assertIsNone(OAuthCredentialRepository(conn).get_active(principal.id))
            account = AdsAccountRepository(conn).get_account(principal.id, "1234567890")
            assert account is not None
            self.assertEqual(account.status, "disconnected")
            events = AuditRepository(conn).list_for_principal(principal.id)
            self.assertEqual(events[0].correlation_id, "disconnect-corr-1")

    async def test_disconnect_confirmation_page_shows_impact_summary(self) -> None:
        """Faz 7.4: before the irreversible POST, the user sees how many accounts are
        linked, that the credential is permanently deleted, and an explicit warning."""
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        AdsAccountRepository(conn).link_account(principal.id, "1111111111", None)
        vault = app.state.auth_context.vault
        vault_ref = vault.store("real-google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            confirm_page = await client.get("/disconnect")

        self.assertEqual(confirm_page.status_code, 200)
        text = confirm_page.text
        self.assertIn("geri alınamaz", text)
        self.assertIn("2 bağlı Google Ads hesabının", text)
        self.assertIn("kalıcı olarak", text)
        self.assertIn('action="/disconnect"', text)

    async def test_disconnect_confirmation_page_requires_session(self) -> None:
        _, app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/disconnect")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login")

    async def test_disconnect_without_session_is_unauthorized(self) -> None:
        _, app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.post("/disconnect", data={"csrf_token": "whatever"})
        self.assertEqual(response.status_code, 401)

    async def test_disconnect_rejects_wrong_csrf_token(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        vault = app.state.auth_context.vault
        vault_ref = vault.store("real-google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            response = await client.post("/disconnect", data={"csrf_token": "wrong-token"})
            self.assertEqual(response.status_code, 401)
            # Nothing was revoked -- the credential must still be active.
            self.assertIsNotNone(OAuthCredentialRepository(conn).get_active(principal.id))

    async def test_logout_revokes_session_with_csrf(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            page = await client.get("/approvals")
            match = CSRF_RE.search(page.text)
            assert match is not None
            csrf_token = match.group(1)

            response = await client.post("/logout", data={"csrf_token": csrf_token})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], "/login")

            after = await client.get("/approvals")
            self.assertEqual(after.status_code, 302)
            self.assertEqual(after.headers["location"], "/login")

    async def test_logout_rejects_wrong_csrf_token(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            await self._login(client)
            response = await client.post("/logout", data={"csrf_token": "wrong-token"})
            self.assertEqual(response.status_code, 401)

            still_logged_in = await client.get("/approvals")
            self.assertEqual(still_logged_in.status_code, 200)

    async def test_session_cookies_are_hardened_by_default(self) -> None:
        """Non-local environment (the default here, see _settings): both cookies must be
        Secure + SameSite=Strict; only web_session (never touched by page script) is
        HttpOnly."""
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            callback_response = await self._login(client)

        set_cookie_headers = callback_response.headers.get_list("set-cookie")
        session_cookie = next(h for h in set_cookie_headers if h.startswith("web_session=")).lower()
        csrf_cookie = next(h for h in set_cookie_headers if h.startswith("web_csrf=")).lower()

        self.assertIn("httponly", session_cookie)
        self.assertIn("secure", session_cookie)
        self.assertIn("samesite=strict", session_cookie)
        self.assertIn("secure", csrf_cookie)
        self.assertIn("samesite=strict", csrf_cookie)
        self.assertNotIn("httponly", csrf_cookie)

    async def test_session_cookie_is_not_secure_in_local_environment(self) -> None:
        settings = Settings(
            sqlite_db_path=":memory:",
            environment="local",
            public_base_url=PUBLIC_BASE_URL,
            mcp_resource_path="/mcp",
            local_vault_key=Fernet.generate_key().decode(),
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_ads_developer_token="dev-token",
            allowed_hosts=("connector.example.com",),
            cors_allowed_origins=(),
        )
        app = create_app(
            settings,
            google_client=FakeGoogleOAuthClient(),
            login_google_client=FakeGoogleOAuthClient(google_subject="sub-1"),
        )
        conn = app.state.auth_context.conn
        PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            callback_response = await self._login(client)

        session_cookie = next(
            h
            for h in callback_response.headers.get_list("set-cookie")
            if h.startswith("web_session=")
        ).lower()
        self.assertNotIn("secure", session_cookie)

    async def test_malformed_session_cookie_is_rejected_without_crashing(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            client.cookies.set(
                "web_session", "not-a-real-session-token", domain="connector.example.com"
            )
            client.cookies.set("web_csrf", "not-a-real-csrf-token", domain="connector.example.com")
            page = await client.get("/approvals")

        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["location"], "/login")

    async def test_disconnect_from_one_browser_revokes_a_concurrent_session_elsewhere(
        self,
    ) -> None:
        """The same principal signed in from two browsers (e.g. phone + laptop) --
        disconnecting from one must end the other's session too, since disconnect is
        the "stop all future access" guarantee (docs/PRODUCT.md), not merely "sign this
        browser out"."""
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        vault = app.state.auth_context.vault
        vault_ref = vault.store("real-google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as browser_a,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as browser_b,
        ):
            await self._login(browser_a)
            still_signed_in_a = await browser_a.get("/approvals")
            self.assertEqual(still_signed_in_a.status_code, 200)

            await self._login(browser_b)
            page_b = await browser_b.get("/approvals")
            match = CSRF_RE.search(page_b.text)
            assert match is not None
            response = await browser_b.post("/disconnect", data={"csrf_token": match.group(1)})
            self.assertEqual(response.status_code, 302)

            # browser_a's session, issued and valid before browser_b's disconnect, must
            # now also be dead -- without re-authenticating, it cannot still reach /approvals.
            after = await browser_a.get("/approvals")
            self.assertEqual(after.status_code, 302)
            self.assertEqual(after.headers["location"], "/login")

    async def test_replayed_login_state_is_rejected(self) -> None:
        _, app = self._build_app(login_subject="sub-1")
        conn = app.state.auth_context.conn
        PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            login_response = await client.get("/login")
            state = _state_from_redirect(login_response)
            first = await client.get(
                "/google/callback", params={"state": state, "code": "fake-code"}
            )
            second = await client.get(
                "/google/callback", params={"state": state, "code": "fake-code"}
            )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)


if __name__ == "__main__":
    unittest.main()
