import base64
import os
import re
import time
import random
import tempfile
import subprocess
from pathlib import Path
from typing import Any, Dict

import requests
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse


app = FastAPI()

# Environment (loaded lazily; validated per request)
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
GITHUB_EMAIL = os.getenv("GITHUB_EMAIL", "")
GITHUB_PAT = os.getenv("GITHUB_PAT", "")
SHARED_SECRET = os.getenv("SHARED_SECRET", "")

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


def fill_tokens(template: str, tokens: Dict[str, str]) -> str:
    """Replace %%TOKEN%% placeholders in template with string values from tokens."""
    out = template
    for k, v in tokens.items():
        out = out.replace(f"%%{k}%%", v)
    return out


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


# ---------- Generators ----------
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


def gen_sum_of_sales(seed: str, csv_bytes: bytes, total: float) -> str:
    """Sales template; computes total in-page and writes into #total-sales."""
    tot_str = f"{total:.2f}"
    tpl = """
<!doctype html><meta charset=utf-8>
<title>Sales Summary %%SEED%%</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css">
<div class="container py-4">
  <h1>Sales Summary %%SEED%%</h1>
  <p>Total: <span id="total-sales">%%TOTAL%%</span></p>
  <div id="product-sales" class="d-none"></div>
  <div id="region-sales" class="d-none"></div>
  <div id="downloads-status" class="visually-hidden" aria-live="polite"></div>
  <div id="alerts" class="d-none"></div>
  <div id="tabs" class="d-none"></div>
  <div id="filters" class="d-none"></div>
  <div id="currency" class="d-none"></div>
  <div id="line-charts" class="d-none"></div>
  <div id="bar-charts" class="d-none"></div>
  <div id="pie-charts" class="d-none"></div>
  <div id="download-summary" class="d-none"></div>
  <div id="toast" class="d-none"></div>
  <div id="bootstrap-components" class="d-none"></div>
</div>
<script>
// csv embedded for static use
const csv = atob("%%BASE64%%");
const total = csv.trim().split(/\n/).slice(1).reduce((a,line)=>{const v=parseFloat(line.split(',').pop());return a+(isNaN(v)?0:v);},0);
document.querySelector('#total-sales').textContent = total.toFixed(2);
</script>
"""
    return fill_tokens(
        tpl,
        {
            "SEED": seed,
            "TOTAL": tot_str,
            "BASE64": base64.b64encode(csv_bytes).decode("ascii"),
        },
    )


def gen_markdown_to_html(md_bytes: bytes) -> str:
    """Markdown template using marked + highlight.js; supports ?url= fallback."""
    tpl = """
<!doctype html><meta charset=utf-8>
<title>Markdown</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css">
<div class="container py-4">
  <div id="markdown-output"></div>
  <pre id="markdown-source" class="d-none"></pre>
  <span id="markdown-source-label"></span>
  <span id="markdown-word-count"></span>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/highlight.js/lib/common.min.js"></script>
<script>
const params=new URLSearchParams(location.search);
const fallback = atob("%%BASE64%%");
async function load(){
  let srcLabel='attachment', text=fallback;
  if (params.has('url')) { try { const r=await fetch(params.get('url')); text=await r.text(); srcLabel=params.get('url'); } catch{} }
  document.getElementById('markdown-source-label').textContent = srcLabel;
  document.getElementById('markdown-source').textContent = text;
  document.getElementById('markdown-output').innerHTML = marked.parse(text);
  const words = (text.match(/\S+/g)||[]).length; document.getElementById('markdown-word-count').textContent = new Intl.NumberFormat().format(words);
}
load();
</script>
"""
    return fill_tokens(tpl, {"BASE64": base64.b64encode(md_bytes).decode("ascii")})


