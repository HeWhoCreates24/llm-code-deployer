# LLM Code Deployer

Node.js service that receives instructor POSTs, generates a static site from a brief, creates/updates a public GitHub repo, deploys to GitHub Pages, and notifies an evaluation URL. Handles Round 1 and Round 2 identically with different commit messages.

## Requirements

- Node 20+
- Env vars:
  - `GITHUB_TOKEN` (fine-grained PAT: contents RW, metadata R, pages RW, administration RW)
  - `GITHUB_OWNER` (username or org)
  - `SERVICE_SECRET` (shared secret for POST validation)
  - `PORT` (optional, default 8787)

## Run

```bash
npm install
node index.js
```

## Endpoint

- `POST /task` — Content-Type: application/json

Body includes: `email`, `secret`, `task`, `round`, `nonce`, `brief`, `checks[]`, `evaluation_url`, `attachments[]` (objects with `name`, `url` as data URIs).

## What it does

- Derives repo name `llm-<task-slug>`
- Creates the repo if missing (public)
- Generates `/site` from a template router based on the brief
- Commits `/site/**`, `LICENSE` (MIT), `.github/workflows/deploy-pages.yml`, and a professional `README.md`
- Posts back to `evaluation_url` with retry backoff (1/2/4/8/16/32s)

## How to Test Locally

1) Start the server:

```bash
node index.js
```

2) POST Round 1 (replace placeholders with your env values):

```bash
curl -s -X POST http://localhost:8787/task \
  -H "Content-Type: application/json" \
  -d '{
    "email":"student@example.com",
    "secret":"<SERVICE_SECRET>",
    "task":"captcha-solver-xyz",
    "round":1,
    "nonce":"n-001",
    "brief":"Create a captcha solver that handles ?url=https://.../image.png. Default to attached sample.",
    "checks":["Repo has MIT license","README.md is professional","Page displays captcha URL passed at ?url=...","Page displays solved captcha text within 15 seconds"],
    "evaluation_url":"https://httpbin.org/status/200",
    "attachments":[{"name":"sample.png","url":"data:image/png;base64,iVBORw0..."}]
  }'
```

Expect HTTP 200 JSON with `repo_url`, `commit_sha`, `pages_url`.

3) Once Actions run, GitHub Pages publishes `/site`.

4) POST Round 2 with a modified brief; confirm a new commit and redeploy.

## Security & Hygiene

- Never logs secrets or full attachment payloads
- JSON body limit ~8 MB
- No database or queues

## License

MIT

