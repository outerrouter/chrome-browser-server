# 24/7 Chrome Server with noVNC and Selenium

এই container server-side Chromium চালায় এবং এক public port থেকে password-protected noVNC, Selenium এবং health endpoint দেয়। আপনি noVNC/browser viewer বন্ধ করে বের হয়ে গেলেও server-এর Chrome tabs চলতে থাকে যতক্ষণ hosting service process চালু রাখে।

## Controls

- `KEEPALIVE_URLS`: comma-separated website URLs; প্রতিটি আলাদা tab-এ খুলবে
- `BROWSER_DURATION_MINUTES=0`: unlimited duration
- `BROWSER_DURATION_MINUTES=120`: 120 মিনিট পরে browser session cleanly বন্ধ হবে
- `BROWSER_PANEL_USER`: HTTP basic-auth username; default `browser`
- `BROWSER_PANEL_PASSWORD`: nginx/noVNC access password
- `SE_VNC_PASSWORD`: VNC password; না দিলে `BROWSER_PANEL_PASSWORD` ব্যবহার হবে
- `KEEPALIVE_INTERVAL_SECONDS`: Selenium health-check interval

## Local run

```bash
export BROWSER_PANEL_PASSWORD='shift78&'
export KEEPALIVE_URLS='https://example.com,https://your-webapp.example'
export BROWSER_DURATION_MINUTES='0'
docker compose up -d --build
```

তারপর `http://localhost:8080/` খুলুন। প্রথমে HTTP username/password চাইবে; username `browser`, password আপনার configured password। noVNC চাইলে একই password ব্যবহার করবে।

## Render deployment

1. এই directory-টি GitHub repository-তে push করুন।
2. Render-এ **New → Web Service → Existing Repository** নির্বাচন করুন অথবা Blueprint হিসেবে `render.yaml` ব্যবহার করুন।
3. Docker runtime নির্বাচন করুন।
4. Secret environment variables সেট করুন:

| Variable | Example | Purpose |
|---|---|---|
| `BROWSER_PANEL_PASSWORD` | আপনার secret password | HTTP/noVNC lock |
| `SE_VNC_PASSWORD` | একই secret password | VNC lock |
| `KEEPALIVE_URLS` | `https://site-a.com,https://site-b.com` | persistent tabs |
| `BROWSER_DURATION_MINUTES` | `0`, `60`, `720` | unlimited বা auto-stop |
| `KEEPALIVE_INTERVAL_SECONDS` | `60` | session health check |

### Render free-tier limitation

Render Free web services **15 মিনিট inbound traffic না থাকলে sleep করতে পারে**, এবং free instance-এ 512 MB RAM থাকে। তাই Render Free-তে duration timer ঠিকমতো কাজ করলেও genuine 24/7 guarantee করা যায় না। noVNC connection বা regular user traffic থাকলে service active থাকতে পারে, কিন্তু user disconnect করলে Render পরে sleep করতে পারে।

সত্যিকারের always-on Chrome-এর জন্য Render paid always-on instance বা একটি always-on VM ব্যবহার করুন। Chrome/noVNC-এর জন্য কমপক্ষে 2 GB RAM বেশি নিরাপদ।

## Security

- Password source code-এ hardcode করবেন না; Render secret বা local `.env` ব্যবহার করুন।
- `/wd/hub`, `/status`, noVNC এবং websocket—সব route HTTP basic auth-এর পিছনে আছে।
- `KEEPALIVE_URLS`-এ password বা private token দেওয়া URL রাখবেন না।
- Public service হলে long random password এবং private access/IP allowlist ব্যবহার করুন।
- এই browser server personal Chrome নয়; server-এর ভিতরের আলাদা Chromium profile।
