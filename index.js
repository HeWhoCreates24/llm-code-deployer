import express from 'express';
import bodyParser from 'body-parser';
import { Octokit } from '@octokit/rest';
import fetch from 'node-fetch';
import fs from 'fs/promises';
import path from 'path';
import { generateSite } from './generator/index.js';

// Debug toggle
const DEBUG = true;

const PORT = process.env.PORT ? Number(process.env.PORT) : 8787;
const GITHUB_TOKEN = process.env.GITHUB_TOKEN || '';
const GITHUB_OWNER = process.env.GITHUB_OWNER || '';
const SERVICE_SECRET = process.env.SERVICE_SECRET || '';

if (!GITHUB_TOKEN || !GITHUB_OWNER || !SERVICE_SECRET) {
  console.error('Missing required environment variables. Ensure GITHUB_TOKEN, GITHUB_OWNER, SERVICE_SECRET are set.');
  process.exit(1);
}

const app = express();
app.use(bodyParser.json({ limit: '8mb', strict: true }));

const octokit = new Octokit({
  auth: GITHUB_TOKEN,
  userAgent: 'llm-code-deployer/1.0.0',
  log: DEBUG ? console : undefined,
});

function slugifyTask(task) {
  const base = String(task || '').toLowerCase().replace(/[^a-z0-9\-_]+/g, '-').replace(/^-+|-+$/g, '');
  const short = base.slice(0, 55); // keep room for 'llm-'
  return short || 'task';
}

function deriveRepoName(task) {
  return `llm-${slugifyTask(task)}`.slice(0, 60);
}

async function getOwnerType(owner) {
  try {
    const u = await octokit.rest.users.getByUsername({ username: owner });
    return u.data.type; // 'User' or 'Organization'
  } catch {
    return 'User';
  }
}

async function ensureRepoExists(owner, repo) {
  try {
    const r = await octokit.rest.repos.get({ owner, repo });
    return r.data;
  } catch (e) {
    if (e.status !== 404) throw e;
    const type = await getOwnerType(owner);
    if (type === 'Organization') {
      const created = await octokit.rest.repos.createInOrg({ org: owner, name: repo, private: false, auto_init: true });
      return created.data;
    } else {
      const created = await octokit.rest.repos.createForAuthenticatedUser({ name: repo, private: false, auto_init: true });
      return created.data;
    }
  }
}

async function getDefaultBranch(owner, repo) {
  const r = await octokit.rest.repos.get({ owner, repo });
  return r.data.default_branch || 'main';
}

async function getFileShaIfExists(owner, repo, path, ref) {
  try {
    const res = await octokit.rest.repos.getContent({ owner, repo, path, ref });
    if (Array.isArray(res.data)) return null;
    return res.data.sha || null;
  } catch (e) {
    if (e.status === 404) return null;
    throw e;
  }
}

async function putFile({ owner, repo, path: filePath, content, message, branch }) {
  const sha = await getFileShaIfExists(owner, repo, filePath, branch);
  const res = await octokit.rest.repos.createOrUpdateFileContents({
    owner,
    repo,
    path: filePath,
    message,
    content: Buffer.from(content, 'utf8').toString('base64'),
    branch,
    sha: sha || undefined
  });
  return res.data.commit.sha;
}

async function readLocal(file) {
  const full = path.join(process.cwd(), file);
  return fs.readFile(full, 'utf8');
}

function yearNow() {
  return new Date().getFullYear();
}

async function buildLicense(owner) {
  const tmpl = await readLocal('mit-license.txt');
  return tmpl.replace('[year]', String(yearNow())).replace('[fullname]', owner);
}

async function buildReadme({ brief, checks = [], pagesUrl }) {
  const base = await readLocal('readme-base.md');
  const lines = [];
  lines.push(base.trim());
  lines.push('\n\n## Brief\n');
  lines.push(brief ? String(brief) : '(none)');
  if (checks && checks.length) {
    lines.push('\n\n## Checks\n');
    for (const c of checks) lines.push(`- ${c}`);
  }
  if (pagesUrl) {
    lines.push('\n\n## Deployment\n');
    lines.push(`- Pages URL: ${pagesUrl}`);
  }
  lines.push('\n\n## License\n');
  lines.push('MIT');
  return lines.join('\n');
}