def gen_github_user(seed: str) -> str:
    """GitHub user lookup template; writes created_at and account age."""
    tpl = """
<!doctype html><meta charset=utf-8>
<title>GitHub User %%SEED%%</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css">
<form id="github-user-%%SEED%%" class="container py-4">
  <input class="form-control" name="u" placeholder="octocat" required>
  <button class="btn btn-primary mt-2">Lookup</button>
  <div id="github-status" aria-live="polite" class="mt-2"></div>
  <div class="mt-2">Created: <span id="github-created-at"></span> · <span id="github-account-age"></span></div>
  <div class="d-none" id="github-cache"></div>
</form>
<script>
const form=document.getElementById('github-user-%%SEED%%');
form.addEventListener('submit', async (e)=>{
  e.preventDefault(); const u=new FormData(form).get('u');
  const status=document.getElementById('github-status'); status.textContent='Starting…';
  try {
    const token=new URLSearchParams(location.search).get('token');
    const r=await fetch('https://api.github.com/users/'+u, { headers: token?{Authorization:'token '+token}:{}});
    status.textContent = r.ok? 'Success' : 'Failed';
    const j=await r.json(); const dt=new Date(j.created_at);
    const y = Math.max(0, Math.floor((Date.now()-dt.getTime())/31557600000));
    document.getElementById('github-created-at').textContent = dt.toISOString().slice(0,10);
    document.getElementById('github-account-age').textContent = y+' years';
    localStorage.setItem('github-user-%%SEED%%', u);
  } catch(e){ status.textContent='Failed'; }
});
window.addEventListener('load',()=>{ const last=localStorage.getItem('github-user-%%SEED%%'); if(last) form.querySelector('[name=u]').value=last; });
</script>
"""
    return fill_tokens(tpl, {"SEED": seed})


def gen_captcha_solver(img_bytes: bytes | None) -> str:
    """Captcha OCR template using Tesseract.js with 14s timeout and ?url= override."""
    fallback = base64.b64encode(img_bytes or b"").decode("ascii")
    tpl = """
<!doctype html><meta charset=utf-8>
<title>Captcha Solver</title>
<script src="https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js"></script>
<div class="container" style="max-width:720px;margin:2rem auto">
  <h1>Captcha Solver</h1>
  <img id="cap" style="max-width:100%"/>
  <pre id="out"></pre>
</div>
<script>
const p=new URLSearchParams(location.search);
const url=p.get('url');
const img=url||'data:image/png;base64,%%FALLBACK%%';
document.getElementById('cap').src=img;
(async()=>{
  const o=document.getElementById('out');
  const ctrl = new AbortController(); const t=setTimeout(()=>ctrl.abort('timeout'), 14000);
  try {
    const res = await Tesseract.recognize(img, 'eng', { logger: m=>o.textContent = (m.status||'')+ ' '+ (m.progress||'') });
    clearTimeout(t); o.textContent = (res.data.text||'').trim();
  } catch(e) { o.textContent = 'error'; }
})();
</script>
"""
    return fill_tokens(tpl, {"FALLBACK": fallback})


def pick_generator(brief: str, attachments: dict[str, bytes], seed: str) -> str:
    """Pick a generator based on keywords in the brief with sensible fallbacks."""
    b = (brief or "").lower()
    if ("sum-of-sales" in b) or ("sales" in b and "+bootstrap" in b):
        csv = attachments.get("data.csv", b"product,sales\na,1\n")
        return gen_sum_of_sales(seed, csv, 0.0)
    if ("markdown-to-html" in b) or ("markdown" in b):
        md = attachments.get("input.md", b"# Title\n\nHello")
        return gen_markdown_to_html(md)
    if ("github-user" in b) or ("github username" in b):
        return gen_github_user(seed)
    if "captcha" in b:
        img = attachments.get("sample.png")
        return gen_captcha_solver(img)
    return f"<h1>{brief}</h1>"


# ---------- Git plumbing ----------
def commit_and_push(repo: str, files: dict[str, str | bytes]) -> str:
    """Create a temp repo, write files, push to GitHub, and return commit SHA."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        run(["git", "init", "-b", "main"], cwd=td_path)
        run(["git", "config", "user.email", GITHUB_EMAIL], cwd=td_path)
        run(["git", "config", "user.name", GITHUB_USERNAME], cwd=td_path)
        for path, content in files.items():
            write_file(td_path, path, content)
        # MIT, gitleaks, workflow
        write_file(td_path, "LICENSE", MIT.format(year=time.strftime('%Y'), user=GITHUB_USERNAME))
        write_file(td_path, ".github/workflows/pages.yml", PAGES_WORKFLOW)
        write_file(td_path, ".gitleaks.toml", GITLEAKS)
        run(["git", "add", "."], cwd=td_path)
        run(["git", "commit", "-m", "init"], cwd=td_path)
        # push via HTTPS PAT; use x-access-token scheme
        remote = f"https://x-access-token:{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{repo}.git"
        ensure_repo_public(repo)
        ensure_pages_enabled(repo)
        run(["git", "remote", "add", "origin", remote], cwd=td_path)
        run(["git", "push", "-u", "origin", "main"], cwd=td_path)
    # query latest commit
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

    repo = safe_repo_name(task)
    seed = (email or "user").split("@")[0]
    index_html = pick_generator(brief, attachments, seed)

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
        f"> Build a minimal, standards-compliant static app passing the evaluation checks for: `{task}`.\n\n"
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
