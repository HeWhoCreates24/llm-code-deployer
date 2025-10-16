
function parseCSV(text){
  const [header, ...rows] = text.trim().split(/\r?\n/);
  const cols = header.split(",");
  return rows.map(r => Object.fromEntries(r.split(",").map((v,i)=>[cols[i].trim(), v.trim()])));
}

(async () => {
  document.getElementById("title").textContent = window.PAGE_TITLE || "Sales Summary";
  const csv = window.ATTACHMENT_TEXT || "product,sales\n";
  const data = parseCSV(csv);
  const total = data.reduce((a, r) => a + (parseFloat(r.sales) || 0), 0);
  document.getElementById("total-sales").textContent = total.toFixed(2);
})();
