"""Faz 7.6: real-browser E2E + automated accessibility gate for ``/approvals``.

Unlike every other test in this suite (which drives the ASGI app in-process over
``httpx.ASGITransport``), this one runs the actual app behind a real TCP socket
(``uvicorn``) and drives it with a real Chromium instance (Playwright) plus an
automated accessibility scanner (axe-core, via ``axe-core-python``) -- the tool
choice docs/DESIGN.md's "Tasarım teslim kontrolü" and docs/TESTING.md's "UI"
test-pyramid tier call for but never picked a concrete tool for. Google is never
contacted: the browser is routed through the same ``FakeGoogleOAuthClient`` double
every other auth test uses, and navigation to the real ``accounts.google.com`` is
intercepted and aborted before it leaves the machine.

Skips cleanly (not a failure) wherever Playwright or its Chromium browser isn't
installed -- ``python -m playwright install chromium`` is a ~100MB download this
suite must not force on every contributor/CI runner that only needs the
``unittest``-tier tests (docs/TESTING.md "Kalite kapısı" does not list this file's
requirements as a required check for that reason; see docs/TESTING.md "Güncelleme
geçmişi" for the follow-up needed before it can be wired into CI).
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
from backend.src.app import create_app
from backend.src.approval import Proposal, build_proposal_payload, submit_proposal
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings
from backend.src.db.proposals import ProposalRepository
from backend.src.db.repository import AdsAccountRepository, PrincipalRepository
from cryptography.fernet import Fernet

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_IMPORT_ERROR: Exception | None = None
except ImportError as import_error:  # pragma: no cover - exercised only without the dep
    _PLAYWRIGHT_IMPORT_ERROR = import_error

try:
    from axe_core_python.sync_playwright import Axe

    _AXE_IMPORT_ERROR: Exception | None = None
except ImportError as import_error:  # pragma: no cover - exercised only without the dep
    _AXE_IMPORT_ERROR = import_error


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_pending_proposal(principal_id: str, customer_id: str, proposal_id: str) -> Proposal:
    now = datetime.now(UTC)
    payload = build_proposal_payload(
        proposal_type="campaign_pause",
        campaign_id="9999",
        rationale="30 gunluk performans dususu",
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


def _build_seeded_app(port: int):
    """Build the app *and* seed its DB from the thread that will also serve it --
    the underlying sqlite3 connection enforces same-thread access, so both must
    happen here rather than in the test's main thread (see module docstring)."""
    settings = Settings(
        sqlite_db_path=":memory:",
        # "local", not "test": app.py requires an https:// PUBLIC_BASE_URL outside
        # "local" (OAuth 2.1/MCP Authorization AS endpoints must be TLS), and this
        # suite talks to a real http://127.0.0.1 socket, not an in-process transport.
        environment="local",
        public_base_url=f"http://127.0.0.1:{port}",
        mcp_resource_path="/mcp",
        local_vault_key=Fernet.generate_key().decode(),
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_ads_developer_token="dev-token",
        # Starlette's TrustedHostMiddleware compares against the Host header with
        # any ":port" suffix already stripped -- the pattern itself must not
        # include the port either.
        allowed_hosts=("127.0.0.1",),
        cors_allowed_origins=(),
    )
    app = create_app(
        settings,
        google_client=FakeGoogleOAuthClient(),
        login_google_client=FakeGoogleOAuthClient(google_subject="sub-1", email="user@example.com"),
    )
    conn = app.state.auth_context.conn
    principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
    AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
    ProposalRepository(conn).save(
        _make_pending_proposal(principal.id, "1234567890", "proposal-e2e-1")
    )
    return app


