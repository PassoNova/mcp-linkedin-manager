"""
LinkedIn REST API v2 client + Voyager (internal web) client.

LinkedInClient  — official OAuth API (api.linkedin.com/v2)
VoyagerClient   — LinkedIn's internal web API (linkedin.com/voyager/api),
                  authenticated via browser session cookies (li_at + JSESSIONID).
                  Requires no partner-program access; use set_web_session tool
                  to register cookies extracted once from your browser DevTools.

LinkedIn API surface used here:
  GET  /v2/userinfo                          (OpenID Connect – profile + email)
  GET  /v2/me                                (full profile projection)
  POST /v2/ugcPosts                          (create a post)
  GET  /v2/ugcPosts?q=authors&...            (list own posts)
  DELETE /v2/ugcPosts/{urn}                  (delete a post)
  GET  voyager/api/me                        (full profile via web session)
  PATCH voyager/api/identity/profiles/{id}  (update headline via web session)
"""

from __future__ import annotations

import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import httpx

API_BASE = "https://api.linkedin.com/v2"
REST_BASE = "https://api.linkedin.com/rest"   # versioned REST API (requires LinkedIn-Version header)
OPENID_USERINFO = "https://api.linkedin.com/v2/userinfo"
VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

# LinkedIn requires these headers on all v2 REST calls.
# LinkedIn-Version pins the API version; without it some finders return NO_VERSION errors.
RESTLI_HEADER = {"X-Restli-Protocol-Version": "2.0.0", "LinkedIn-Version": "202506"}


class LinkedInClient:
    """Thin wrapper around LinkedIn REST API v2."""

    def __init__(self, access_token: str, timeout: float = 20.0) -> None:
        self._token = access_token
        self._timeout = timeout

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            **RESTLI_HEADER,
        }
        if extra:
            h.update(extra)
        return h

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(url, headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json() if r.content else {}

    def _post(self, path: str, body: dict) -> Any:
        url = f"{API_BASE}{path}"
        with httpx.Client(timeout=self._timeout) as c:
            r = c.post(url, headers=self._headers(), json=body)
            r.raise_for_status()
            return r.json() if r.content else {}

    def _patch(self, path: str, body: dict) -> Any:
        url = f"{API_BASE}{path}"
        with httpx.Client(timeout=self._timeout) as c:
            r = c.patch(url, headers=self._headers(), json=body)
            r.raise_for_status()
            return r.json() if r.content else {}

    def _delete(self, path: str) -> None:
        url = f"{API_BASE}{path}"
        with httpx.Client(timeout=self._timeout) as c:
            r = c.delete(url, headers=self._headers())
            r.raise_for_status()

    # ── Profile ────────────────────────────────────────────────────────────────

    def get_userinfo(self) -> dict:
        """
        OpenID Connect /userinfo endpoint.
        Returns: sub, name, given_name, family_name, picture, email,
                 locale (when available).
        This is the most reliable way to get basic profile info with the
        openid + profile + email scopes.
        """
        return self._get(OPENID_USERINFO)

    def get_profile(self) -> dict:
        """
        LinkedIn v2 /me with a broad projection.
        Falls back gracefully if restricted fields are absent.
        """
        projection = (
            "id,firstName,lastName,headline,vanityName,"
            "profilePicture(displayImage~:playableStreams)"
        )
        return self._get(f"/me?projection=({projection})")

    def get_person_urn(self) -> str:
        """Return the caller's person URN, e.g. urn:li:person:AbC123."""
        info = self.get_userinfo()
        # /userinfo returns 'sub' which is the raw person ID
        sub = info.get("sub", "")
        return f"urn:li:person:{sub}"

    # ── Posts ──────────────────────────────────────────────────────────────────

    def create_post(
        self,
        text: str,
        visibility: str = "PUBLIC",
        person_urn: Optional[str] = None,
    ) -> dict:
        """
        Publish a UGC text post on behalf of the authenticated member.

        Args:
            text:        Post body (max 3,000 characters).
            visibility:  "PUBLIC" or "CONNECTIONS".
            person_urn:  Resolved automatically if omitted.
        """
        if person_urn is None:
            person_urn = self.get_person_urn()

        body = {
            "author": person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": visibility.upper()
            },
        }
        return self._post("/ugcPosts", body)

    def get_posts(
        self,
        count: int = 10,
        person_urn: Optional[str] = None,
    ) -> list[dict]:
        """
        Return the authenticated member's recent posts via the versioned REST API.

        Uses GET /rest/posts?q=author (LinkedIn versioned API, replacing the
        deprecated GET /v2/ugcPosts?q=authors).

        Args:
            count:      How many to fetch (1–50).
            person_urn: Resolved automatically if omitted.
        """
        if person_urn is None:
            person_urn = self.get_person_urn()

        params = {
            "q": "author",
            "author": person_urn,
            "count": max(1, min(count, 50)),
            "sortBy": "LAST_MODIFIED",
        }
        url = f"{REST_BASE}/posts"
        data = self._get(url, params=params)
        return data.get("elements", [])

    def delete_post(self, post_urn: str) -> None:
        """
        Permanently delete a post (ugcPost or share URN).

        Args:
            post_urn: Full URN, e.g. urn:li:ugcPost:123 or urn:li:share:123
        """
        encoded = urllib.parse.quote(post_urn, safe="")
        if ":share:" in post_urn:
            with httpx.Client(timeout=self._timeout) as c:
                r = c.delete(
                    f"{REST_BASE}/posts/{encoded}",
                    headers=self._headers(),
                )
                r.raise_for_status()
        else:
            self._delete(f"/ugcPosts/{encoded}")

    # ── Profile mutations ──────────────────────────────────────────────────────

    def update_headline(self, headline: str, locale: str = "en_US") -> dict:
        """
        Update the member's profile headline via PATCH /v2/me.

        Note: This endpoint is available in the standard Consumer API only for
        apps that have been granted the 'rw_me' scope. Standard apps typically
        only have read access to /me. The call is made and the response is
        returned; if LinkedIn rejects it with 403, the error is surfaced to
        the tool caller.
        """
        country, language = locale.split("_", 1) if "_" in locale else ("US", locale)
        body = {
            "patch": {
                "$set": {
                    "headline": {
                        "localized": {locale: headline},
                        "preferredLocale": {
                            "country": country,
                            "language": language,
                        },
                    }
                }
            }
        }
        return self._post("/me", body)

    # ── Connections / community ────────────────────────────────────────────────

    def get_connections_count(self) -> dict:
        """
        Retrieve first-degree connection count.
        Requires r_network scope (partner-gated). Returns a descriptive
        message when the scope is unavailable.
        """
        try:
            data = self._get(
                "/connections",
                params={"q": "viewer", "start": 0, "count": 0},
            )
            return {
                "count": data.get("paging", {}).get("total", "unknown"),
                "note": "First-degree connections.",
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 401):
                return {
                    "count": "unavailable",
                    "note": (
                        "The r_network scope is restricted to LinkedIn partner apps. "
                        "Connection counts cannot be read via the standard Consumer API."
                    ),
                }
            raise


