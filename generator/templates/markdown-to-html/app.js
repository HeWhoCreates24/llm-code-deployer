
const out = document.getElementById("markdown-output");
const src = document.getElementById("markdown-source");
const label = document.getElementById("markdown-source-label");
const wc = document.getElementById("markdown-word-count");

async function loadMarkdown() {
  const qs = new URLSearchParams(location.search);
  const url = qs.get("url");
  let md = "";

  if (url) {
    label.textContent = `Loaded from URL`;
    md = await (await fetch(url)).text();
  } else if (window.ATTACHMENT_TEXT) {
    label.textContent = `Loaded from attachment`;
    md = window.ATTACHMENT_TEXT;
  } else {
    label.textContent = `No source`;
    md = "# Hello\n\nProvide ?url=... or attachment.";
  }

  src.textContent = md;
  marked.setOptions({ highlight: (code, lang) => (window.hljs.highlightAuto(code).value) });
  out.innerHTML = marked.parse(md);

  const words = md.trim().split(/\s+/).filter(Boolean).length;
  wc.textContent = new Intl.NumberFormat().format(words) + " words";
}
loadMarkdown();
