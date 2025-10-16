(() => {
  const $ = (s) => document.querySelector(s);
  const srcEl = $('#markdown-source');
  const outEl = $('#markdown-output');
  const countEl = $('#markdown-word-count');
  const inject = window.__INJECT__ || {};

  function render() {
    const md = srcEl.value || '';
    const html = marked.parse(md, {
      highlight: (code, lang) => {
        if (lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
      }
    });
    outEl.innerHTML = html;
    const words = (md.trim().match(/\b\w+\b/g) || []).length;
    countEl.textContent = new Intl.NumberFormat().format(words);
  }

  const params = new URLSearchParams(location.search);
  const urlParam = params.get('url');

  (async () => {
    let source = inject.ATTACHMENT_TEXT || '';
    if (urlParam) {
      try {
        const res = await fetch(urlParam);
        source = await res.text();
      } catch (e) {
        source = '# Error fetching markdown from URL';
      }
    }
    srcEl.value = source || srcEl.value;
    render();
  })();

  srcEl.addEventListener('input', render);
})();

