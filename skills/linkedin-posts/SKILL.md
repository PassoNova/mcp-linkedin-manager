---
name: linkedin-posts
description: >
  Create, view, or delete LinkedIn posts. Trigger with "post to LinkedIn",
  "create a LinkedIn post", "write a LinkedIn post", "show my LinkedIn posts",
  "list my posts", "delete a post", "remove a LinkedIn post", or "draft
  something for LinkedIn".
---

All post operations run on the **active account**. Use `switch_user(alias)`
to change accounts or `list_users` to see which account is currently active.

## Creating a post

1. **Draft first** — if the user has rough ideas, help craft a polished post:
   - Strong opening (no "I'm excited to share…")
   - Short paragraphs (1–3 sentences)
   - Question or CTA at the end
   - Under 1,300 characters for best reach (3,000 char limit)

2. **Confirm before posting** — show final text, character count, and visibility;
   ask explicitly before calling `create_post`

3. **Visibility** — default PUBLIC; ask about CONNECTIONS-only if content seems personal

4. **After posting** — show the returned URN; tell the user to save it for later deletion

## Viewing posts

Call `get_posts` (default 10, max 50). Present: date, visibility, ~150 char preview,
URN collapsed. If empty, note that older posts may not appear via the API.

## Deleting a post

1. If no URN: call `get_posts` first, show the list
2. Show the post text preview and warn: **"This cannot be undone."**
3. Call `delete_post` only after explicit confirmation

## Drafting without posting

If the user says "draft" or "help me write", produce the text for review without
calling `create_post`. Offer to refine before they decide to publish.