async function ensureRepo({ task, brief, checks, attachments, round }) {
  const owner = GITHUB_OWNER;
  const repo = deriveRepoName(task);
  const repoData = await ensureRepoExists(owner, repo);
  const branch = await getDefaultBranch(owner, repo);

  const { files } = await generateSite({ brief, attachments });
  const licenseContent = await buildLicense(owner);
  const readmeContent = await buildReadme({ brief, checks, pagesUrl: `https://${owner}.github.io/${repo}/` });
  const workflowContent = await readLocal('workflows/deploy-pages.yml');

  const commitMessage = round === 2 ? 'feat: round2 update' : 'feat: initial app';

  // Write workflow
  await putFile({ owner, repo, path: '.github/workflows/deploy-pages.yml', content: workflowContent, message: commitMessage, branch });
  // Write license
  await putFile({ owner, repo, path: 'LICENSE', content: licenseContent, message: commitMessage, branch });
  // Write site files
  for (const [p, content] of Object.entries(files)) {
    await putFile({ owner, repo, path: p, content, message: commitMessage, branch });
  }
  // Write README
  await putFile({ owner, repo, path: 'README.md', content: readmeContent, message: commitMessage, branch });

  // Get HEAD commit SHA
  const ref = await octokit.rest.git.getRef({ owner, repo, ref: `heads/${branch}` });
  const commitSha = ref.data.object.sha;
  const pagesUrl = `https://${owner}.github.io/${repo}/`;
  return { owner, repo, commitSha, pagesUrl };
}

async function postWithBackoff(url, payload) {
  const delays = [1, 2, 4, 8, 16, 32];
  let lastStatus = 0;
  let lastText = '';
  for (let i = 0; i < delays.length; i++) {
    try {
      const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      lastStatus = res.status;
      if (res.ok) return { ok: true };
      lastText = await res.text().catch(() => '');
    } catch (e) {
      lastText = (e && e.message) ? e.message : String(e);
    }
    await new Promise((r) => setTimeout(r, delays[i] * 1000));
  }
  return { ok: false, status: lastStatus, error: lastText || 'callback failed' };
}

// Wrapper to match requested signature
async function postWithRetry(url, payload) {
  const res = await postWithBackoff(url, payload);
  return !!res.ok;
}

function validateBody(body) {
  const errors = [];
  const required = ['email', 'task', 'round', 'nonce', 'evaluation_url'];
  for (const k of required) {
    if (body[k] === undefined || body[k] === null || body[k] === '') errors.push(`missing field: ${k}`);
  }
  if (typeof body.round !== 'number') errors.push('round must be a number');
  return errors;
}

app.post('/task', async (req, res) => {
  try {
    const { email, secret, task, round, nonce, brief = '', checks = [], evaluation_url, attachments = [] } = req.body || {};

    if (!secret || secret !== SERVICE_SECRET) {
      return res.status(403).json({ ok: false, error: 'invalid secret' });
    }
    const errors = validateBody(req.body || {});
    if (errors.length) {
      return res.status(400).json({ ok: false, error: errors.join('; ') });
    }

    try {
      console.time('ensureRepo');
      const info = await ensureRepo({ task, brief, checks, attachments, round });
      console.timeEnd('ensureRepo');

      console.log(JSON.stringify({ level: 'info', event: 'task', task, round, repo: info.repo, commitSha: info.commitSha, pagesUrl: info.pagesUrl }));

      const payload = {
        email,
        task,
        round,
        nonce,
        repo_url: `https://${GITHUB_OWNER ? 'github.com/' + GITHUB_OWNER : 'github.com'}/${info.repo}`,
        commit_sha: info.commitSha,
        pages_url: info.pagesUrl,
      };

      console.time('postEval');
      const ok = await postWithRetry(evaluation_url, payload);
      console.timeEnd('postEval');

      return res.status(200).json({ ok: true, payload, note: ok ? undefined : 'evaluation_url not reachable after retries' });
    } catch (e) {
      console.error('TASK ERROR:', e?.stack || e?.message || e);
      return res.status(500).json({ ok: false, error: 'internal error' });
    }
  } catch (e) {
    console.error('Unhandled error:', e && e.message ? e.message : String(e));
    return res.status(500).json({ ok: false, error: 'internal error' });
  }
});

app.get('/', (_req, res) => {
  res.type('text').send('llm-code-deployer is running');
});

app.listen(PORT, () => {
  console.log(`Server listening on port ${PORT}`);
});
