import base64
import os
import re
import time
import random
import tempfile
import subprocess
from pathlib import Path
from typing import Any, Dict
import json

import requests
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse


app = FastAPI()

# Environment (loaded lazily; validated per request)
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
GITHUB_EMAIL = os.getenv("GITHUB_EMAIL", "")
GITHUB_PAT = os.getenv("GITHUB_PAT", "")
SHARED_SECRET = os.getenv("SHARED_SECRET", "")

# Optional LLM gateway (OpenAI-compatible)
API_KEY = os.getenv("API_KEY", "")
API_BASE_URL = os.getenv("API_BASE_URL", "https://aipipe.org/openai/v1")
API_MODEL = os.getenv("API_MODEL", "gpt-4o-mini")

API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_PAT}",
}

# Types and constants
JSONDict = Dict[str, Any]
HTTP_TIMEOUT = 30


# ---------- Helpers ----------
def assert_secret(payload: JSONDict) -> None:
    """Raise 401 if the provided secret does not match the server's secret."""
    if payload.get("secret") != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")


def safe_repo_name(task: str) -> str:
    """Return a sanitized GitHub repo name for the task."""
    task_safe = re.sub(r"[^a-zA-Z0-9\-]+", "-", task).strip("-")[:80]
    return f"llm-task-{task_safe}"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a subprocess command with check=True."""
    subprocess.run(cmd, cwd=cwd, check=True)


def gh_api(method: str, url: str, json_body: JSONDict | None = None) -> JSONDict:
    """Perform a GitHub API call and raise on non-2xx responses."""
    r = requests.request(method, url, headers=API_HEADERS, json=json_body, timeout=HTTP_TIMEOUT)
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub API {method} {url} failed: {r.status_code} {r.text}")
    return r.json() if r.text else {}


def ensure_repo_public(repo: str) -> None:
    """Create the repo if missing; otherwise ensure it is public."""
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo}"
    r = requests.get(url, headers=API_HEADERS, timeout=HTTP_TIMEOUT)
    if r.status_code == 404:
        gh_api(
            "POST",
            "https://api.github.com/user/repos",
            {
                "name": repo,
                "private": False,
                "auto_init": False,
            },
        )
    elif r.status_code == 200:
        gh_api("PATCH", url, {"private": False})
    else:
        r.raise_for_status()


def ensure_pages_enabled(repo: str) -> None:
    """Enable GitHub Pages for the repo using workflow builds via server PAT.

    Idempotent: returns if the site exists; creates when 404.
    """
    base = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo}/pages"
    r = requests.get(base, headers=API_HEADERS, timeout=HTTP_TIMEOUT)
    if r.status_code == 200:
        return
    if r.status_code != 404:
        r.raise_for_status()
    r = requests.post(base, headers=API_HEADERS, json={"build_type": "workflow"}, timeout=HTTP_TIMEOUT)
    if r.status_code not in (201, 409):  # 409 = already exists race
        raise RuntimeError(f"Create Pages failed: {r.status_code} {r.text}")


def write_file(repo_dir: Path, rel: str, content: str | bytes) -> None:
    """Write text or bytes to a file path under repo_dir, creating parents."""
    p = repo_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")


# No template token filler needed; we now rely on the LLM for HTML.


MIT = (
    """
MIT License

Copyright (c) {year} {user}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
    .strip()
)


PAGES_WORKFLOW = (
    """
name: github-pages

on:
  push:
    branches: [ main ]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Upload site artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: dist
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4

"""
    .strip()
)


GITLEAKS = (
    """
[extend]
    paths = ["."]
"""
    .strip()
)


# ---------- Attachments ----------
def decode_attachments(attachments: list[Dict[str, str]] | None) -> Dict[str, bytes]:
    """Decode data: URLs in attachments into a mapping name->bytes."""
    out: Dict[str, bytes] = {}
    for att in attachments or []:
        name = att.get("name")
        url = att.get("url", "")
        if not name:
            continue
        if url.startswith("data:") and "," in url:
            b64 = url.split(",", 1)[1]
            try:
                out[name] = base64.b64decode(b64)
            except Exception:
                # skip invalid data URI
                pass
    return out


