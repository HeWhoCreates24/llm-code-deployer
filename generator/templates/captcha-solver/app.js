
const qs = new URLSearchParams(location.search);
const urlParam = qs.get("url");
const imgEl = document.getElementById("captcha");
const srcEl = document.getElementById("img-src");
const resultEl = document.getElementById("result");

// window.ATTACHMENT_URL may be injected by generator when attachment provided
const src = urlParam || (window.ATTACHMENT_URL ?? "");

if (!src) {
  resultEl.textContent = "No image provided. Add ?url=https://.../image.png";
} else {
  imgEl.src = src;
  srcEl.textContent = src;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);

  Tesseract.recognize(src, "eng", { logger: () => {} })
    .then(({ data }) => {
      clearTimeout(timer);
      resultEl.textContent = (data && data.text) ? data.text.trim() : "(no text)";
    })
    .catch(err => {
      resultEl.textContent = "Error: " + (err.message || String(err));
    });
}
