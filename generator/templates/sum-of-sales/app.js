(() => {
  const inject = window.__INJECT__ || {};
  if (inject.PAGE_TITLE) {
    document.title = inject.PAGE_TITLE;
    const h1 = document.querySelector('h1');
    if (h1) h1.textContent = inject.PAGE_TITLE;
  }
  const csvText = inject.ATTACHMENT_TEXT || '';
  const out = document.querySelector('#total-sales');

  function parseCSV(text) {
    const lines = text.split(/\r?\n/).filter(Boolean);
    if (!lines.length) return { headers: [], rows: [] };
    const split = (line) => line.split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map(s => s.replace(/^\s*"|"\s*$/g, '').trim());
    const headers = split(lines[0]).map(h => h.toLowerCase());
    const rows = lines.slice(1).map(l => split(l));
    return { headers, rows };
  }

  function sumSales(text) {
    const { headers, rows } = parseCSV(text);
    if (!headers.length) return 0;
    let idx = headers.indexOf('sales');
    if (idx === -1) idx = headers.indexOf('amount');
    if (idx === -1) return 0;
    let total = 0;
    for (const row of rows) {
      const raw = row[idx] || '0';
      const num = parseFloat(String(raw).replace(/[^0-9.+-]/g, ''));
      if (!Number.isNaN(num)) total += num;
    }
    return total;
  }

  const total = sumSales(csvText);
  out.textContent = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(total);
})();

