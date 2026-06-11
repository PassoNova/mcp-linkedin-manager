# Contributing to LinkedIn MCP

Thank you for considering a contribution. This document covers local development setup, conventions, the process for adding new tools, and how releases work.

---

## Local development setup

**Prerequisites:** Python 3.11+, [uv](https://astral.sh/uv), Git, Google Chrome (for Voyager tier tests).

```bash
git clone https://github.com/PassoNova/mcp-linkedin-manager.git
cd mcp-linkedin-manager/mcp
uv sync --group dev
```

This installs the server and all test dependencies into `.venv/` inside `mcp/`.

### Run the server locally

The server is launched from inside `mcp/` so bare imports (`from auth import ...`) resolve correctly:

```bash
cd mcp
uv run python server.py
```

For Claude Desktop / Claude Code, point `.mcp.json` at this path:

```json
{
  "mcpServers": {
    "linkedin-manager": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/mcp", "python", "server.py"],
      "env": {
        "LINKEDIN_CLIENT_ID": "<your-client-id>",
        "LINKEDIN_CLIENT_SECRET": "<your-client-secret>"
      }
    }
  }
}
```

### Live log tail

```bash
tail -f ~/.linkedin_mcp.log
```

Set `LINKEDIN_MCP_DEBUG=1` to mirror logs to stderr as well.

---

## Running tests

```bash
cd mcp
uv run pytest tests/ -v
```

Playwright pool tests are excluded from CI (require a display):

```bash
uv run pytest tests/ --ignore=tests/test_playwright_pool.py -v
```

Tests **never** touch real credentials. The `_isolate_files` autouse fixture in `conftest.py` redirects all file paths and keychain calls to `tmp_path`. Do not commit tests that make live API calls.

---

## Code structure

```
mcp/
├── auth.py           # OAuth flow, credential storage
├── client.py         # LinkedInClient (REST) + VoyagerClient (Playwright)
├── server.py         # MCP tool definitions and server entry point
├── cache.py          # In-memory TTL cache
├── log_config.py     # Rotating file logger
└── tests/            # Pytest test suite
```

All imports within `mcp/` use bare names (`from auth import ...`). This is intentional — the server is always launched from inside `mcp/`, which puts it on `sys.path`. Do not switch to relative imports.

---

## Adding a new tool

1. **Implement business logic** in `client.py` — either `LinkedInClient` (REST API) or `VoyagerClient` (Playwright / Voyager API).

2. **Register the MCP tool** in `server.py`. Follow this pattern exactly — it ensures consistent logging and error formatting:

   ```python
   @mcp.tool()
   def my_new_tool(param: str) -> str:
       """One-line docstring shown to the model as the tool description."""
       with _tool_log("my_new_tool", param=param):
           try:
               client = _get_client()
               result = client.my_method(param)
               return json.dumps(result, indent=2)
           except Exception as exc:
               return _format_error(exc)
   ```

   For Voyager-only tools, guard with:

   ```python
   voyager = _get_voyager_client()
   if not voyager:
       return "❌ Web session required. Run `authenticate` first, or call `set_web_session`."
   ```

3. **Add the tool to the README.md tool table** and document any required capability tier.

4. **Write tests** in `tests/`. Cover the happy path, the unauthenticated path, and any error cases your tool surfaces. Mock `httpx` and `keyring` — never call the real LinkedIn API.

5. **Update `CHANGELOG.md`** under `[Unreleased]` with a brief description of the new tool.

---

## Capability tiers

Tools must clearly document which tier they require:

| Tier | What's needed | How it's obtained |
|------|--------------|-------------------|
| `BASE` | Nothing | Always available |
| `OAUTH` | Valid access token | `authenticate(alias)` |
| `VOYAGER` | Token + `li_at` + `JSESSIONID` | Auto-captured from Chrome after `authenticate`; or `set_web_session` |

If a tool requires VOYAGER, check `_get_voyager_client()` and return a helpful message if it returns `None`. Do not silently fall back to a lower tier in a way that changes the response shape.

---

## LinkedIn API notes

- **Version header**: all calls to `https://api.linkedin.com/rest/*` must include `LinkedIn-Version: YYYYMM` (currently `202506`). This is set in `RESTLI_HEADER` in `client.py`. Do not call rest endpoints without it.
- **REST vs v2**: the `GET /v2/ugcPosts` finder is deprecated. Use `GET /rest/posts?q=author` with `REST_BASE`. See `client.get_posts()` for the reference implementation.
- **Standard OAuth scopes**: `openid`, `profile`, `email`, `w_member_social`. Partner-gated scopes (`rw_me`, `r_member_social`) are not available on standard developer apps and will return 403. Do not attempt to use them.
- **Voyager API**: the internal `https://www.linkedin.com/voyager/api/*` endpoints are undocumented and may change. Prefer REST API where possible; use Voyager only for capabilities unavailable via the official API.

---

## Pull request process

1. Branch from `main`: `git checkout -b feat/my-feature`
2. Keep commits atomic and follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
3. Ensure `uv run pytest tests/ --ignore=tests/test_playwright_pool.py` passes locally.
4. Open a pull request targeting `main`. The CI workflow runs automatically.
5. Squash-merge after approval. The auto-tag workflow creates a `v*` tag on merge, which triggers the release workflow that builds and publishes the `.plugin` archive to GitHub Releases.

---

## Release process

Releases are fully automated. On every merge to `main`:

1. `.github/workflows/auto-tag.yml` increments the patch version and pushes a `v*` tag.
2. `.github/workflows/release.yml` triggers on the new tag, builds the `.plugin` zip, and creates a GitHub Release with the archive as an asset.

To cut a minor or major release, manually push a tag with the desired version:

```bash
git tag v1.1.0
git push origin v1.1.0
```

This bypasses the auto-patch increment and uses whatever version you specified.

---

## Secrets required in the GitHub repository

| Secret | Used by | Purpose |
|---|---|---|
| `PAT_TOKEN` | `auto-tag.yml`, `release.yml` | Push tags and create releases (GITHUB_TOKEN cannot trigger cross-workflow events) |

Set these under **Settings → Secrets and variables → Actions**.
