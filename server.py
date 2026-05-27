#!/usr/bin/env python3
"""
Alph4 Labs · Operations dashboard server.

Serves the dashboard with HTTP basic auth and proxies mark-as-paid
calls to the live WooCommerce REST API at alph4labs.com.

Production-ready for Railway / Render / any container host.

Required env vars:
  AUTH_USER         basic-auth username
  AUTH_PASS         basic-auth password
  WC_CONSUMER_KEY   WooCommerce REST API consumer key (ck_...)
  WC_CONSUMER_SECRET WooCommerce REST API consumer secret (cs_...)

Optional:
  PORT              defaults to 3000 (Railway/Render auto-set this)
  CACHE_SECONDS     dashboard regeneration interval, defaults to 60
"""
import http.server, socketserver, json, urllib.request, urllib.error, base64
import os, sys, subprocess, time, secrets
from urllib.parse import urlparse
from pathlib import Path

PORT           = int(os.environ.get("PORT", 3000))
AUTH_USER      = os.environ.get("AUTH_USER", "alph4")
AUTH_PASS      = os.environ.get("AUTH_PASS", "")
CK             = os.environ.get("WC_CONSUMER_KEY", "")
CS             = os.environ.get("WC_CONSUMER_SECRET", "")
CACHE_SECONDS  = int(os.environ.get("CACHE_SECONDS", 60))
WC_BASE        = "https://alph4labs.com/wp-json/wc/v3"

HERE = Path(__file__).parent.resolve()
DASHBOARD = HERE / "dashboard.html"

MISSING_VARS = [n for n, v in [
    ("WC_CONSUMER_KEY", CK),
    ("WC_CONSUMER_SECRET", CS),
] if not v]

if MISSING_VARS:
    print(f"⚠ Missing env vars: {', '.join(MISSING_VARS)}", file=sys.stderr)
    print("  Server will start in setup mode — set these in Railway → Variables tab.", file=sys.stderr)

if not AUTH_PASS:
    print("⚠ AUTH_PASS missing — generating an ephemeral password for this run.", file=sys.stderr)
    AUTH_PASS = secrets.token_urlsafe(12)
    print(f"  Username: {AUTH_USER}  Password: {AUTH_PASS}", file=sys.stderr)

EXPECTED_AUTH = "Basic " + base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode()).decode()

