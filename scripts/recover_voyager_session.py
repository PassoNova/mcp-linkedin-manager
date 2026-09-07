"""
Recover the Voyager web session from the existing Playwright browser profile.

Usage:
    python scripts/recover_voyager_session.py [alias]

The browser profile at ~/.linkedin_mcp_browser_<alias> already contains a valid
LinkedIn session from a previous `authenticate` run (the login happened inside
the Playwright window). This opens that profile headlessly, reads the cookies,
and saves them as the alias's web session. Equivalent to the
`refresh_web_session` MCP tool, for use when the server is not running.

Make sure no MCP server is currently using the profile (Chromium locks it).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

from auth import (  # noqa: E402
    _browser_dir,
    get_active_alias,
    harvest_session_from_profile,
    has_browser_profile,
    save_web_session,
)


def recover(alias: str) -> None:
    bdir = _browser_dir(alias)
    if not has_browser_profile(bdir):
        print(f"❌ No browser profile found at {bdir}")
        print("   Run `authenticate` first to create the profile.")
        sys.exit(1)

    print(f"Reading LinkedIn session from browser profile at {bdir} …")
    li_at, jsessionid, err = harvest_session_from_profile(bdir)
    if not li_at:
        print(f"❌ {err}")
        print("   Run `authenticate` again and log in inside the window that opens.")
        sys.exit(1)

    save_web_session(li_at, jsessionid or "", alias)
    print(f"✅ Web session saved for alias '{alias}'.")
    print(f"   JSESSIONID: {'present' if jsessionid else '(not set — refreshed on first Voyager request)'}")
    print("   Voyager tools (get_recent_activity, get_full_profile, ...) are now enabled.")


if __name__ == "__main__":
    recover(sys.argv[1] if len(sys.argv) > 1 else (get_active_alias() or "default"))
