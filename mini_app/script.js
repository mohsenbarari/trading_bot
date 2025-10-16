const API_BASE_URL = 'https://telegram.362514.ir';
const tg = window.Telegram.WebApp;
let jwtToken = null;
let appConfig = {};

// --- تابع برای نمایش پیام Toast ---
function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast show ${type}`;
    setTimeout(() => {
        toast.className = toast.className.replace('show', '');
    }, 3000);
}

async function fetchWithAuth(url, options = {}) {
    if (!jwtToken) {
        showToast("خطا: توکن احراز هویت موجود نیست.", 'error');
        return;
    }
    const headers = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${jwtToken}`,
        ...options.headers,
    };
    return fetch(url, { ...options, headers });
}

async function fetchUserDetails() {
    try {
        const response = await fetchWithAuth(`${API_BASE_URL}/api/auth/me`);
        if (!response.ok) throw new Error('Failed to fetch user details');
        const user = await response.json();

        if (user && user.role) {
            document.getElementById('userInfo').innerHTML = `<p>خوش آمدید, <strong>${user.full_name}</strong>!</p><p>سطح دسترسی: <strong>${user.role}</strong></p>`;
            if (user.role === 'مدیر ارشد') {
                document.getElementById('adminPanel').style.display = 'block';
            }
        } else {
            document.getElementById('userInfo').innerHTML = `<p>خطا: اطلاعات کاربر دریافت نشد.</p>`;
        }
    } catch (error) {
        document.getElementById('userInfo').innerHTML = `<p>خطای ارتباط با سرور (دریافت کاربر): ${error.message}</p>`;
    }
}

document.addEventListener('DOMContentLoaded', async function () {
    tg.ready();
    tg.expand();

    try {
        const configResponse = await fetch(`${API_BASE_URL}/api/config`);
        if (!configResponse.ok) throw new Error('Failed to load app config');
        appConfig = await configResponse.json();
    } catch (error) {
        document.getElementById('userInfo').innerHTML = `<p>خطای دریافت تنظیمات برنامه: ${error.message}</p>`;
        return;
    }

    if (!tg.initData) {
        document.getElementById('userInfo').innerHTML = "<p>لطفاً این صفحه را از طریق ربات تلگرام باز کنید.</p>";
        return;
    }
    
    try {
        const loginResponse = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ init_data: tg.initData })
        });

        if (!loginResponse.ok) {
            const err = await loginResponse.json();
            throw new Error(err.detail || 'Failed to login');
        }

        const data = await loginResponse.json();
        if (data.access_token) {
            jwtToken = data.access_token;
            await fetchUserDetails();
        } else {
            throw new Error(data.detail || 'توکن دریافت نشد');
        }
    } catch (error) {
        document.getElementById('userInfo').innerHTML = `<p>خطای ارتباط با سرور (لاگین): ${error.message}</p>`;
    }
});

document.getElementById('invitationForm').addEventListener('submit', async function (event) {
    event.preventDefault();
    const submitBtn = document.getElementById('submitBtn');
    const originalBtnText = submitBtn.textContent;

    submitBtn.disabled = true;
    submitBtn.textContent = 'در حال ساخت...';

    const payload = {
        account_name: document.getElementById('accountName').value,
        mobile_number: document.getElementById('mobileNumber').value,
        role: document.getElementById('role').value
    };
    
    try {
        const invResponse = await fetchWithAuth(`${API_BASE_URL}/api/invitations/`, {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        
        const data = await invResponse.json();

        if (invResponse.ok && data.token) {
            showToast('دعوتنامه با موفقیت ساخته شد!', 'success');
            const bot_username = appConfig.bot_username || 'YOUR_BOT_USERNAME';
            const bot_link = `https://t.me/${bot_username}?start=${data.token}`;
            
            const linkContainer = document.createElement('div');
            linkContainer.className = 'result-link';
            linkContainer.innerHTML = `<span>${bot_link}</span>`;
            
            const copyBtn = document.createElement('button');
            copyBtn.textContent = 'کپی';
            copyBtn.className = 'copy-btn';
            copyBtn.onclick = () => {
                navigator.clipboard.writeText(bot_link);
                showToast('لینک کپی شد!', 'success');
            };
            
            linkContainer.appendChild(copyBtn);
            
            const resultDiv = document.getElementById('userInfo');
            resultDiv.appendChild(linkContainer);

            document.getElementById('invitationForm').reset();

        } else { 
            showToast(`خطا: ${data.detail || 'مشکلی پیش آمد'}`, 'error');
        }
    } catch (error) {
        showToast(`خطای ارتباط با سرور: ${error.message}`, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalBtnText;
    }
});