const form = document.getElementById("github-user-form");
const statusEl = document.getElementById("github-status");
const createdEl = document.getElementById("github-created-at");
const ageEl = document.getElementById("github-account-age");

function setStatus(msg){ statusEl.textContent = msg; }

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = new FormData(form).get("username");
  setStatus("Starting lookup…");
  try {
    const res = await fetch(`https://api.github.com/users/${encodeURIComponent(username)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    setStatus("Success.");
    const created = new Date(data.created_at);
    createdEl.textContent = created.toISOString().slice(0,10);
    const years = Math.max(0, Math.floor((Date.now()-created.getTime())/ (365.25*24*3600*1000)));
    ageEl.textContent = `${years} years`;
    localStorage.setItem("github-user-cache", JSON.stringify({username}));
  } catch (err) {
    setStatus("Failed: " + (err.message || String(err)));
  }
});

// repopulate
const cached = localStorage.getItem("github-user-cache");
if (cached) {
  try {
    const { username } = JSON.parse(cached);
    form.username.value = username;
  } catch {}
}