# ---------- LLM-backed generator ----------
def _extract_html(text: str) -> str:
    """Strip Markdown code fences and return raw HTML string."""
    if not text:
        return ""
    t = text.strip()
    # Remove ```html ... ``` or ``` ... ``` fences if present
    if t.startswith("```"):
        # find first newline after opening fence
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def _normalize_checks(checks: Any) -> list[str]:
    """Normalize request checks into concise strings for the LLM prompt."""
    out: list[str] = []
    if isinstance(checks, (list, tuple)):
        for c in checks:
            if isinstance(c, str):
                s = c
            elif isinstance(c, dict):
                try:
                    s = json.dumps(c, ensure_ascii=False, separators=(",", ":"))
                except Exception:
                    s = str(c)
            else:
                s = str(c)
            s = s.strip()
            if s:
                out.append(s[:800])
            if len(out) >= 40:
                break
    return out


def build_llm_prompt(task: str, brief: str, seed: str, attachments: dict[str, bytes], checks: Any | None = None) -> str:
    """Construct a concise, explicit prompt for generating a static HTML page.

    The prompt covers known templates (markdown, sales, github-user, captcha) and
    instructs the model to emit a single self-contained HTML document with the
    expected selectors used by the evaluator.
    """
    # Summarize attachments in a JSON-like manifest the model can act on.
    att_manifest: Dict[str, str] = {}
    for name, data in (attachments or {}).items():
        # Only include small samples (cap size) to avoid massive prompts
        sample = data[:2048]
        att_manifest[name] = base64.b64encode(sample).decode("ascii")

    hints = []
    b = (brief or "").lower()
    if ("sum-of-sales" in b) or ("sales" in b):
        hints.append(
            "- Sales: Include Bootstrap 5. Compute CSV last-column sum into #total-sales."
        )
    if ("markdown-to-html" in b) or ("markdown" in b):
        hints.append(
            "- Markdown: Use marked + highlight.js. Render into #markdown-output; show #markdown-source-label and #markdown-word-count using Intl.NumberFormat; support ?url= fallback to embedded attachment."
        )
    if ("github-user" in b) or ("github username" in b):
        hints.append(
            f"- GitHub user: Form id=\"github-user-{seed}\"; fetch https://api.github.com/users/<name>; write ISO date to #github-created-at; aria-live #github-status; years into #github-account-age; cache in localStorage."
        )
    if "captcha" in b:
        hints.append(
            "- Captcha: Use Tesseract.js v5 via CDN; support ?url=; fallback to embedded PNG data URI; print solved text within 15s."
        )
    # Incorporate explicit checks as additional implementation hints
    for chk in _normalize_checks(checks):
        hints.append(f"CHECK: {chk}")

    if not hints:
        hints.append("- Fallback: Echo the brief in an <h1> and include basic Bootstrap.")

    prompt = f"""
You are an expert frontend engineer. Produce a single, self-contained HTML file (no external build step) that satisfies the brief below and passes basic selector checks. Follow these strict rules:

- Output ONLY raw HTML. Do not include markdown code fences.
- Use plain HTML/CSS/JS with CDN scripts when needed. No frameworks besides allowed CDNs.
- The page must be static and run fully in the browser.
- Use seed = {seed} when constructing any required element IDs.
- If attachments are referenced below, embed their content as base64 strings and decode in JS at runtime.

Brief: {brief}
Task: {task}

Attachments (name -> base64_sample):
{json.dumps(att_manifest, indent=2)}

Implementation hints:
{chr(10).join(hints)}

Important:
- Include <!doctype html> and a <meta charset="utf-8">.
- Ensure the specified element IDs exist and are populated appropriately.
- If using external scripts: use jsDelivr for Bootstrap 5, marked, highlight.js, or Tesseract.js when applicable.
- Ensure the page remains functional without any server.
""".strip()

    return prompt


