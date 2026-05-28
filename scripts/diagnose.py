#!/usr/bin/env python3
"""
LinkedIn MCP — Diagnostics

Usage: python scripts/diagnose.py

Checks every component the MCP server depends on and reports status.
Run this when authentication fails or a tool returns unexpected errors.

Exit codes:
  0 — all checks passed
  1 — one or more checks failed (see output)
"""

from __future__ import annotations

import json
import os
import sys
import time

# Allow running from repo root or scripts/
_MCP_DIR = os.path.join(os.path.dirname(__file__), "..", "mcp")
sys.path.insert(0, os.path.abspath(_MCP_DIR))

PASS = "✅"
WARN = "⚠️ "
FAIL = "❌"


def _section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print('─' * 50)


def check_python() -> bool:
    _section("Python environment")
    v = sys.version_info
    ok = v >= (3, 10)
    status = PASS if ok else FAIL
    print(f"  {status} Python {v.major}.{v.minor}.{v.micro} {'(>=3.10 required)' if not ok else ''}")
    return ok


def check_dependencies() -> bool:
    _section("Required packages")
    packages = {
        "httpx": "HTTP client for LinkedIn REST API",
        "mcp": "Model Context Protocol server framework",
        "dotenv": "Environment variable loader (python-dotenv)",
        "keyring": "OS keychain integration",
    }
    optional = {
        "playwright": "Voyager web automation (optional but strongly recommended)",
        "browser_cookie3": "Chrome cookie capture after OAuth (optional)",
    }
    all_ok = True
    for pkg, desc in packages.items():
        try:
            __import__(pkg)
            print(f"  {PASS} {pkg:<20} {desc}")
        except ImportError:
            print(f"  {FAIL} {pkg:<20} MISSING — {desc}")
            all_ok = False
    for pkg, desc in optional.items():
        try:
            __import__(pkg)
            print(f"  {PASS} {pkg:<20} {desc}")
        except ImportError:
            print(f"  {WARN} {pkg:<20} not installed — {desc}")
    return all_ok


def check_chrome() -> bool:
    _section("System Chrome (needed for automatic Voyager session capture)")
    paths = [
        "/Applications/Google Chrome.app",
        os.path.expanduser("~/Applications/Google Chrome.app"),
    ]
    found = next((p for p in paths if os.path.exists(p)), None)
    if found:
        print(f"  {PASS} Chrome found at {found}")
        return True
    print(f"  {WARN} Chrome not found at {paths}")
    print("        Voyager session must be set manually via `set_web_session`.")
    return True  # Warning only — OAuth still works


def check_playwright() -> bool:
    _section("Playwright (needed for Voyager API scraping)")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"  {WARN} Playwright not installed. Voyager tools will be unavailable.")
        print("        Install with: pip install playwright && playwright install chromium")
        return True  # Warning only

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        print(f"  {PASS} Playwright Chromium launches successfully")
        return True
    except Exception as exc:
        print(f"  {FAIL} Playwright Chromium launch failed: {exc}")
        print("        Try: playwright install chromium")
        return False


def check_credentials() -> bool:
    _section("LinkedIn app credentials")
    try:
        from auth import load_credentials, _HAS_KEYRING
    except Exception as exc:
        print(f"  {FAIL} Could not import auth module: {exc}")
        return False

    print(f"  {'✓' if _HAS_KEYRING else '✗'} OS keychain available: {_HAS_KEYRING}")

    creds = load_credentials()
    if creds:
        print(f"  {PASS} App credentials found in OS keychain (client_id={creds['client_id'][:8]}…)")
        return True

    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        print(f"  {PASS} App credentials found in environment (LINKEDIN_CLIENT_ID={client_id[:8]}…)")
        return True

    # Check both mcp/.env and repo root .env
    dotenv_path = next(
        (p for p in [
            os.path.join(_MCP_DIR, ".env"),
            os.path.join(os.path.dirname(__file__), "..", ".env"),
        ] if os.path.exists(p)),
        None,
    )
    if dotenv_path:
        from dotenv import dotenv_values
        env = dotenv_values(dotenv_path)
        if env.get("LINKEDIN_CLIENT_ID") and env.get("LINKEDIN_CLIENT_SECRET"):
            print(f"  {PASS} App credentials found in .env file")
            return True

    print(f"  {FAIL} No app credentials found.")
    print("        Run: python -m linkedin_mcp setup")
    print("        Or set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in your environment.")
    return False


def check_auth_state() -> bool:
    _section("Authentication state")
    try:
        from auth import load_user_registry, load_token, load_web_session, is_token_expired, has_browser_profile, _browser_dir
    except Exception as exc:
        print(f"  {FAIL} Could not import auth module: {exc}")
        return False

    reg = load_user_registry()
    active = reg.get("active")
    aliases = reg.get("aliases", [])

    if not aliases:
        print(f"  {WARN} No users registered. Run `authenticate` with an alias.")
        return True

    print(f"  Active alias: {active or '(none)'}")
    print(f"  Registered:  {', '.join(aliases)}")
    print()

    all_ok = True
    for alias in aliases:
        marker = "▶ " if alias == active else "  "
        token = load_token(alias)
        if not token:
            print(f"  {marker}{FAIL} [{alias}] No token — run authenticate('{alias}')")
            all_ok = False
            continue

        expired = is_token_expired(token)
        obtained_at = token.get("_obtained_at", 0)
        expires_in = token.get("expires_in", 0)
        expiry = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(obtained_at + expires_in)) if (obtained_at and expires_in) else "unknown"
        scopes = token.get("scope", "unknown")

        if expired:
            print(f"  {marker}{FAIL} [{alias}] Token EXPIRED (expiry={expiry}) — run authenticate('{alias}')")
            all_ok = False
        else:
            print(f"  {marker}{PASS} [{alias}] Token valid (expiry={expiry})")

        print(f"        Scopes: {scopes}")

        session = load_web_session(alias)
        bdir = _browser_dir(alias)
        has_profile = has_browser_profile(bdir)
        if session:
            print(f"        {PASS} Web session present (Voyager tier)")
            print(f"        Browser profile: {'exists' if has_profile else 'missing — re-authenticate for best Voyager performance'}")
        else:
            print(f"        {WARN} No web session — OAUTH tier only")
            print("               Run authenticate() again or use set_web_session to enable Voyager")

    return all_ok


def check_log_file() -> bool:
    _section("Log file")
    log_path = os.path.expanduser(os.environ.get("LINKEDIN_MCP_LOG", "~/.linkedin_mcp.log"))
    if os.path.exists(log_path):
        size = os.path.getsize(log_path)
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(log_path)))
        print(f"  {PASS} Log file: {log_path}")
        print(f"        Size: {size:,} bytes | Last modified: {mtime}")
        print(f"        Tail: tail -f {log_path}")
    else:
        print(f"  ℹ️  Log file not yet created (will appear after first server start)")
        print(f"       Expected path: {log_path}")
    return True


def main() -> int:
    print("=" * 50)
    print("  LinkedIn MCP — Diagnostics")
    print("=" * 50)

    results = [
        check_python(),
        check_dependencies(),
        check_chrome(),
        check_playwright(),
        check_credentials(),
        check_auth_state(),
        check_log_file(),
    ]

    all_ok = all(results)
    print(f"\n{'=' * 50}")
    if all_ok:
        print(f"  {PASS} All checks passed")
    else:
        print(f"  {FAIL} Some checks failed — see output above")
    print("=" * 50)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
