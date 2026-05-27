# Deploying the Alph4 Labs ops dashboard

End-to-end: get this from `localhost:3000` onto a real URL your friend can use.

**What you're deploying:** `server.py` + `refresh_dashboard.py` + the HTML/CSS they emit. The server talks to the live alph4labs.com WooCommerce REST API on every page load and on every "mark paid" click.

**Cost:** ~$5/month on Railway (or $0 on Render's free tier with the caveat that it sleeps after 15 min of inactivity).

**Time:** 20-30 min if you have a GitHub account, 35-45 min if you don't.

---

## Step 1 · Get the code into GitHub

Railway and Render both deploy from a GitHub repo. We need to push these files there first.

### If you don't have a GitHub account yet:

1. Go to <https://github.com> and sign up (free)
2. Verify your email
3. You're in

### Create a new private repo:

1. Go to <https://github.com/new>
2. Repository name: `alph4-ops` (or whatever)
3. **Privacy: Private** (CRITICAL — this repo will contain your friend's customer-facing config)
4. Leave everything else blank
5. Click **Create repository**

### Upload the files (no command line needed):

1. On the new empty-repo page, click the link **"uploading an existing file"**
2. Open Finder → `~/Downloads/alph4labs_new/preview/`
3. Select **everything in that folder EXCEPT** `dashboard.html` and `.env` (if it exists) — gitignore will skip them on re-deploys but for first upload, just don't include them
4. Drag the files into the upload box
5. Scroll down, type a commit message like "Initial deploy"
6. Click **Commit changes**

---

## Step 2 · Deploy on Railway

1. Go to <https://railway.app> → **Login with GitHub**
2. Click **New Project** → **Deploy from GitHub repo**
3. If first time: Railway asks for permission to read your repos → grant
4. Pick the `alph4-ops` repo
5. Railway detects it's a Python project and starts building automatically — takes ~60 seconds

While that's building:

### Add the environment variables

In the Railway project view → **Variables** tab → **+ New Variable**. Add these four (one at a time):

| Variable | Value |
|---|---|
| `AUTH_USER` | `alph4` (or whatever username you want) |
| `AUTH_PASS` | a strong password — your friend and you will type this to log in |
| `WC_CONSUMER_KEY` | your WC API key starting `ck_…` |
| `WC_CONSUMER_SECRET` | your WC API secret starting `cs_…` |

After adding all four, Railway will auto-redeploy.

### Get the public URL

1. **Settings** tab → **Networking** section
2. Click **Generate Domain**
3. Railway gives you a URL like `alph4-ops-production.up.railway.app`
4. Open it in your browser
5. Browser pops a basic-auth prompt → enter the `AUTH_USER` / `AUTH_PASS` you set
6. The dashboard loads

Done. Send your friend the URL + credentials over Signal/WhatsApp.

---

## Alternative: Render (free tier)

Same idea, slightly different UI:

1. <https://render.com> → Sign in with GitHub
2. **New +** → **Web Service** → pick the repo
3. Settings:
   - Runtime: **Python 3**
   - Build command: `pip install -r requirements.txt`
   - Start command: `python3 server.py`
   - Plan: **Free** (or Starter $7/mo for always-on)
4. Environment tab: add the same 4 env vars
5. **Create Web Service**
6. Wait ~3 min for first build
7. URL appears at the top of the page

**Free tier caveat:** Render free-tier services spin down after 15 min of inactivity. First visit after idle takes ~30s to wake up. Fine for occasional use; annoying for daily use. Upgrade to Starter if your friend opens it multiple times a day.

---

## Custom domain (optional)

Both Railway and Render let you point a real domain like `ops.alph4labs.com` at the deployment.

1. In Railway/Render's networking settings, add the custom domain
2. They give you a CNAME target (e.g. `alph4-ops-production.up.railway.app`)
3. In your DNS provider (where alph4labs.com is registered — probably Namecheap or GoDaddy), add a CNAME record:
   - **Type:** CNAME
   - **Host:** ops
   - **Value:** the target Railway/Render gave you
   - TTL: leave default
4. Wait 5-30 min for DNS propagation
5. Visit `https://ops.alph4labs.com` — HTTPS is auto-provisioned

---

## After deploy

### To update the code:

1. Edit files locally in `~/Downloads/alph4labs_new/preview/`
2. Go to GitHub repo → **Add file** → **Upload files** → drag the updated file
3. Commit
4. Railway/Render auto-redeploys in ~60s

(Or if you ever set up `git` locally, just `git push` and it auto-deploys.)

### To rotate the password:

Railway/Render → Variables → update `AUTH_PASS` → save → auto-redeploys with new password. Old sessions stay logged in until they hit a 401.

### To revoke API access:

WP admin → WooCommerce → Settings → Advanced → REST API → delete the key. Generate a new one, update `WC_CONSUMER_KEY` and `WC_CONSUMER_SECRET` in Railway/Render env vars.

### To check it's alive:

Visit `https://your-url/health` — should return `{"ok": true, "service": "alph4-ops"}`. This endpoint has no auth so monitoring tools can hit it.

---

## What lives where

- **`server.py`** — the HTTP server. Handles auth, serves dashboard, proxies mark-paid POSTs to WC.
- **`refresh_dashboard.py`** — generates `dashboard.html` from live WC data. Called by server.py whenever the cached HTML is older than `CACHE_SECONDS` (default 60s).
- **`requirements.txt`** — Python dependencies (just `openpyxl`).
- **`railway.toml`** — Railway build/deploy config.
- **`Procfile`** — Render/Heroku start command (`web: python3 server.py`).
- **`runtime.txt`** — Python version pin (`python-3.11`).
- **`.env.example`** — template for local development; gitignored real `.env` should never get committed.

---

## Things to know about the running service

- **Auth is HTTP basic** — browsers cache the credentials per session. Logout = close the browser or hit `Cmd+Shift+Delete` → clear site data.
- **Every page load hits the WC API** (subject to the 60s cache). At your scale that's fine; if you ever hit rate limits, bump `CACHE_SECONDS`.
- **Mark-paid clicks invalidate the cache immediately** so the dashboard reflects the new status on the next refresh.
- **The on-Mac payment tracker spreadsheet is NOT uploaded** — production reads paid/unpaid from WooCommerce status (`on-hold` = unpaid, `processing`/`completed` = paid). Before going live, do one local pass to mark the 12 paid orders as `processing` in WC (the bulk button on the local dashboard does this in one click).
- **Logs** — Railway/Render both have a Logs tab showing every request + any errors. Useful for debugging.
