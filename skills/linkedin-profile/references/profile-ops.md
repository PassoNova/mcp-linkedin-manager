# LinkedIn MCP — Profile Operations

All profile operations run on the **active account** (set via `switch_user`).
Use `list_users` to see which alias is currently active.

## Viewing profile

Call `get_profile` and present the result clearly:
- **Name** and **Headline** prominently
- **Email** and **Profile URL** as secondary info
- Mention the profile picture URL is available if they want it
- Note the **Person URN** is used internally by other tools

After showing the profile, proactively offer:
- "Would you like to update your headline?"
- "Want me to show your recent posts?"

If a Voyager session is active, also offer `get_full_profile` for experience,
education, certifications, skills, and contact info.

## Updating headline

When the user wants to update their headline:
1. Confirm the new headline text with the user before applying it
2. Check it is ≤ 220 characters — warn if longer
3. Call `update_headline` with the confirmed text
4. Call `get_profile` again to show the updated result

If `update_headline` returns a 403 error, explain:
> Updating the headline requires the `rw_me` OAuth scope, which LinkedIn
> restricts to partner-program apps. This field can be edited directly at
> linkedin.com/in/me → Edit profile → Headline. Alternatively, if you ran
> `authenticate` and a browser session was captured, Voyager can update it
> without partner-program approval — try `set_web_session` or re-authenticating.

## Full profile (Voyager tier)

If `check_auth` shows `tier: VOYAGER`, call `get_full_profile` to retrieve:
- About / summary section
- Experience entries
- Education entries
- Certifications
- Skills

Present each section in a readable format. If the user wants to edit these
sections, they must do so directly at linkedin.com (the standard API does not
permit writing to these fields). Claude in Chrome can help if the extension is
installed.

## What the API cannot do (standard OAuth tier)

When the user asks about editing experience, education, certifications, skills,
or the About/summary section via the API, call `get_api_capabilities` and explain:

> LinkedIn's standard Consumer API does not support writing to profile sections
> (experience, education, certifications, about). This requires the LinkedIn
> Partner Program. You can edit these fields directly at linkedin.com, or I can
> help via "Claude in Chrome" if the browser extension is installed.

Never attempt to write these fields via the API — they will always fail with 403.