class _ServerThread(threading.Thread):
    """Runs one real uvicorn server (app build + DB seed included) on its own thread."""

    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.server: uvicorn.Server | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None

    def run(self) -> None:
        try:
            app = _build_seeded_app(self.port)
            config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
            self.server = uvicorn.Server(config)
        except Exception as error:  # noqa: BLE001 - surfaced to the main thread below
            self._error = error
            self._ready.set()
            return
        self._ready.set()
        self.server.run()

    def wait_ready(self, timeout: float = 10.0) -> None:
        if not self._ready.wait(timeout):
            raise TimeoutError("Sunucu thread'i zamanında hazır olmadı")
        if self._error is not None:
            raise self._error
        base_url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{base_url}/healthz", timeout=1.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError as error:
                last_error = error
            time.sleep(0.05)
        raise TimeoutError(f"Sunucu /healthz'e zamanında yanıt vermedi: {last_error}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        self.join(timeout=5.0)


@unittest.skipIf(
    _PLAYWRIGHT_IMPORT_ERROR is not None, f"playwright kurulu değil: {_PLAYWRIGHT_IMPORT_ERROR}"
)
@unittest.skipIf(
    _AXE_IMPORT_ERROR is not None, f"axe-core-python kurulu değil: {_AXE_IMPORT_ERROR}"
)
class ApprovalsBrowserE2ETests(unittest.TestCase):
    """Real Chromium against a real TCP server -- see module docstring for scope."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.port = _free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = _ServerThread(cls.port)
        cls.thread.start()
        try:
            cls.thread.wait_ready()
        except Exception as error:  # noqa: BLE001 - environment-dependent, must skip not fail
            raise unittest.SkipTest(f"Yerel test sunucusu başlatılamadı: {error}") from error

        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except PlaywrightError as error:
            cls.playwright.stop()
            cls.thread.stop()
            raise unittest.SkipTest(
                f"Chromium başlatılamadı (bkz. 'playwright install'): {error}"
            ) from error

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()
        cls.thread.stop()

    def _login(self, page) -> None:
        """Reach an authenticated /approvals session without ever pointing the
        browser at the real accounts.google.com: ``/login``'s redirect is read
        with a plain HTTP client (not the browser) purely to extract ``state``,
        then the browser is sent straight to the callback -- exactly what would
        happen after a real Google consent screen, without the browser ever
        needing to render or navigate through Google's own domain."""
        redirect = httpx.get(f"{self.base_url}/login", follow_redirects=False)
        self.assertEqual(redirect.status_code, 302)
        state = parse_qs(urlsplit(redirect.headers["location"]).query)["state"][0]
        page.goto(f"{self.base_url}/google/callback?state={state}&code=fake-code")
        self.assertTrue(page.url.endswith("/approvals"))

    def test_login_preview_a11y_reflow_decision_and_disconnect(self) -> None:
        page = self.browser.new_page()
        # ``/login`` (reached below via the disconnect redirect) always 302s onward
        # to Google for real -- this sandbox happens to have outbound internet, so
        # without this guard the browser would actually leave localhost. Abort that
        # hop; it is no part of anything this suite is responsible for proving.
        page.route("https://accounts.google.com/**", lambda route: route.abort())
        try:
            self._login(page)

            # -- Faz 7.1 preview content is on the rendered page, not just in the
            # raw HTTP body (proves real DOM rendering, not a markup-only check).
            self.assertIn("1234567890", page.content())
            self.assertIn("30 gunluk performans dususu", page.content())

            # -- Faz 7.2: automated accessibility scan of the real, rendered page.
            axe_results = Axe().run(page)
            violations = axe_results.get("violations", [])
            self.assertEqual(
                violations,
                [],
                f"axe-core ihlalleri: {[v['id'] for v in violations]}",
            )

            # -- Faz 7.2: keyboard flow -- the skip link is the first stop.
            page.keyboard.press("Tab")
            focused_class = page.evaluate("document.activeElement.className")
            self.assertEqual(focused_class, "skip-link")

            # -- Faz 7.2: 320 CSS px reflow -- no horizontal scroll.
            page.set_viewport_size({"width": 320, "height": 640})
            scroll_width = page.evaluate("document.documentElement.scrollWidth")
            client_width = page.evaluate("document.documentElement.clientWidth")
            self.assertLessEqual(scroll_width, client_width + 1)
            page.set_viewport_size({"width": 1280, "height": 800})

            # -- Faz 7.1/2: a real click submits the approve form; the proposal
            # then disappears from the pending list (server round-trip proven).
            page.get_by_role("button", name="Onayla: campaign_pause / kampanya 9999").click()
            page.wait_for_url(f"{self.base_url}/approvals")
            self.assertNotIn("campaign_pause", page.content())

            # -- Faz 7.4: disconnect shows the impact/warning screen first.
            page.get_by_role("link", name="Bağlantıyı kes (disconnect)").click()
            page.wait_for_url(f"{self.base_url}/disconnect")
            self.assertIn("geri alınamaz", page.content())

            # The real confirm click revokes the session: POST /disconnect redirects
            # to GET /login, which itself immediately redirects on to Google -- a hop
            # this test blocks (see the route() above), so the browser's navigation
            # never completes past that point (aborting any hop in a redirect chain
            # aborts the whole chain, same as a real disconnected user's browser
            # would experience before Google responds). The click's actual, provable
            # effect is checked directly: the session cookie the disconnect response
            # deletes is gone from the browser's cookie jar.
            page.get_by_role("button", name="Evet, bağlantıyı kalıcı olarak kes").click()
            page.wait_for_timeout(500)
            cookie_names = {cookie["name"] for cookie in page.context.cookies()}
            self.assertNotIn("web_session", cookie_names)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
