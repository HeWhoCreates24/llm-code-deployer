const el = document.getElementById("attachments");
if (Array.isArray(window.ATTACHMENTS) && window.ATTACHMENTS.length) {
  el.innerHTML = "<h2>Attachments</h2>" + window.ATTACHMENTS.map(a => `<div><a href="${a.url}">${a.name}</a></div>`).join("");
}
