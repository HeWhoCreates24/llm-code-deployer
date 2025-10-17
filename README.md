LLM Code Deployer (FastAPI)
=================================

A minimal FastAPI microservice that turns incoming task briefs into static apps, pushes them to new public GitHub repositories, deploys via GitHub Pages Actions, and notifies an evaluation endpoint. Supports round‑2 updates to the same repo while preserving history.

Features
--------

- One endpoint: `POST /task`
- Verifies shared secret, generates `dist/index.html`
- Creates/updates repo `llm-task-<task>` under your GitHub account
- Deploys with GitHub Pages (Actions workflow)
- Notifies `evaluation_url` with retry backoff
- LLM‑assisted HTML generation via an OpenAI‑compatible API (aipipe), with a minimal fallback if the call fails
- Round‑2 safe: clones, rebases, and pushes on top of `origin/main` to preserve history

Environment Variables
---------------------

- `GITHUB_USERNAME` (required) – Your GitHub username
- `GITHUB_EMAIL` (required) – Your Git config email
- `GITHUB_PAT` (required) – Fine‑grained PAT with:
  - Repository: Contents (RW), Metadata (R), Actions (RW), Pages (RW), Administration (RW if available)
- `SHARED_SECRET` (required) – Shared secret that must match request `secret`
- `API_KEY` (optional, recommended) – Bearer token for the OpenAI‑compatible API
- `API_BASE_URL` (optional) – Defaults to `https://aipipe.org/openai/v1`
- `API_MODEL` (optional) – Defaults to `gpt-4o-mini`

Tip (Windows PowerShell):
```
$Env:GITHUB_USERNAME = 'your-user'
$Env:GITHUB_EMAIL    = 'you@example.com'
$Env:GITHUB_PAT      = '<gh_pat>'
$Env:SHARED_SECRET   = '<shared-secret>'
$Env:API_KEY         = '<aipipe-or-openai-compatible-key>'
```

Run Locally
-----------

```
python -m venv .venv
. .venv/Scripts/Activate.ps1   # Windows PowerShell
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Request Format (POST /task)
---------------------------

Content-Type: `application/json`

Fields:
- `email` (string) – User email; used to derive a stable seed
- `secret` (string) – Must exactly match server `SHARED_SECRET`
- `task` (string) – Task identifier; repo name becomes `llm-task-<task>`
- `round` (number, 1 or 2) – Round index
- `nonce` (string) – Opaque correlation id echoed back to evaluator
- `brief` (string) – Natural‑language brief
- `checks` (array<any>) – Acceptance checks; forwarded to the LLM prompt as hints
- `evaluation_url` (string) – Callback URL to notify `{ email, task, round, nonce, repo_url, commit_sha, pages_url }`
- `attachments` (array<object>) – Optional data URIs to include as inputs
  - Each attachment: `{ "name": "file.ext", "url": "data:<mime>;base64,<...>" }`

Example:
```
curl -X POST http://localhost:8000/task \
  -H 'Content-Type: application/json' \
  -d '{
    "email":"you@example.com",
    "secret":"<SHARED_SECRET>",
    "task":"github-user-created-localtest",
    "round":1,
    "nonce":"abc-123",
    "brief":"Build a Bootstrap page with a form id=\"github-user-seed\" and show created date.",
    "checks":["form id must be github-user-<seed>",{"selector":"#github-created-at"}],
    "evaluation_url":"https://httpbin.org/status/200",
    "attachments":[]
  }'
```

Response
--------

`200 OK` JSON:
```
{
  "ok": true,
  "repo": "llm-task-<task>",
  "commit": "<sha>",
  "pages_url": "https://<user>.github.io/llm-task-<task>/",
  "notified": true
}
```

How It Works
------------

- Validates `secret` against `SHARED_SECRET`.
- Decodes `attachments` (data URIs) into bytes.
- LLM generator builds a targeted prompt from `{task, brief, checks, attachments, seed}` and calls `POST {API_BASE_URL}/chat/completions` with `Authorization: Bearer {API_KEY}`.
  - If LLM fails or `API_KEY` not set, falls back to a minimal static page.
- Creates or ensures a public repo `llm-task-<task>` and Pages enabled.
- Writes files: `dist/index.html`, `README.md`, `LICENSE` (MIT), `.gitleaks.toml`, `.github/workflows/pages.yml`.
- Commits on top of `origin/main` (clone‑first). On push rejection, fetch+rebase, then last resort `--force-with-lease`.
- Notifies `evaluation_url` with exponential backoff (1,2,4,8,16,32...).

Round‑2 Behavior
----------------

Round‑2 requests reuse the same repo:
- The service clones the repo, updates the generated artifacts, commits, and pushes on top of `origin/main`.
- This preserves history and avoids non–fast‑forward errors.

Troubleshooting
---------------

- Invalid secret: ensure the running process has `SHARED_SECRET` set; restart after changing env vars.
- LLM fallback page: verify `API_KEY`, model support, and connectivity to `API_BASE_URL`.
- DNS/network errors: test `Resolve-DnsName api.github.com` and proxy configuration.
- Non–fast‑forward push (round‑2): handled automatically by clone→rebase→push logic.

Security Notes
--------------

- No secrets are committed to generated repos.
- A minimal `.gitleaks.toml` is included in generated repos; extend CI scanning as desired.

License
-------

Generated task repos receive an MIT LICENSE file with your GitHub username and the current year.
