# LinkedIn MCP — Posts Operations

All post operations run on the **active account** (set via `switch_user`).
Use `list_users` to see which alias is currently active.

## Creating a post

When the user wants to publish something:

1. **Draft first** — if the user gives rough ideas rather than finished text,
   help them craft a polished post. LinkedIn posts that perform well tend to:
   - Open with a strong first line (no "I'm excited to share…")
   - Use short paragraphs (1–3 sentences each)
   - End with a question or call to action
   - Stay under 1,300 characters for maximum visibility (3,000 char limit)

2. **Confirm before posting** — always show the final text to the user and ask
   for explicit confirmation before calling `create_post`. Include the character
   count and visibility setting in the confirmation.

3. **Visibility** — default to PUBLIC. Ask if they want CONNECTIONS-only if
   the content seems personal or internal.

4. **After posting** — show the returned URN and tell the user to save it if
   they might want to delete the post later.

## Viewing posts

Call `get_posts` with a sensible count (default 10, up to 50).
Present results in a readable list:
- Creation date and visibility
- Text preview (first ~150 chars)
- URN (collapsed — only show in full if user asks to delete)

If the list is empty, tell the user no posts were found via the API and note
that older posts may not appear.

## Deleting a post

When the user wants to delete a post:
1. If they don't have the URN, call `get_posts` first and show the list
2. Confirm the specific post (show the text preview) before deleting
3. Warn explicitly: **"This cannot be undone."**
4. Only call `delete_post` after the user confirms
5. Confirm deletion with a brief success message

## Drafting without posting

If the user says "draft" or "help me write" without wanting to post immediately,
write the post text and present it for review without calling `create_post`.
Offer to refine it before they decide to publish.
