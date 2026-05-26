---
name: linkedin-profile
description: >
  View or update your LinkedIn profile information. Trigger with "get my
  LinkedIn profile", "show my LinkedIn", "update my headline", "what's my
  LinkedIn bio", "my LinkedIn info", or "what does my LinkedIn profile say".
---

## Viewing profile

Call `get_profile` and present the result clearly:
- **Name** and **Headline** prominently
- **Email** and **Profile URL** as secondary info
- Mention the profile picture URL is available if they want it
- Note the **Person URN** is used internally by other tools

After showing the profile, proactively offer:
- "Would you like to update your headline?"
- "Want me to show your recent posts?"

## Updating headline

When the user wants to update their headline:
1. Confirm the new headline text with the user before applying it
2. Check it is ≤ 220 characters — warn if longer
3. Call `update_headline` with the confirmed text
4. Call `get_profile` again to show the updated result

If `update_headline` returns a 403 error, explain:
> Updating the headline requires the `rw_me` OAuth scope, which LinkedIn
> restricts to partner-program apps. This field can be edited directly at
> linkedin.com/in/me → Edit profile → Headline.

## What the API cannot do

When the user asks about editing experience, education, certifications,
skills, or the About/summary section, call `get_api_capabilities` and explain
the LinkedIn API restriction clearly. Offer two alternatives:
1. Edit directly at linkedin.com
2. Use "Claude in Chrome" — Claude can interact with LinkedIn's web interface
   through the browser extension if installed

Never attempt to edit these fields via the API — they will always fail with 403.
