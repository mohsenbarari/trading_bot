// app.js - interactions for mini app
const API_BASE_URL = window.API_BASE_URL || 'https://telegram.362514.ir';
const tg = window.Telegram ? window.Telegram.WebApp : null;
let jwtToken = null;

async function init() {
  if (tg) { try { tg.ready(); tg.expand(); } catch(e){} }

  try {
    const r = await fetch(`${API_BASE_URL}/api/config`);
    if (r.ok) {
      const cfg = await r.json();
      document.getElementById('botName').textContent = cfg.bot_username || 'ربات';
      document.getElementById('serverStatus').textContent = 'در دسترس';
    }
  } catch(e){
    document.getElementById('serverStatus').textContent = 'خطا در ارتباط';
  }
}

document.addEventListener('submit', async (ev) => {
  if (ev.target && ev.target.id === 'invitationForm') {
    ev.preventDefault();
    const name = document.getElementById('username').value.trim();
    const role = document.getElementById('role').value;
    const resEl = document.getElementById('result');
    resEl.textContent = 'درحال ارسال...';

    try {
      const res = await fetch(`${API_BASE_URL}/api/invitations/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: name, role })
      });

      if (res.ok) {
        resEl.textContent = '✅ دعوت‌نامه با موفقیت ارسال شد.';
      } else {
        const j = await res.json();
        resEl.textContent = j.detail || '❌ خطا در ارسال دعوت‌نامه.';
      }
    } catch(err) {
      resEl.textContent = 'خطای ارتباط: ' + err.message;
    }
  }
});

window.addEventListener('DOMContentLoaded', init);
