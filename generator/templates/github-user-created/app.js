(() => {
  const form = document.getElementById('github-user-form');
  const statusEl = document.getElementById('github-status');
  const createdEl = document.getElementById('github-created-at');
  const ageEl = document.getElementById('github-account-age');
  const userInput = document.getElementById('username');

  const LAST_KEY = 'last_github_username';

  function setStatus(msg) {
    statusEl.textContent = msg;
  }

  function yearsBetween(a, b) {
    const diff = b.getTime() - a.getTime();
    const years = diff / (1000 * 60 * 60 * 24 * 365.25);
    return Math.floor(years);
  }

  function fmtYYYYMMDDUTC(date) {
    const y = date.getUTCFullYear();
    const m = String(date.getUTCMonth() + 1).padStart(2, '0');
    const d = String(date.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }

  async function lookup(username) {
    setStatus('Fetching user…');
    try {
      const res = await fetch(`https://api.github.com/users/${encodeURIComponent(username)}`);
      if (!res.ok) throw new Error('User not found');
      const data = await res.json();
      const createdAt = new Date(data.created_at);
      createdEl.textContent = fmtYYYYMMDDUTC(createdAt);
      ageEl.textContent = String(yearsBetween(createdAt, new Date()));
      setStatus('Success');
      localStorage.setItem(LAST_KEY, username);
    } catch (e) {
      setStatus('Error fetching user.');
      createdEl.textContent = '—';
      ageEl.textContent = '—';
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const u = userInput.value.trim();
    if (u) lookup(u);
  });

  // Prefill from localStorage
  const last = localStorage.getItem(LAST_KEY);
  if (last) {
    userInput.value = last;
  }
})();