def llm_generate_static_html(task: str, brief: str, seed: str, attachments: dict[str, bytes], checks: Any | None = None) -> tuple[str | None, str]:
    """Call an OpenAI-compatible chat completion endpoint to generate HTML.

    Returns a tuple (html, prompt_used). If the call fails or output empty,
    returns (None, prompt_used).
    """
    prompt = build_llm_prompt(task, brief, seed, attachments, checks)

    if not API_KEY:
        return None, prompt

    url = f"{API_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": API_MODEL,
        "messages": [
            {"role": "system", "content": "You generate production-ready, self-contained static HTML only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=HTTP_TIMEOUT)
        if r.status_code >= 300:
            # Let caller fall back to deterministic templates
            return None, prompt
        data = r.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        html = _extract_html(content)
        if html and ("<html" in html.lower() or "<!doctype" in html.lower() or "<body" in html.lower()):
            return html, prompt
        return None, prompt
    except Exception:
        return None, prompt


def generate_index_html(brief: str, attachments: dict[str, bytes], seed: str, task: str, checks: Any | None = None) -> tuple[str, str]:
    """Try the LLM-backed generator first; fall back to deterministic templates.

    Returns (index_html, prompt_used).
    """
    html, prompt = llm_generate_static_html(task, brief, seed, attachments, checks)
    if html:
        return html, prompt
    # Fallback: minimal static page echoing the brief
    fallback_html = f"""
<!doctype html><meta charset="utf-8">
<title>Fallback App</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css">
<div class="container py-4">
  <h1>{brief}</h1>
  <p>Seed: {seed}</p>
  <p>This fallback rendered because the LLM call was unavailable.</p>
  <div id="status" class="visually-hidden" aria-live="polite"></div>
  <div id="content"></div>
  <script>document.getElementById('status').textContent='ready';</script>
</div>
"""
    fallback_prompt = (
        "LLM call failed or API_KEY missing; returned minimal static fallback."
    )
    return fallback_html, fallback_prompt


# ---------- Git plumbing ----------
def commit_and_push(repo: str, files: dict[str, str | bytes]) -> str:
    """Clone (if exists) or init repo, write files, push to GitHub, return commit SHA.

    Preserves history on round-2 by cloning and committing on top of origin/main.
    Falls back to rebase on rejection, then --force-with-lease as last resort.
    """
    remote = f"https://x-access-token:{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{repo}.git"
    ensure_repo_public(repo)
    ensure_pages_enabled(repo)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)

        cloned = False
        try:
            # Clone shallow if repo already has content
            run(["git", "clone", "--depth", "1", remote, "."], cwd=td_path)
            cloned = True
        except Exception:
            # Likely an empty repo or transient network; fall back to init
            run(["git", "init", "-b", "main"], cwd=td_path)
            run(["git", "remote", "add", "origin", remote], cwd=td_path)

        # Identity
        run(["git", "config", "user.email", GITHUB_EMAIL], cwd=td_path)
        run(["git", "config", "user.name", GITHUB_USERNAME], cwd=td_path)

        # Ensure we're on a main branch tracking origin/main when available
        if cloned:
            try:
                run(["git", "checkout", "-B", "main", "origin/main"], cwd=td_path)
            except Exception:
                run(["git", "checkout", "-B", "main"], cwd=td_path)
        else:
            # Newly initialized repository
            run(["git", "checkout", "-B", "main"], cwd=td_path)

        # Write/overwrite files
        for path, content in files.items():
            write_file(td_path, path, content)
        write_file(td_path, "LICENSE", MIT.format(year=time.strftime('%Y'), user=GITHUB_USERNAME))
        write_file(td_path, ".github/workflows/pages.yml", PAGES_WORKFLOW)
        write_file(td_path, ".gitleaks.toml", GITLEAKS)

        # Commit
        run(["git", "add", "."], cwd=td_path)
        try:
            run(["git", "commit", "-m", "update"], cwd=td_path)
        except Exception:
            # Nothing to commit (no changes); continue to push
            pass

        # Push with preserve-history strategy
        try:
            run(["git", "push", "-u", "origin", "main"], cwd=td_path)
        except Exception:
            try:
                # Try fetch + rebase (fast-forward the local branch)
                run(["git", "fetch", "origin", "main"], cwd=td_path)
                run(["git", "rebase", "origin/main"], cwd=td_path)
                run(["git", "push", "-u", "origin", "main"], cwd=td_path)
            except Exception:
                # Last resort to avoid repeated failures in CI races
                run(["git", "push", "--force-with-lease", "-u", "origin", "main"], cwd=td_path)

    # Query latest commit
    data = gh_api("GET", f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo}/commits/main")
    return data["sha"]


def pages_url(repo: str) -> str:
    """Compute the public GitHub Pages URL for the repo."""
    return f"https://{GITHUB_USERNAME}.github.io/{repo}/"


# ---------- Notify ----------
RETRYABLE = {429, 500, 502, 503, 504}

def notify(eval_url: str, payload: JSONDict, max_elapsed_sec: int = 600, first_delay: float = 1.0) -> bool:
    """Try to send the evaluation notification.

    Returns True if a 200 was received. Retries with exponential backoff + jitter
    for up to max_elapsed_sec.
    """
    deadline = time.time() + max_elapsed_sec
    delay = first_delay
    last_status: Any = None
    last_text = ""

    while time.time() < deadline:
        try:
            r = requests.post(
                eval_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if r.status_code == 200:
                return True
            last_status, last_text = r.status_code, (r.text or "")
            if r.status_code not in RETRYABLE:
                break
        except Exception as e:
            last_status, last_text = "EXC", str(e)

        time.sleep(delay + random.uniform(0, 0.5))
        delay = min(delay * 2, 60)

    # Could log last_status/last_text here
    return False


# ---------- Endpoint ----------
@app.post("/task")
async def handle(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    assert_secret(body)

    email = body.get("email")
    task = body.get("task")
    round_idx = int(body.get("round", 1))
    nonce = body.get("nonce")
    brief = body.get("brief", "")
    eval_url = body.get("evaluation_url")

    if not task or not isinstance(task, str):
        raise HTTPException(status_code=400, detail="Missing or invalid 'task'")
    if not eval_url or not isinstance(eval_url, str):
        raise HTTPException(status_code=400, detail="Missing or invalid 'evaluation_url'")

    # Ensure server env is configured
    missing = [k for k, v in {
        "GITHUB_USERNAME": GITHUB_USERNAME,
        "GITHUB_EMAIL": GITHUB_EMAIL,
        "GITHUB_PAT": GITHUB_PAT,
        "SHARED_SECRET": SHARED_SECRET,
    }.items() if not v]
    if missing:
        raise HTTPException(status_code=500, detail=f"Server misconfigured; missing env: {', '.join(missing)}")

    attachments = decode_attachments(body.get("attachments"))
    checks = body.get("checks")

    repo = safe_repo_name(task)
    seed = (email or "user").split("@")[0]
    index_html, llm_prompt_text = generate_index_html(brief, attachments, seed, task, checks)

    # Compose README content (kept minimal to match checks)
    readme = (
        f"# {task}\n\n"
        f"**Round {round_idx}**\n\n"
        "This app was generated by a tiny LLM-assisted generator (prompt recorded below) and deployed via GitHub Pages Actions.\n\n"
        "## Brief\n" + str(brief) + "\n\n"
        "## How it works\n"
        "- Static site in `dist/` is deployed by Actions.\n"
        "- Attachments (if any) decoded from data URIs.\n"
        "- Minimal JS implements checks.\n\n"
        "## LLM prompt excerpt\n"
        + (llm_prompt_text or "(no LLM prompt recorded)") + "\n\n"
        "## License\nMIT\n"
    )

    files: dict[str, str | bytes] = {
        "dist/index.html": index_html,
        "README.md": readme,
    }

    commit_sha = commit_and_push(repo, files)

    purl = pages_url(repo)

    payload = {
        "email": email,
        "task": task,
        "round": round_idx,
        "nonce": nonce,
        "repo_url": f"https://github.com/{GITHUB_USERNAME}/{repo}",
        "commit_sha": commit_sha,
        "pages_url": purl,
    }

    # Fast path: try once synchronously (up to ~5s). If not OK, retry in background up to 10 minutes.
    notified = notify(eval_url, payload, max_elapsed_sec=5, first_delay=1.0)
    if not notified:
        background_tasks.add_task(notify, eval_url, payload, 600, 1.0)

    return JSONResponse({"ok": True, "repo": repo, "commit": commit_sha, "pages_url": purl, "notified": notified})