# ── Voyager (web session) client ───────────────────────────────────────────────

_VOYAGER_FETCH_SCRIPT = """
    async ({url, jsessionid, method, body}) => {
        const opts = {
            method,
            headers: {
                'accept': 'application/vnd.linkedin.normalized+json+2.1',
                'x-restli-protocol-version': '2.0.0',
                'x-li-lang': 'en_US',
                'csrf-token': jsessionid,
            }
        };
        if (body) {
            opts.body = JSON.stringify(body);
            opts.headers['content-type'] = 'application/json';
        }
        const resp = await fetch(url, opts);
        return { status: resp.status, body: await resp.text() };
    }
"""

_PAGE_TEXT_SCRIPT = """
    () => {
        const main = document.querySelector('main') || document.querySelector('[role="main"]');
        return (main || document.body).innerText;
    }
"""


class VoyagerClient:
    """
    LinkedIn Voyager API client backed by a Playwright persistent browser context.

    LinkedIn's Cloudflare protection ties session cookies to the browser
    fingerprint that created them.  Plain HTTP clients (httpx, curl) and fresh
    Playwright contexts are rejected via TLS fingerprinting even with valid
    cookies.  The only reliable path is to reuse the exact same persistent
    Chromium profile that was used during the authenticate() OAuth flow.

    Flow per request:
      1. Re-open the persistent context at *user_data_dir* (same fingerprint).
      2. Navigate to linkedin.com/feed/ (required so fetch() runs on that origin).
      3. Execute the Voyager fetch() call from within the browser's JS engine.
      4. Parse and return the JSON response.
    """

    def __init__(
        self,
        li_at: str,
        jsessionid: str,
        user_data_dir: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._li_at = li_at
        self._jsessionid = jsessionid.strip('"')
        self._user_data_dir = user_data_dir
        self._timeout_ms = int(timeout * 1000)

    def _request(self, path: str, method: str = "GET", body: Optional[dict] = None) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(self._browser_request, path, method, body).result()

    def _browser_request(self, path: str, method: str, body: Optional[dict]) -> Any:
        from playwright.sync_api import sync_playwright

        if not self._user_data_dir:
            raise RuntimeError(
                "No persistent browser profile found. "
                "Run the `authenticate` tool once to set up the browser session "
                "needed for Voyager API access."
            )

        url = f"{VOYAGER_BASE}{path}"
        jsessionid = self._jsessionid

        _BLOCKED = {"image", "media", "font", "stylesheet", "other"}

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                self._user_data_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.new_page()
                page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in _BLOCKED
                    else route.continue_(),
                )
                page.goto(
                    "https://www.linkedin.com/feed/",
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )
                live_cookies = context.cookies("https://www.linkedin.com")
                live_jsessionid = next(
                    (c["value"].strip('"') for c in live_cookies if c["name"] == "JSESSIONID"),
                    jsessionid,
                )
                result = page.evaluate(
                    _VOYAGER_FETCH_SCRIPT,
                    {"url": url, "jsessionid": live_jsessionid, "method": method, "body": body},
                )
            finally:
                context.close()

        if result["status"] >= 400:
            raise RuntimeError(
                f"Voyager API error {result['status']}: {result['body'][:300]}"
            )
        return json.loads(result["body"]) if result["body"] else {}

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        if params:
            path = f"{path}?{urllib.parse.urlencode(params)}"
        return self._request(path, "GET")

    def _patch(self, path: str, body: dict) -> Any:
        return self._request(path, "PATCH", body)

    def get_me(self) -> dict:
        """
        Fetch the authenticated member's profile from the Voyager API.

        Returns a normalized dict with keys: headline, first_name, last_name,
        public_id, entity_urn, picture_url.
        """
        raw = self._get("/me")

        # Voyager /me: miniProfile lives in included[0], data holds only a URN pointer.
        included = raw.get("included", [])
        mini = included[0] if included else {}

        if not mini:
            mini = raw.get("data", raw)
            if "miniProfile" in mini:
                mini = mini["miniProfile"]

        picture_root = mini.get("picture", {})
        artifacts = (
            picture_root
            .get("com.linkedin.common.VectorImage", {})
            .get("artifacts", [])
        )
        picture_url = ""
        if artifacts:
            root_url = (
                picture_root
                .get("com.linkedin.common.VectorImage", {})
                .get("rootUrl", "")
            )
            picture_url = root_url + artifacts[-1].get("fileIdentifyingUrlPathSegment", "")

        return {
            "headline": mini.get("occupation", ""),
            "first_name": mini.get("firstName", ""),
            "last_name": mini.get("lastName", ""),
            "public_id": mini.get("publicIdentifier", ""),
            "entity_urn": mini.get("entityUrn", ""),
            "picture_url": picture_url,
        }

    def update_headline(self, headline: str, public_id: str) -> dict:
        """Update the profile headline via Voyager."""
        return self._patch(
            f"/identity/profiles/{public_id}",
            {"patch": {"$set": {"headline": headline}}},
        )

    def get_notifications(self, count: int = 20) -> list[dict]:
        """
        Fetch recent notifications by scraping the LinkedIn notifications page DOM.

        LinkedIn renders notification cards server-side; the Voyager REST endpoint
        returns only badge counts, not the cards themselves.
        """
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(self._browser_scrape_notifications, count).result()

    def _browser_scrape_notifications(self, count: int) -> list[dict]:
        from playwright.sync_api import sync_playwright

        if not self._user_data_dir:
            raise RuntimeError(
                "No persistent browser profile found. Run authenticate first."
            )

        _BLOCKED = {"image", "media", "font", "stylesheet", "other"}
        _EXTRACT_SCRIPT = """
            () => {
                const cards = document.querySelectorAll(
                    '.nt-card-list__item, [data-urn], .notification-item, article'
                );
                if (cards.length) {
                    return Array.from(cards).map(c => c.innerText.trim()).filter(Boolean);
                }
                const main = document.querySelector('main') || document.body;
                return main.innerText.split('\\n').map(l => l.trim()).filter(Boolean);
            }
        """
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                self._user_data_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.new_page()
                page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in _BLOCKED
                    else route.continue_(),
                )
                page.goto(
                    "https://www.linkedin.com/notifications/",
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )
                page.evaluate("window.scrollTo(0, 600)")
                page.wait_for_timeout(2000)
                items = page.evaluate(_EXTRACT_SCRIPT)
            finally:
                context.close()

        return [{"text": t} for t in items[:count]]

    def get_conversations(self, entity_urn: str, count: int = 20) -> list[dict]:
        """
        Fetch recent messaging conversations via Voyager GraphQL.

        entity_urn: the URN from get_me() — either fs_miniProfile or fsd_profile form;
        the raw member ID is extracted and rebuilt as fsd_profile for the mailbox query.
        """
        raw_id = entity_urn.split(":")[-1]
        mailbox_urn = f"urn:li:fsd_profile:{raw_id}"
        encoded = urllib.parse.quote(mailbox_urn, safe="")
        path = (
            f"/voyagerMessagingGraphQL/graphql"
            f"?queryId=messengerConversations.0d5e6781bbee71c3e51c8843c6519f48"
            f"&variables=(mailboxUrn:{encoded})"
        )
        raw = self._get(path)
        included = raw.get("included", [])
        convos = [
            item for item in included
            if item.get("$type") == "com.linkedin.messenger.Conversation"
        ]
        return convos[:count]

    def get_recent_posts(self, public_id: str, count: int = 10) -> list[dict]:
        """Scrape recent posts from the member's LinkedIn activity page."""
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(self._browser_scrape_posts, public_id, count).result()

    def _browser_scrape_posts(self, public_id: str, count: int) -> list[dict]:
        from playwright.sync_api import sync_playwright

        if not self._user_data_dir:
            raise RuntimeError(
                "No persistent browser profile found. Run authenticate first."
            )

        _BLOCKED = {"image", "media", "font", "stylesheet", "other"}
        _EXTRACT_SCRIPT = """
            () => {
                const posts = [];
                // Each activity update card
                const cards = document.querySelectorAll(
                    '.feed-shared-update-v2, [data-urn], .occludable-update'
                );
                for (const card of cards) {
                    const urn = card.getAttribute('data-urn') || '';
                    // Skip non-post URNs (e.g. ads, shares by others)
                    if (urn && !urn.includes('activity') && !urn.includes('ugcPost') && !urn.includes('share')) continue;
                    const textEl = card.querySelector(
                        '.feed-shared-update-v2__description, .break-words, .feed-shared-text'
                    );
                    const text = textEl ? textEl.innerText.trim() : '';
                    const timeEl = card.querySelector('time, .feed-shared-actor__sub-description');
                    const time = timeEl ? timeEl.getAttribute('datetime') || timeEl.innerText.trim() : '';
                    if (text) posts.push({ urn, text, time });
                }
                // Fallback: if no structured cards found, grab all article text blocks
                if (!posts.length) {
                    const items = document.querySelectorAll('article, li[class*="occludable"]');
                    for (const item of items) {
                        const t = item.innerText.trim();
                        if (t.length > 30) posts.push({ urn: '', text: t.slice(0, 500), time: '' });
                    }
                }
                return posts;
            }
        """

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                self._user_data_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.new_page()
                page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in _BLOCKED
                    else route.continue_(),
                )
                page.goto(
                    f"https://www.linkedin.com/in/{public_id}/recent-activity/all/",
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )
                page.evaluate("window.scrollTo(0, 800)")
                page.wait_for_timeout(2500)
                items = page.evaluate(_EXTRACT_SCRIPT)
            finally:
                context.close()

        return items[:count]

    def get_profile_sections(self, public_id: str) -> dict:
        """
        Scrape profile sections (about, experience, education, skills) via browser.

        Navigates to each detail page within a single persistent browser context
        and extracts the main content text.
        """
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(self._browser_scrape_profile, public_id).result()

    def _browser_scrape_profile(self, public_id: str) -> dict:
        from playwright.sync_api import sync_playwright

        if not self._user_data_dir:
            raise RuntimeError(
                "No persistent browser profile found. "
                "Run the `authenticate` tool once to set up the browser session."
            )

        _BLOCKED = {"image", "media", "font", "stylesheet", "other"}
        pages = {
            "about": f"/in/{public_id}/",
            "experience": f"/in/{public_id}/details/experience/",
            "education": f"/in/{public_id}/details/education/",
            "skills": f"/in/{public_id}/details/skills/",
        }
        sections: dict = {}

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                self._user_data_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.new_page()
                page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in _BLOCKED
                    else route.continue_(),
                )
                for section, path in pages.items():
                    try:
                        page.goto(
                            f"https://www.linkedin.com{path}",
                            wait_until="domcontentloaded",
                            timeout=self._timeout_ms,
                        )
                        page.evaluate("window.scrollTo(0, 600)")
                        page.wait_for_timeout(1500)
                        content = page.evaluate(_PAGE_TEXT_SCRIPT)
                        sections[section] = content[:4000]
                    except Exception as exc:
                        sections[section] = f"Error: {exc}"
            finally:
                context.close()

        return sections
