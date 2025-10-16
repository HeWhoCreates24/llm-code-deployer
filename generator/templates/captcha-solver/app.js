(() => {
  const $ = (sel) => document.querySelector(sel);
  const params = new URLSearchParams(window.location.search);
  const urlParam = params.get('url');
  const inject = window.__INJECT__ || {};
  const imgUrl = urlParam || inject.ATTACHMENT_URL || '';
  const imgEl = $('#captcha');
  const resultEl = $('#result');

  if (!imgUrl) {
    resultEl.textContent = 'No image URL provided.';
    return;
  }

  imgEl.src = imgUrl;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);

  // Tesseract.js recognizes from a URL; we wrap in a try/catch for aborts.
  (async () => {
    try {
      resultEl.textContent = 'Solving...';
      const { data } = await Tesseract.recognize(imgUrl, 'eng', { logger: () => {} });
      resultEl.textContent = (data && data.text ? data.text : '').trim() || '(no text recognized)';
    } catch (err) {
      if (controller.signal.aborted) {
        resultEl.textContent = 'Timed out after 15 seconds.';
      } else {
        resultEl.textContent = 'Error recognizing captcha.';
      }
    } finally {
      clearTimeout(timeout);
    }
  })();
})();