SETUP_PAGE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Alph4 Labs Ops — Setup needed</title>
<style>
  body { margin: 0; background: #08091A; color: #F4F6FC; font-family: -apple-system, sans-serif; min-height: 100vh; display: grid; place-items: center; padding: 40px; }
  .card { max-width: 600px; background: #0F1226; border: 1px solid #1F2447; border-radius: 16px; padding: 48px 40px; }
  h1 { font-size: 28px; margin: 0 0 8px; color: white; font-weight: 500; }
  .tag { font-size: 11px; letter-spacing: 0.22em; text-transform: uppercase; color: #F87171; font-weight: 600; margin-bottom: 24px; }
  p { color: #A0AAD0; line-height: 1.6; margin: 0 0 16px; }
  ol { color: #A0AAD0; line-height: 1.8; padding-left: 20px; }
  code { background: #161B3F; color: #B4C8FF; padding: 3px 8px; border-radius: 4px; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; }
  ul { color: #A0AAD0; line-height: 1.8; padding-left: 20px; margin-top: 8px; }
  .check { color: #34D399; }
  .miss { color: #F87171; }
</style>
</head><body>
<div class="card">
  <div class="tag">● Setup required</div>
  <h1>Alph4 Labs Ops · setup needed</h1>
  <p>The server is running but missing WooCommerce API credentials.</p>
  <ol>
    <li>Go to your Railway project → <b>Variables</b> tab</li>
    <li>Add these env vars (click <b>+ New Variable</b> for each):
      <ul>
        <li><code>WC_CONSUMER_KEY</code> &mdash; your <code>ck_…</code> key</li>
        <li><code>WC_CONSUMER_SECRET</code> &mdash; your <code>cs_…</code> secret</li>
        <li><code>AUTH_USER</code> &mdash; login username (e.g. <code>alph4</code>)</li>
        <li><code>AUTH_PASS</code> &mdash; a strong password</li>
      </ul>
    </li>
    <li>Railway auto-redeploys in ~30s</li>
    <li>Hard-refresh this page</li>
  </ol>
</div>
</body></html>"""

_last_built = 0.0


def regenerate_if_stale():
    """Run refresh_dashboard.py if dashboard.html is missing or older than CACHE_SECONDS."""
    global _last_built
    now = time.time()
    if DASHBOARD.exists() and (now - _last_built) < CACHE_SECONDS:
        return
    try:
        env = {**os.environ, "WC_CONSUMER_KEY": CK, "WC_CONSUMER_SECRET": CS}
        result = subprocess.run(
            [sys.executable, str(HERE / "refresh_dashboard.py")],
            cwd=str(HERE),
            env=env,
            capture_output=True,
            timeout=45,
        )
        if result.returncode == 0:
            _last_built = now
        else:
            print(f"refresh_dashboard.py failed (exit {result.returncode}):", file=sys.stderr)
            print(result.stderr.decode()[:2000], file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("refresh_dashboard.py timed out after 45s", file=sys.stderr)
    except Exception as e:
        print(f"refresh failed: {e}", file=sys.stderr)


def wc_update_order(order_id: int, new_status: str) -> dict:
    url = f"{WC_BASE}/orders/{order_id}"
    creds = base64.b64encode(f"{CK}:{CS}".encode()).decode()
    data = json.dumps({"status": new_status}).encode()
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "alph4-dashboard/5.0")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    # ── AUTH ─────────────────────────────────────────────────
    def _require_auth(self) -> bool:
        # Open endpoints (no auth required)
        if self.path == "/health":
            return True
        provided = self.headers.get("Authorization", "")
        if secrets.compare_digest(provided, EXPECTED_AUTH):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Alph4 Labs Ops"')
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Authentication required")
        return False

    def _json(self, code, payload):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    # ── GET ──────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)

        # Health probe (no auth, for Railway/Render)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "service": "alph4-ops", "missing_env": MISSING_VARS})
            return

        # If env vars are missing, show a setup page instead of normal auth flow
        if MISSING_VARS and parsed.path in ("/", "/index.html", "/dashboard.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SETUP_PAGE.encode())
            return

        if not self._require_auth():
            return

        # Root or /dashboard.html → regenerate-if-stale, then serve dashboard
        if parsed.path in ("/", "/index.html", "/dashboard.html"):
            regenerate_if_stale()
            if DASHBOARD.exists():
                content = DASHBOARD.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            else:
                self.send_error(503, "Dashboard not yet generated — refresh in a few seconds")
                return

        # Static files (img/, etc.) — fall through to SimpleHTTPRequestHandler
        super().do_GET()

    # ── POST (API) ───────────────────────────────────────────
    def do_POST(self):
        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")

        # POST /api/order/{id}/status   body: {"status": "completed"|"processing"|...}
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "order" and parts[3] == "status":
            order_id = parts[2]
            if not order_id.isdigit():
                return self._json(400, {"error": "Invalid order id"})

            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return self._json(400, {"error": "Invalid JSON body"})

            new_status = payload.get("status")
            if new_status not in ("completed", "processing", "cancelled", "on-hold", "refunded"):
                return self._json(400, {"error": f"Status '{new_status}' not allowed"})

            try:
                updated = wc_update_order(int(order_id), new_status)
                # Invalidate cache so next GET regenerates
                global _last_built
                _last_built = 0
                return self._json(200, {
                    "ok": True,
                    "order_id": updated.get("id"),
                    "status": updated.get("status"),
                    "total": updated.get("total"),
                    "customer": (updated.get("billing") or {}).get("email"),
                })
            except urllib.error.HTTPError as ex:
                msg = ex.read().decode("utf-8", errors="replace")[:300]
                return self._json(ex.code, {"error": f"WooCommerce returned {ex.code}", "detail": msg})
            except Exception as ex:
                return self._json(500, {"error": str(ex)})

        self._json(404, {"error": f"No endpoint at {parsed.path}"})


if __name__ == "__main__":
    # Pre-generate the dashboard once at startup so first request is fast
    print(f"Starting Alph4 Labs ops server on 0.0.0.0:{PORT}")
    print(f"Auth: {AUTH_USER} / {'*' * len(AUTH_PASS)}")
    print(f"Health probe: GET /health (no auth)")
    print(f"Pre-generating dashboard...")
    regenerate_if_stale()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"✓ Listening on http://0.0.0.0:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
