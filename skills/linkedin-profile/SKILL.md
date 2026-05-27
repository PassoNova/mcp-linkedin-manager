---
name: linkedin-profile
description: >
  View or update your LinkedIn profile information. Trigger with "get my
  LinkedIn profile", "show my LinkedIn", "update my headline", "what's my
  LinkedIn bio", "my LinkedIn info", or "what does my LinkedIn profile say".
---

All profile operations run on the **active account**. Use `switch_user(alias)`
to change accounts or `list_users` to see which account is currently active.

## Viewing profile

Call `get_profile` and present: **Name** and **Headline** prominently, Email and
Profile URL as secondary. After showing, proactively offer to update the headline
or show recent posts. If Voyager is active, offer `get_full_profile` for experience,
education, certifications, skills, and about.

## Updating headline

1. Confirm the new text (≤ 220 characters) before applying
2. Call `update_headline`
3. Call `get_profile` again to show the result

If 403: explain that `rw_me` scope requires LinkedIn Partner Program. Suggest
editing at linkedin.com or re-authenticating to try via Voyager.

## Full profile (Voyager tier)

If `check_auth` shows `tier: VOYAGER`, call `get_full_profile` to retrieve
About, Experience, Education, Certifications, and Skills. Present each section
clearly. For editing these sections, direct the user to linkedin.com or offer
Claude in Chrome if the extension is installed.

## API limitations

When asked about editing experience, education, certifications, or About via
API: call `get_api_capabilities`, explain the restriction, and offer two paths:
1. Edit directly at linkedin.com
2. Use Claude in Chrome browser extension

Never attempt to write these fields via the API — they always return 403.
