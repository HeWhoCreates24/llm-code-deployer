(() => {
  const list = document.getElementById('attachments');
  const inject = window.__INJECT__ || {};
  const items = Array.isArray(inject.ATTACHMENTS) ? inject.ATTACHMENTS : [];
  if (!items.length) {
    list.innerHTML = '<li><em>No attachments provided.</em></li>';
    return;
  }
  for (const a of items) {
    const li = document.createElement('li');
    const name = a && a.name ? a.name : 'attachment';
    const url = a && a.url ? a.url : '#';
    const link = document.createElement('a');
    link.href = url;
    link.textContent = name;
    link.target = '_blank';
    li.appendChild(link);
    list.appendChild(li);
  }
})();

