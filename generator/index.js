import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const TEMPLATES_ROOT = path.join(__dirname, 'templates');

function chooseTemplate(brief = '') {
  const b = (brief || '').toLowerCase();
  const has = (s) => b.includes(s);
  if (has('captcha') && b.includes('?url=')) return 'captcha-solver';
  if (has('markdown') || has('marked') || has('highlight.js') || has('highlightjs')) return 'markdown-to-html';
  if ((has('csv') && has('sales')) || has('sum of sales')) return 'sum-of-sales';
  if ((has('github') && has('user') && (has('created') || has('creation'))) || has('created at')) return 'github-user-created';
  return 'fallback';
}

function parseDataUri(dataUri) {
  if (!dataUri || typeof dataUri !== 'string' || !dataUri.startsWith('data:')) return null;
  const firstComma = dataUri.indexOf(',');
  if (firstComma === -1) return null;
  const meta = dataUri.substring(5, firstComma); // skip 'data:'
  const data = dataUri.substring(firstComma + 1);
  const isBase64 = /;base64/i.test(meta);
  const mime = meta.replace(/;base64/i, '');
  try {
    const buffer = isBase64 ? Buffer.from(data, 'base64') : Buffer.from(decodeURIComponent(data), 'utf8');
    return { mime, buffer, text: buffer.toString('utf8') };
  } catch {
    return null;
  }
}

function injectVariablesIntoHtml(html, vars) {
  const serialized = 'window.__INJECT__ = ' + JSON.stringify(vars) + ';\n';
  const inlineScript = `<script>\n${serialized}</script>`;

  const idx = html.indexOf('<script');
  if (idx !== -1) {
    return html.slice(0, idx) + inlineScript + '\n' + html.slice(idx);
  }
  const headIdx = html.indexOf('</head>');
  if (headIdx !== -1) {
    return html.slice(0, headIdx) + inlineScript + '\n' + html.slice(headIdx);
  }
  const bodyIdx = html.indexOf('<body');
  if (bodyIdx !== -1) {
    const closeBody = html.indexOf('>', bodyIdx);
    if (closeBody !== -1) {
      return html.slice(0, closeBody + 1) + '\n' + inlineScript + '\n' + html.slice(closeBody + 1);
    }
  }
  return inlineScript + html;
}

async function loadTemplateFiles(tpl) {
  const dir = path.join(TEMPLATES_ROOT, tpl);
  try {
    await fs.access(dir);
  } catch {
    throw new Error(`Template dir not found: ${dir}`);
  }
  const htmlPath = path.join(dir, 'index.html');
  const jsPath = path.join(dir, 'app.js');

  let html;
  try {
    html = await fs.readFile(htmlPath, 'utf8');
  } catch {
    throw new Error(`Missing template file: ${htmlPath}`);
  }

  let js = '';
  try {
    js = await fs.readFile(jsPath, 'utf8');
  } catch {
    js = '';
  }
  return { html, js };
}

function derivePageTitleFromBrief(brief) {
  if (!brief) return null;
  const match = /sales summary[^\n\r]*/i.exec(brief);
  return match ? match[0].trim() : null;
}

export async function generateSite({ brief = '', attachments = [] }) {
  const tpl = chooseTemplate(brief);
  const { html: tplHtmlRaw, js: tplJs } = await loadTemplateFiles(tpl);

  const vars = { ATTACHMENTS: attachments.map(a => ({ name: a?.name, url: a?.url })) };

  const first = attachments && attachments.length ? attachments[0] : null;
  const parsed = first && typeof first.url === 'string' ? parseDataUri(first.url) : null;

  if (tpl === 'captcha-solver' && parsed && parsed.mime && /^image\//i.test(parsed.mime)) {
    vars.ATTACHMENT_URL = first.url; // keep as data URI
  }
  if (tpl === 'markdown-to-html' && parsed && /^text\/markdown$/i.test(parsed.mime)) {
    vars.ATTACHMENT_TEXT = parsed.text;
  }
  if (tpl === 'sum-of-sales' && parsed && /^text\/csv$/i.test(parsed.mime)) {
    vars.ATTACHMENT_TEXT = parsed.text;
    const t = derivePageTitleFromBrief(brief);
    if (t) vars.PAGE_TITLE = t;
  }
  if (tpl === 'github-user-created') {
    // no special injection beyond ATTACHMENTS
  }

  const injectedHtml = injectVariablesIntoHtml(tplHtmlRaw, vars);

  const files = {
    'site/index.html': injectedHtml,
    'site/app.js': tplJs
  };

  return { tpl, files };
}
