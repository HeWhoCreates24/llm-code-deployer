import fs from "node:fs";
import path from "node:path";

const TEMPLATES = [
  { id: "captcha-solver",  match: /captcha.*\?url=/i },
  { id: "markdown-to-html", match: /markdown|marked|highlight\.js/i },
  { id: "sum-of-sales",     match: /sales.*csv/i },
  { id: "github-user-created", match: /github.*created/i },
];

function pickTemplate(brief) {
  if (!brief) return "fallback";
  const hit = TEMPLATES.find(t => t.match.test(brief));
  return hit ? hit.id : "fallback";
}

export function buildSiteFiles({ brief, attachments = [], seedTitle }) {
  const tpl = pickTemplate(brief);
  const baseDir = path.join(process.cwd(), "generator", "templates", tpl);

  const files = {};
  for (const f of ["index.html","app.js"]) {
    const p = path.join(baseDir, f);
    if (fs.existsSync(p)) {
      files[`site/${f}`] = fs.readFileSync(p, "utf8");
    }
  }

  // Inject inline bootstrapping vars into index.html
  if (files["site/index.html"]) {
    let boot = "<script>/* injected */\n";
    // Attachments
    if (attachments?.length) {
      const first = attachments[0];
      if (tpl === "captcha-solver" && first.url.startsWith("data:image/")) {
        boot += `window.ATTACHMENT_URL = "${first.url}";\n`;
      }
      if (tpl === "markdown-to-html" && first.url.startsWith("data:text/markdown")) {
        const text = Buffer.from(first.url.split(",")[1] || "", "base64").toString("utf8");
        files["site/app.js"] = (files["site/app.js"]||"") + `\nwindow.ATTACHMENT_TEXT=${JSON.stringify(text)};`;
      }
      if (tpl === "sum-of-sales" && first.url.startsWith("data:text/csv")) {
        const text = Buffer.from(first.url.split(",")[1] || "", "base64").toString("utf8");
        files["site/app.js"] = (files["site/app.js"]||"") + `\nwindow.ATTACHMENT_TEXT=${JSON.stringify(text)};`;
      }
    }
    if (tpl === "sum-of-sales" && seedTitle) {
      files["site/app.js"] = (files["site/app.js"]||"") + `\nwindow.PAGE_TITLE=${JSON.stringify(seedTitle)};`;
    }
    boot += "</script>\n";
    files["site/index.html"] = files["site/index.html"].replace("</body>", `${boot}</body>`);
  }

  return { tpl, files };
}
