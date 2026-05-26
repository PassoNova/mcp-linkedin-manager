#!/usr/bin/env bash
# Run this once from iTerm: bash push-pr.sh
set -euo pipefail

cd "$(dirname "$0")"

git checkout -b feat/voyager-client 2>/dev/null || git checkout feat/voyager-client

git add .

git commit -m "feat: add VoyagerClient for full profile access via browser session

LinkedIn's public API restricts experience, education, certifications,
skills, notifications, and messaging to partner-program apps. This adds
a second client using LinkedIn's internal Voyager API via browser session
cookies (li_at + jsessionid), captured automatically during the OAuth flow.

New tools:
- set_web_session   — manually provide li_at + jsessionid cookies
- clear_web_session — remove saved web session
- get_full_profile  — read full profile (all sections) via Voyager
- get_notifications — fetch LinkedIn notifications
- get_conversations — fetch messaging conversations

Changes:
- VoyagerClient in client.py with browser-request fallback for scraping
- run_oauth_flow now returns (token_data, li_at, jsessionid) tuple
- get_profile prefers Voyager when session available, falls back to OAuth
- update_headline uses Voyager (no partner scope required)
- Dual-client architecture: Voyager preferred, OAuth API as fallback"

git push origin feat/voyager-client

echo ""
echo "✅ Branch pushed. Open your PR at:"
echo "   https://github.com/PassoNova/mcp-linkedin-manager/compare/feat/voyager-client"
