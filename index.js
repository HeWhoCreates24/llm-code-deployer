import express from "express";
import bodyParser from "body-parser";
import { Octokit } from "@octokit/rest";
import fs from "node:fs";
import path from "node:path";
import fetch from "node-fetch";
import crypto from "node:crypto";
import { buildSiteFiles } from "./generator/index.js";

const {
  GITHUB_TOKEN,
  GITHUB_OWNER,
  SERVICE_SECRET,
  PORT = "8787",
} = process.env;

if (!GITHUB_TOKEN || !GITHUB_OWNER || !SERVICE_SECRET) {
  console.error("Set GITHUB_TOKEN, GITHUB_OWNER, SERVICE_SECRET");
  process.exit(1);
}

const app = express();
app.use(bodyParser.json({ limit: "5mb" }));

const octo = new Octokit({ auth: GITHUB_TOKEN });

function mitLicense(owner) {
  return fs.readFileSync(path.join(process.cwd(), "mit-license.txt"), "utf8")
    .replace("[year]", new Date().getFullYear())
    .replace("[fullname]", owner);
}

function readmeContent({ brief, checks, pagesUrl }) {
  const base = fs.readFileSync(path.join(process.cwd(),"readme-base.md"), "utf8");
  return `${base}

## Brief
${brief || "N/A"}

## Checks
${(checks||[]).map(c => `- ${c}`).join("\n")}

## Deployment
- GitHub Pages: ${pagesUrl || "TBD"}

## License
MIT
`;
}

// create or update file helper
async function putFile({ owner, repo, path, content, message }) {
  let sha = undefined;
  try {
    const { data } = await octo.repos.getContent({ owner, repo, path });
    sha = data.sha;
  } catch {}
  await octo.repos.createOrUpdateFileContents({
    owner, repo, path, message,
    content: Buffer.from(content, "utf8").toString("base64"),
    sha
  });
}

function repoNameFor(task) {
  const safe = task.replace(/[^a-zA-Z0-9-_]/g, "-").slice(0,60);
  return `llm-${safe}`;
}

async function ensureRepo({ task, brief, checks, attachments, round }) {
  const owner = GITHUB_OWNER;
  const repo = repoNameFor(task);

  // create repo if missing
  try { await octo.repos.get({ owner, repo }); }
  catch {
    await octo.repos.createForAuthenticatedUser({
      name: repo, private: false, auto_init: true, description: `Auto-generated for ${task}`
    });
  }

  // site files
  const seedTitle = /Sales Summary/.test(brief) ? brief.match(/Sales Summary.*?(\d+)?/)?.[0] : undefined;
  const { files } = buildSiteFiles({ brief, attachments, seedTitle });

  // write files under /site and add workflow + license + README
  const commitMsg = round === 1 ? "feat: initial app" : "feat: round2 update";
  await putFile({ owner, repo, path: ".github/workflows/deploy-pages.yml",
    content: fs.readFileSync(path.join(process.cwd(),"workflows","deploy-pages.yml"), "utf8"),
    message: commitMsg
  });

  for (const [p, c] of Object.entries(files)) {
    await putFile({ owner, repo, path: p, content: c, message: commitMsg });
  }

  await putFile({ owner, repo, path: "LICENSE", content: mitLicense(owner), message: commitMsg });

  // README after we know pages URL structure
  const pagesUrl = `https://${owner}.github.io/${repo}/`;
  await putFile({ owner, repo, path: "README.md",
    content: readmeContent({ brief, checks, pagesUrl }),
    message: commitMsg
  });

  // get latest commit sha on main
  const { data: ref } = await octo.git.getRef({ owner, repo, ref: "heads/main" });
  const commitSha = ref.object.sha;

  return { owner, repo, commitSha, pagesUrl };
}

async function postWithRetry(url, payload) {
  let delay = 1000;
  for (let i = 0; i < 6; i++) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) return true;
    await new Promise(r => setTimeout(r, delay));
    delay *= 2;
  }
  return false;
}

app.post("/task", async (req, res) => {
  try {
    const { email, secret, task, round, nonce, brief, checks, evaluation_url, attachments } = req.body || {};

    if (secret !== SERVICE_SECRET) {
      return res.status(403).json({ ok:false, error: "invalid secret" });
    }
    if (!email || !task || !round || !nonce || !evaluation_url) {
      return res.status(400).json({ ok:false, error: "missing fields" });
    }

    // BUILD or REVISE
    const { repo, commitSha, pagesUrl } = await ensureRepo({
      task, brief, checks, attachments, round
    });

    // EVALUATE ping
    const payload = {
      email, task, round, nonce,
      repo_url: `https://github.com/${GITHUB_OWNER}/${repo}`,
      commit_sha: commitSha,
      pages_url: pagesUrl
    };
    const ok = await postWithRetry(evaluation_url, payload);
    if (!ok) {
      // still return 200 per spec we respond successfully to the instructor’s POST
      // but indicate evaluation push failed so they can retry from their side
      return res.status(200).json({ ok:true, note:"evaluation_url not reachable after retries", payload });
    }

    return res.status(200).json({ ok:true, payload });
  } catch (e) {
    console.error(e);
    return res.status(500).json({ ok:false, error: e.message || String(e) });
  }
});

app.listen(PORT, () => {
  console.log(`LLM Code Deployer listening on :${PORT}`);
});
