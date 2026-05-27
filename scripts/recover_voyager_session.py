"""
Recover the Voyager web session from the existing Playwright browser profile.

Usage:
    python scripts/recover_voyager_session.py [alias]

The browser profile at ~/.linkedin_mcp_browser_<alias> already contains a valid
LinkedIn session from a previous authenticate run.  This script opens that profile
headlessly, navigates to LinkedIn feed so the server issues a fresh JSESSIONID,
reads the cookies, and saves them as the active web session.
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

from auth import (
    _browser_dir,
    has_browser_profile,
    save_web_session,
    get_active_alias,
)


async def recover(alias: str) -> None:
    from playwright.async_api import async_playwright

    bdir = _browser_dir(alias)
    if not has_browser_profile(bdir):
        print(f"❌ No browser profile found at {bdir}")
        print("   Run `authenticate` first to create the profile.")
        sys.exit(1)

    print(f"Opening browser profile at {bdir} …")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            bdir,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await context.new_page()

        print("Navigating to LinkedIn feed to refresh JSESSIONID …")
        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
        except Exception as exc:
            print(f"⚠️  Feed navigation error (non-fatal): {exc}")

        cookies = await context.cookies("https://www.linkedin.com")
        li_at = next((c["value"] for c in cookies if c["name"] == "li_at"), None)
        jsessionid = next((c["value"] for c in cookies if c["name"] == "JSESSIONID"), None)

        await context.close()

    if not li_at:
        print("❌ li_at cookie not found in the browser profile.")
        print("   The session may have expired. Run `authenticate` again.")
        sys.exit(1)

    print(f"✅ li_at found (first 30 chars): {li_at[:30]}…")
    print(f"   JSESSIONID: {jsessionid[:30] + '…' if jsessionid else '(not set — will be refreshed on first Voyager request)'}")

    save_web_session(li_at, jsessionid or "", alias)
    print(f"\n✅ Web session saved for alias '{alias}'.")
    print("   Voyager API (get_posts, get_profile, etc.) is now enabled.")


if __name__ == "__main__":
    alias = sys.argv[1] if len(sys.argv) > 1 else (get_active_alias() or "default")
    asyncio.run(recover(alias))
