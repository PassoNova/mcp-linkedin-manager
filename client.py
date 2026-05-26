"""
LinkedIn REST API v2 client.

All public methods return plain Python dicts/lists. They raise
httpx.HTTPStatusError on 4xx/5xx responses (the caller decides how to
surface that to the MCP tool user).

LinkedIn API surface used here:
  GET  /v2/userinfo                          (OpenID Connect – profile + email)
  GET  /v2/me                                (full profile projection)
  POST /v2/ugcPosts                          (create a post)
  GET  /v2/ugcPosts?q=authors&...            (list own posts)
  DELETE /v2/ugcPosts/{urn}                  (delete a post)

Fields that require LinkedIn Partner-Program access (experience, education,
certifications, skills via the Profile API) are NOT available through the
standard Consumer API. Those tools will return clear explanations rather than
failing silently.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Optional

import httpx

API_BASE = "https://api.linkedin.com/v2"
OPENID_USERINFO = "https://api.linkedin.com/v2/userinfo"

# LinkedIn requires this header on all v2 REST calls.
RESTLI_HEADER = {"X-Restli-Protocol-Version": "2.0.0"}


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
        Return the authenticated member's recent UGC posts.

        Args:
            count:      How many to fetch (1–50).
            person_urn: Resolved automatically if omitted.
        """
        if person_urn is None:
            person_urn = self.get_person_urn()

        encoded = urllib.parse.quote(person_urn, safe="")
        params = {
            "q": "authors",
            "authors": f"List({encoded})",
            "count": max(1, min(count, 50)),
        }
        data = self._get("/ugcPosts", params=params)
        return data.get("elements", [])

    def delete_post(self, post_urn: str) -> None:
        """
        Permanently delete a UGC post.

        Args:
            post_urn: Full URN, e.g. urn:li:ugcPost:1234567890
        """
        encoded = urllib.parse.quote(post_urn, safe="")
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
