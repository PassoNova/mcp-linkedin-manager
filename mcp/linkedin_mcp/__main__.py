"""
CLI entry point for linkedin-mcp.

Usage:
    python -m linkedin_mcp setup   — save app credentials to the OS keychain
"""
from __future__ import annotations

import getpass
import sys
import os

# mcp/ directory (parent of this linkedin_mcp/ package) must be on sys.path
# so that auth.py, server.py, etc. are importable without restructuring.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def setup() -> None:
    from auth import save_credentials, _HAS_KEYRING

    if not _HAS_KEYRING:
        print(
            "⚠️  The 'keyring' package could not find a suitable backend on this system.\n"
            "   Install one (e.g. 'pip install keyrings.alt') or set credentials via\n"
            "   LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET environment variables."
        )
        sys.exit(1)

    print("LinkedIn MCP — credential setup")
    print("Get your credentials at: https://www.linkedin.com/developers/apps\n")

    client_id = input("Client ID     : ").strip()
    client_secret = getpass.getpass("Client Secret : ").strip()

    if not client_id or not client_secret:
        print("Error: both Client ID and Client Secret are required.")
        sys.exit(1)

    saved = save_credentials(client_id, client_secret)
    if saved:
        print(
            "\n✅ Credentials saved to the OS keychain.\n"
            "   You can now delete your .env file — it is no longer needed.\n"
            "   Run 'authenticate' in Claude to complete setup."
        )
    else:
        print(
            "\n❌ Failed to save credentials to the OS keychain.\n"
            "   Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET as environment variables instead."
        )
        sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(0)

    command = sys.argv[1]
    if command == "setup":
        setup()
    else:
        print(f"Unknown command: {command!r}")
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
