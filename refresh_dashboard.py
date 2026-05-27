#!/usr/bin/env python3
"""
Alph4 Labs · Operations dashboard
Pure data — no commentary, no narrative, no analysis.

Run:    python3 refresh_dashboard.py
View:   http://localhost:3000/dashboard.html
"""
import json, urllib.request, urllib.parse, base64, html, datetime, os
from collections import defaultdict, Counter
from pathlib import Path

# Optional dependency — only used if the local tracker xlsx exists
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# Credentials MUST come from environment variables — never hardcode them.
# For local dev, set them in your shell or use a .env loader.
CK = os.environ.get("WC_CONSUMER_KEY", "")
CS = os.environ.get("WC_CONSUMER_SECRET", "")
WC_BASE = "https://alph4labs.com/wp-json/wc/v3"

if not CK or not CS:
    import sys
    sys.stderr.write(
        "ERROR: WC_CONSUMER_KEY and WC_CONSUMER_SECRET must be set as environment variables.\n"
        "  Local dev:  export WC_CONSUMER_KEY=ck_... WC_CONSUMER_SECRET=cs_...\n"
        "  Production: set them in Railway/Render's Variables tab.\n"
    )
    sys.exit(1)

# Optional payment-tracker spreadsheet (only present on Leon's local Mac).
# If missing, fall back to WC order status as the paid/unpaid truth.
TRACKER_XLSX = Path(os.environ.get(
    "TRACKER_XLSX",
    "/Users/leonthick/Downloads/alph4_labs_payment_tracker.xlsx"
))


def wc_get(path):
    req = urllib.request.Request(WC_BASE + path)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{CK}:{CS}".encode()).decode())
    req.add_header("User-Agent", "alph4-dashboard/4.0")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def eur(n): return f"€{n:,.2f}"
def e(s):   return html.escape(str(s) if s is not None else "")
def qp(s):  return urllib.parse.quote(s)

def cur_split(n: float) -> str:
    """Split-weight currency: bold integer, lighter cents (Stripe pattern)."""
    integer = f"{int(n):,}"
    cents = f"{int(round((n - int(n)) * 100)):02d}"
    return f'€<span class="cur-int">{integer}</span><span class="cur-cents">.{cents}</span>'

def hours_since(date_str: str, time_str: str = "00:00") -> int:
    try:
        dt = datetime.datetime.fromisoformat(f"{date_str}T{time_str}:00")
        return int((datetime.datetime.now() - dt).total_seconds() / 3600)
    except Exception:
        return 0


orders = wc_get("/orders?per_page=100&status=any&orderby=date&order=desc")

# Tracker spreadsheet for paid/unpaid truth (optional — local dev only)
tracker_status, tracker_addr, tracker_phone = {}, {}, {}
if HAS_OPENPYXL and TRACKER_XLSX.exists():
    wb = openpyxl.load_workbook(TRACKER_XLSX, data_only=True)
    ws = wb["All Orders"]
    for row in ws.iter_rows(min_row=5, values_only=True):
        cells = (list(row) + [None] * 10)[:10]
        status, oid, _, _, _, phone, addr, _, _, _ = cells
        if not oid or status not in ("PAID", "UNPAID"):
            continue
        tracker_status[int(oid)] = status
        if addr: tracker_addr[int(oid)] = str(addr)
        if phone: tracker_phone[int(oid)] = str(phone)
# Else: tracker_status stays empty, and the code below falls back to WC status.

# Enrich each WC order
rows = []
for o in orders:
    oid = o["id"]
    b = o.get("billing") or {}
    name = f"{b.get('first_name','').strip()} {b.get('last_name','').strip()}".strip() or "(guest)"
    items = ", ".join(f"{li.get('quantity','')}× {li.get('name','?')}" for li in o.get("line_items", []))
    addr = tracker_addr.get(oid) or ", ".join(filter(None, [b.get("address_1"), b.get("city"), b.get("postcode"), b.get("country")]))
    phone = tracker_phone.get(oid) or b.get("phone") or ""
    digits = "".join(c for c in phone if c.isdigit())
    if digits and not digits.startswith("34") and (b.get("country") == "ES" or "+34" in phone):
        digits = "34" + digits.lstrip("0")
    rows.append({
        "id": oid, "name": name, "email": b.get("email", ""),
        "phone": phone, "phone_clean": digits, "addr": addr, "items": items,
        "total": float(o.get("total") or 0),
        "wc_status": o["status"], "tracker_status": tracker_status.get(oid),
        "date": o["date_created"][:10], "time": o["date_created"][11:16],
        "method": o.get("payment_method_title") or "—",
        "country": b.get("country", ""), "city": b.get("city", ""),
    })

# If tracker spreadsheet was loaded, use it as truth. Otherwise fall back to WC status:
#   on-hold      → UNPAID (awaiting bank transfer)
#   processing   → PAID   (transfer received, awaiting ship)
#   completed    → PAID   (paid + shipped + done)
if tracker_status:
    paid   = [c for c in rows if c["tracker_status"] == "PAID"]
    unpaid = [c for c in rows if c["tracker_status"] == "UNPAID"]
else:
    paid   = [c for c in rows if c["wc_status"] in ("processing", "completed")]
    unpaid = [c for c in rows if c["wc_status"] in ("on-hold", "pending")]
cancelled = [c for c in rows if c["wc_status"] in ("cancelled", "failed")]
active    = paid + unpaid

# Metrics
total_value   = sum(c["total"] for c in active)
paid_value    = sum(c["total"] for c in paid)
unpaid_value  = sum(c["total"] for c in unpaid)
pct_unpaid    = (unpaid_value / total_value * 100) if total_value else 0
aov           = (total_value / len(active)) if active else 0
collection    = (paid_value / total_value * 100) if total_value else 0

unique_paid_customers = len({c["email"].lower() for c in paid if c["email"]})
unique_owing_customers = len({c["email"].lower() for c in unpaid if c["email"]})
unique_active_customers = len({c["email"].lower() for c in active if c["email"]})

# Repeat unpaid offenders
unpaid_by_email = defaultdict(list)
for c in unpaid:
    unpaid_by_email[c["email"].lower()].append(c)
repeat_offenders = {em: lst for em, lst in unpaid_by_email.items() if len(lst) >= 2}

# Overdue calculations (Vercel pattern)
for c in unpaid:
    c["hours_old"] = hours_since(c["date"], c["time"])
unpaid_over_48h  = [c for c in unpaid if c["hours_old"] >= 48]
unpaid_over_72h  = [c for c in unpaid if c["hours_old"] >= 72]
unpaid_over_48h_value = sum(c["total"] for c in unpaid_over_48h)

# Date range
dates = sorted({c["date"] for c in active})
trading_days = len(dates) if dates else 1
date_first, date_last = (dates[0], dates[-1]) if dates else ("—", "—")

# Country counts (paid orders)
country_counts = Counter(c["country"] or "?" for c in paid)
country_unpaid = Counter(c["country"] or "?" for c in unpaid)

# Product split
product_units = Counter()
product_revenue = defaultdict(float)
for c in active:
    for piece in c["items"].split(", "):
        if "× " in piece:
            qty, name = piece.split("× ", 1)
            try: q = int(qty)
            except ValueError: q = 0
            product_units[name] += q
            product_revenue[name] += c["total"] * q / max(sum(int(p.split("× ")[0]) for p in c["items"].split(", ") if "× " in p), 1)

today = datetime.date.today()
def days_since(d):
    try: return (today - datetime.date.fromisoformat(d)).days
    except Exception: return 0

now = datetime.datetime.now().strftime("%a %d %b %Y · %H:%M GMT")

def flag(country):
    return {"ES":"🇪🇸","IN":"🇮🇳","AZ":"🇦🇿","GB":"🇬🇧","US":"🇺🇸","FR":"🇫🇷","DE":"🇩🇪","PT":"🇵🇹","IT":"🇮🇹"}.get(country, "🌍")


# ── HTML ─────────────────────────────────────────────────────
out = ""

out += f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Alph4 Labs · Operations</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
<style>
  :root {{
    --bg: #06081A;
    --bg-card: #0E1230;
    --bg-card-2: #161B3F;
    --ink: #F4F6FC;
    --ink-muted: #A0AAD0;
    --ink-faint: #5860A0;
    --line: #1F2554;
    --accent: #2E63FF;
    --bright: #6E9FFF;
    --periwinkle: #B4C8FF;
    --ultra: #0014B8;
    --good: #34D399;
    --good-soft: rgba(52,211,153,0.12);
    --warn: #FBBF24;
    --warn-soft: rgba(251,191,36,0.12);
    --bad: #F87171;
    --bad-soft: rgba(248,113,113,0.12);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ background: var(--bg); color: var(--ink); font-family: 'Inter', sans-serif; -webkit-font-smoothing: antialiased; }}
  h1, h2, h3 {{ font-family: 'Fraunces', serif; font-weight: 500; letter-spacing: -0.015em; }}
  .mono {{ font-family: 'JetBrains Mono', monospace; }}

  /* HERO */
  .hero {{
    background:
      radial-gradient(ellipse at 80% 0%, rgba(110,159,255,0.18) 0%, transparent 50%),
      radial-gradient(ellipse at 0% 100%, rgba(46,99,255,0.22) 0%, transparent 60%),
      linear-gradient(180deg, #0A1240 0%, #06081A 100%);
    padding: 56px 40px 96px;
    position: relative;
    border-bottom: 1px solid var(--line);
  }}
  .hero-inner {{ max-width: 1400px; margin: 0 auto; }}
  .hero-top {{ display: flex; justify-content: space-between; align-items: start; margin-bottom: 56px; flex-wrap: wrap; gap: 20px; }}
  .brand {{ font-family: 'Fraunces', serif; font-size: 32px; color: white; line-height: 1; }}
  .brand em {{ color: var(--periwinkle); font-style: italic; font-weight: 400; }}
  .brand-tag {{ font-size: 10px; letter-spacing: 0.3em; text-transform: uppercase; color: rgba(255,255,255,0.55); margin-top: 10px; font-weight: 600; }}
  .stamp {{ text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 11.5px; color: rgba(255,255,255,0.55); line-height: 1.8; }}
  .stamp .live {{
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    background: var(--good); box-shadow: 0 0 14px var(--good);
    margin-right: 7px; vertical-align: middle; animation: pulse 1.8s infinite;
  }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.35; }} }}

  .big {{
    font-family: 'Fraunces', serif;
    font-size: clamp(80px, 14vw, 180px);
    line-height: 0.88;
    color: white;
    letter-spacing: -0.045em;
    font-weight: 300;
    font-variant-numeric: tabular-nums;
  }}
  .big-sub {{ font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--periwinkle); margin-top: 16px; letter-spacing: 0.05em; }}

  /* Split-weight currency (Stripe pattern) */
  .cur-int    {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .cur-cents  {{ font-weight: 400; opacity: 0.65; font-variant-numeric: tabular-nums; }}
  .big .cur-int   {{ font-weight: 400; }}
  .big .cur-cents {{ font-weight: 300; opacity: 0.55; }}
  .metric .val .cur-int, .metric .val .cur-cents {{ font-weight: 500; }}

  /* Status pill with leading colour-dot (Stripe/Polaris pattern) */
  .pill-dot {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px 4px 8px; border-radius: 999px;
    font-size: 10.5px; font-weight: 500; letter-spacing: 0.04em;
    text-transform: lowercase; font-family: 'Inter', sans-serif;
  }}
  .pill-dot::before {{ content:''; width: 6px; height: 6px; border-radius: 50%; }}
  .pill-paid    {{ background: var(--good-soft); color: var(--good); }}
  .pill-paid::before    {{ background: var(--good); box-shadow: 0 0 6px var(--good); }}
  .pill-unpaid  {{ background: var(--warn-soft); color: var(--warn); }}
  .pill-unpaid::before  {{ background: var(--warn); }}
  .pill-overdue {{ background: var(--bad-soft); color: var(--bad); }}
  .pill-overdue::before {{ background: var(--bad); }}

  /* Attention dot (Vercel pattern) in leftmost meta of each row */
  .attn-dot {{
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    flex-shrink: 0;
  }}
  .attn-dot.amber {{ background: var(--warn); box-shadow: 0 0 8px rgba(251,191,36,0.5); }}
  .attn-dot.red   {{ background: var(--bad);  box-shadow: 0 0 10px rgba(248,113,113,0.55); animation: pulse 2s infinite; }}

  /* Top contextual banner (Vercel/Stripe/Shopify pattern) */
  .banner {{
    max-width: 1400px; margin: 32px auto 0; padding: 0 40px;
  }}
  .banner-inner {{
    background: linear-gradient(90deg, var(--bad-soft) 0%, transparent 100%);
    border: 1px solid rgba(248,113,113,0.3); border-left: 3px solid var(--bad);
    border-radius: 10px; padding: 18px 24px;
    display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  }}
  .banner-inner .msg {{ display: flex; align-items: center; gap: 12px; }}
  .banner-inner .msg .attn-dot.red {{ width: 9px; height: 9px; }}
  .banner-inner .msg-text {{ color: white; font-size: 14px; font-weight: 500; }}
  .banner-inner .msg-text strong {{ color: var(--bad); }}
  .banner-inner .msg-sub {{ display: block; color: var(--ink-muted); font-size: 11px; font-weight: 400; margin-top: 2px; }}

  /* Row-hover reveal on paid rows (Stripe/Vercel pattern) */
  .panel.paid .ord .acts {{ opacity: 0; transition: opacity 150ms ease; }}
  .panel.paid .ord:hover .acts {{ opacity: 1; }}
  .panel.paid .ord .acts .done {{ opacity: 1; }}
  .panel.paid .ord:has(.done) .acts {{ opacity: 1; }}  /* always show 'done' pill */

  /* Empty-state "You're caught up" (Vercel pattern) */
  .empty-state {{
    padding: 64px 32px; text-align: center; color: var(--ink-muted);
  }}
  .empty-state .check {{
    width: 48px; height: 48px; margin: 0 auto 18px;
    border-radius: 50%; background: var(--good-soft); color: var(--good);
    display: grid; place-items: center; font-size: 22px; font-weight: 600;
  }}
  .empty-state h4 {{ font-family: 'Fraunces', serif; font-size: 22px; color: white; font-weight: 500; margin-bottom: 6px; }}
  .empty-state p {{ font-size: 13px; color: var(--ink-faint); }}

  /* METRIC STRIP */
  .strip {{
    max-width: 1400px;
    margin: -56px auto 0;
    padding: 0 40px;
    position: relative;
    z-index: 2;
  }}
  .strip-inner {{
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: 16px;
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    overflow: hidden;
    box-shadow: 0 16px 56px rgba(0,0,0,0.5);
  }}
  @media (max-width: 1100px) {{ .strip-inner {{ grid-template-columns: repeat(3, 1fr); }} }}
  @media (max-width: 600px)  {{ .strip-inner {{ grid-template-columns: repeat(2, 1fr); }} }}
  .metric {{
    padding: 24px 26px;
    border-right: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
  }}
  .metric:last-child {{ border-right: 0; }}
  .metric .lbl {{ font-size: 10px; letter-spacing: 0.22em; text-transform: uppercase; color: var(--ink-faint); font-weight: 600; }}
  .metric .val {{ font-family: 'Fraunces', serif; font-size: 36px; color: white; margin-top: 12px; line-height: 1; font-weight: 500; font-variant-numeric: tabular-nums; }}
  .metric .val.good {{ color: var(--good); }}
  .metric .val.warn {{ color: var(--warn); }}
  .metric .val.accent {{ color: var(--bright); }}
  .metric .sub {{ font-size: 11px; color: var(--ink-faint); margin-top: 8px; font-family: 'JetBrains Mono', monospace; }}

  /* PANELS */
  main {{ max-width: 1400px; margin: 48px auto 80px; padding: 0 40px; }}
  .panels {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media (max-width: 1000px) {{ .panels {{ grid-template-columns: 1fr; }} }}

  .panel {{
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: 16px;
    overflow: hidden;
  }}
  .panel.paid {{ border-top: 3px solid var(--good); }}
  .panel.unpaid {{ border-top: 3px solid var(--warn); }}

  .phead {{ padding: 28px 32px 18px; display: flex; justify-content: space-between; align-items: end; gap: 16px; border-bottom: 1px solid var(--line); }}
  .phead h2 {{ font-size: 26px; color: white; }}
  .phead .count {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--ink-faint); text-align: right; line-height: 1.5; }}
  .phead .count strong {{ font-family: 'Fraunces', serif; font-weight: 500; font-size: 22px; display: block; color: white; }}
  .panel.paid .phead .count strong {{ color: var(--good); }}
  .panel.unpaid .phead .count strong {{ color: var(--warn); }}

  .pact {{ padding: 16px 32px 20px; border-bottom: 1px solid var(--line); display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
  .bulk-btn {{
    display: inline-flex; align-items: center; gap: 8px;
    background: var(--accent); color: white; border: 0;
    padding: 12px 22px; border-radius: 999px;
    font: 500 13px 'Inter', sans-serif; cursor: pointer;
    transition: all 200ms ease;
  }}
  .bulk-btn:hover {{ background: var(--bright); transform: translateY(-1px); }}
  .bulk-btn:disabled {{ background: var(--bg-card-2); color: var(--ink-faint); cursor: wait; }}
  .pact-meta {{ font-size: 12px; color: var(--ink-muted); }}

  .order-list {{ max-height: 720px; overflow-y: auto; padding: 8px; }}
  .order-list::-webkit-scrollbar {{ width: 6px; }}
  .order-list::-webkit-scrollbar-thumb {{ background: var(--line); border-radius: 3px; }}

  .ord {{
    display: grid; grid-template-columns: 1fr auto;
    gap: 16px; padding: 18px 24px;
    border-radius: 10px;
    transition: background 150ms ease;
  }}
  .ord:hover {{ background: var(--bg-card-2); }}
  .ord.rep {{ background: var(--bad-soft); border-left: 3px solid var(--bad); padding-left: 21px; }}

  .ord .meta {{ display: flex; gap: 8px; align-items: center; font-size: 11px; color: var(--ink-faint); margin-bottom: 5px; }}
  .ord .meta .id {{ font-family: 'JetBrains Mono', monospace; color: var(--bright); }}
  .ord .rep-tag {{ background: var(--bad); color: white; padding: 2px 7px; border-radius: 999px; font-size: 9.5px; font-weight: 600; letter-spacing: 0.06em; }}
  .ord .nm {{ font-size: 15px; font-weight: 500; color: white; line-height: 1.3; }}
  .ord .sub {{ font-size: 11.5px; color: var(--ink-muted); margin-top: 4px; line-height: 1.4; }}
  .ord .addr {{ color: var(--ink-faint); display: block; font-size: 10.5px; margin-top: 3px; font-family: 'JetBrains Mono', monospace; }}

  .ord .r {{ text-align: right; display: flex; flex-direction: column; justify-content: space-between; align-items: end; gap: 12px; }}
  .ord .amt {{ font-family: 'Fraunces', serif; font-size: 22px; color: white; font-weight: 500; line-height: 1; font-variant-numeric: tabular-nums; }}
  .phead .count strong {{ font-variant-numeric: tabular-nums; }}
  .panel.paid .amt {{ color: var(--good); }}
  .panel.unpaid .amt {{ color: var(--warn); }}

  .acts {{ display: flex; gap: 5px; flex-wrap: wrap; justify-content: end; }}
  .a {{
    display: inline-flex; align-items: center; gap: 5px;
    padding: 6px 11px; border-radius: 6px;
    font-size: 11px; font-weight: 500;
    text-decoration: none; cursor: pointer; border: 0;
    transition: all 150ms ease; white-space: nowrap;
  }}
  .a.mark {{ background: var(--accent); color: white; }}
  .a.mark:hover {{ background: var(--bright); }}
  .a.mark:disabled {{ background: var(--bg-card-2); color: var(--ink-faint); cursor: wait; }}
  .a.wa {{ background: #25D366; color: white; }}
  .a.wa:hover {{ background: #1FB856; }}
  .a.email {{ background: var(--bg-card-2); color: var(--periwinkle); border: 1px solid var(--line); }}
  .a.email:hover {{ background: var(--accent); color: white; border-color: var(--accent); }}
  .done {{ background: var(--good-soft); color: var(--good); padding: 6px 12px; border-radius: 6px; font-size: 11px; font-weight: 500; }}

  /* COUNTRY + PRODUCT mini-tables */
  .mini-row {{ margin-top: 32px; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media (max-width: 900px) {{ .mini-row {{ grid-template-columns: 1fr; }} }}
  .mini {{
    background: var(--bg-card); border: 1px solid var(--line);
    border-radius: 16px; padding: 24px 28px;
  }}
  .mini h3 {{ font-size: 18px; color: white; margin-bottom: 14px; }}
  .mini .row {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--line); font-size: 13px; }}
  .mini .row:last-child {{ border-bottom: 0; }}
  .mini .row .l {{ display: flex; align-items: center; gap: 10px; color: var(--ink); font-weight: 500; }}
  .mini .row .r {{ display: flex; gap: 16px; }}
  .mini .row .n {{ font-family: 'JetBrains Mono', monospace; color: var(--ink-muted); font-size: 12px; min-width: 60px; text-align: right; }}
  .mini .bar {{ width: 100%; height: 6px; background: var(--bg-card-2); border-radius: 999px; overflow: hidden; margin-top: 4px; }}
  .mini .bar .fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--bright)); }}

  /* TOAST */
  .toast {{
    position: fixed; bottom: 28px; right: 28px;
    background: var(--good); color: white;
    padding: 14px 22px; border-radius: 10px;
    font: 500 13px 'Inter', sans-serif;
    box-shadow: 0 12px 36px rgba(0,0,0,0.5);
    z-index: 9999; opacity: 0; transform: translateY(20px);
    transition: all 250ms ease;
  }}
  .toast.show {{ opacity: 1; transform: translateY(0); }}
  .toast.error {{ background: var(--bad); }}

  footer.footer {{ max-width: 1400px; margin: 60px auto 0; padding: 32px 40px; border-top: 1px solid var(--line); font-size: 11px; color: var(--ink-faint); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; }}
  footer code {{ background: var(--bg-card); padding: 3px 8px; border-radius: 4px; font-family: 'JetBrains Mono', monospace; color: var(--periwinkle); border: 1px solid var(--line); font-size: 11px; }}
</style>
</head>
<body>

<header class="hero">
  <div class="hero-inner">
    <div class="hero-top">
      <div>
        <div class="brand">Alph4<em> labs</em></div>
        <div class="brand-tag">Operations · live</div>
      </div>
      <div class="stamp">
        <span class="live"></span>{e(now)}<br>
        Data range {e(date_first)} → {e(date_last)} · {trading_days} trading days
      </div>
    </div>

    <div class="big">{cur_split(total_value)}</div>
    <div class="big-sub">TOTAL ORDER VALUE · {len(active)} ORDERS · {unique_active_customers} CUSTOMERS</div>
  </div>
</header>
"""

out += (f"""<!-- Contextual urgency banner -->
<div class="banner">
  <div class="banner-inner">
    <div class="msg">
      <span class="attn-dot red"></span>
      <div class="msg-text">
        <strong>{len(unpaid_over_48h)} unpaid orders</strong> are now older than 48 hours · {cur_split(unpaid_over_48h_value)} outstanding
        <span class="msg-sub">{len(unpaid_over_72h)} of those are older than 72h. Chase first.</span>
      </div>
    </div>
    <a href="#chase-panel" style="background: var(--bad); color: white; padding: 10px 20px; border-radius: 999px; font-size: 12px; font-weight: 500; text-decoration: none; white-space: nowrap;">Jump to chase list →</a>
  </div>
</div>
""" if unpaid_over_48h else "")

out += f"""

<div class="strip">
  <div class="strip-inner">
    <div class="metric">
      <div class="lbl">Paid</div>
      <div class="val good">{len(paid)}</div>
      <div class="sub">{eur(paid_value)} · {unique_paid_customers} customers</div>
    </div>
    <div class="metric">
      <div class="lbl">Unpaid</div>
      <div class="val warn">{len(unpaid)}</div>
      <div class="sub">{eur(unpaid_value)} · {unique_owing_customers} customers</div>
    </div>
    <div class="metric">
      <div class="lbl">Collection rate</div>
      <div class="val accent">{collection:.0f}%</div>
      <div class="sub">{eur(paid_value)} of {eur(total_value)}</div>
    </div>
    <div class="metric">
      <div class="lbl">AOV</div>
      <div class="val">{eur(aov)}</div>
      <div class="sub">avg order value</div>
    </div>
    <div class="metric">
      <div class="lbl">Repeat unpaid</div>
      <div class="val warn">{len(repeat_offenders)}</div>
      <div class="sub">customers with 2+ unpaid</div>
    </div>
    <div class="metric">
      <div class="lbl">Cancelled</div>
      <div class="val">{len(cancelled)}</div>
      <div class="sub">{eur(sum(c['total'] for c in cancelled))}</div>
    </div>
  </div>
</div>

<main>
  <div class="panels">

    <!-- READY TO SHIP / PAID -->
    <section class="panel paid">
      <div class="phead">
        <h2>Paid · ready to ship</h2>
        <div class="count"><span class="pill-dot pill-paid">paid</span><strong>{cur_split(paid_value)}</strong>{len(paid)} orders</div>
      </div>
"""

if paid:
    out += f"""      <div class="pact">
        <button class="bulk-btn" id="bulk-paid"
          data-ids='{json.dumps([c["id"] for c in paid if c["wc_status"] != "completed"])}'>
          ✓ Mark all {len([c for c in paid if c["wc_status"] != "completed"])} as completed in WC
        </button>
        <span class="pact-meta">Writes to live alph4labs.com · confirms first</span>
      </div>
      <div class="order-list">
"""

    for c in sorted(paid, key=lambda x: -x["total"]):
        already = c["wc_status"] == "completed"
        action = (f'<span class="done">✓ done</span>' if already else
                  f'<button class="a mark" data-id="{c["id"]}" data-name="{e(c["name"])}" data-total="{c["total"]:.2f}">✓ Mark completed</button>')
        out += f"""        <div class="ord" id="paid-{c['id']}">
          <div>
            <div class="meta"><span class="id">#{c['id']}</span> · {e(c['date'])} · {flag(c['country'])} {e(c['city'])}</div>
            <div class="nm">{e(c['name'])}</div>
            <div class="sub">{e(c['items'])}<span class="addr">{e(c['addr'])[:90]}</span></div>
          </div>
          <div class="r">
            <div class="amt">{cur_split(c['total'])}</div>
            <div class="acts">{action}</div>
          </div>
        </div>
"""
    out += "      </div>\n"
else:
    out += """      <div class="empty-state">
        <div class="check">✓</div>
        <h4>Nothing to ship.</h4>
        <p>All paid orders are marked completed in WC.</p>
      </div>
"""

out += f"""    </section>

    <!-- CHASE -->
    <section class="panel unpaid" id="chase-panel">
      <div class="phead">
        <h2>Unpaid · chase</h2>
        <div class="count"><span class="pill-dot pill-unpaid">owed</span><strong>{cur_split(unpaid_value)}</strong>{len(unpaid)} orders</div>
      </div>
"""

if unpaid:
    out += f"""      <div class="pact">
        <span class="pact-meta"><b style="color:var(--bad);">{len(repeat_offenders)} repeat offenders</b> highlighted · sorted by amount owed · WhatsApp + email pre-filled</span>
      </div>
      <div class="order-list">
"""
else:
    out += """      <div class="empty-state">
        <div class="check">✓</div>
        <h4>You're caught up.</h4>
        <p>No outstanding payments to chase.</p>
      </div>
    </section>
  </div>
"""


def sort_key(c):
    is_rep = c["email"].lower() in repeat_offenders
    rep_total = sum(o["total"] for o in repeat_offenders.get(c["email"].lower(), []))
    return (-int(is_rep), -rep_total, -c["total"], c["date"])

for c in sorted(unpaid, key=sort_key):
    is_rep = c["email"].lower() in repeat_offenders
    days = days_since(c["date"])
    days_str = f"{days}d ago" if days else "today"
    rep_tag = ""
    if is_rep:
        rep_count = len(repeat_offenders[c["email"].lower()])
        rep_tag = f'<span class="rep-tag">{rep_count}× unpaid</span>'

    first_name = c["name"].split()[0] if c["name"].split() else ""
    wa_msg = f"Hola {first_name}, recordatorio amistoso de tu pedido #{c['id']} en Alph4 Labs por €{c['total']:.2f}. Te enviaremos el seguimiento en cuanto recibamos el pago. Gracias!"
    wa_url = f"https://wa.me/{c['phone_clean']}?text={qp(wa_msg)}" if c['phone_clean'] else ""

    email_subj = f"Reminder: Order #{c['id']} payment outstanding"
    email_body = f"Hi {first_name},\n\nFriendly reminder that order #{c['id']} for €{c['total']:.2f} is still awaiting payment.\n\nOnce we receive your bank transfer we will dispatch the same business day.\n\nReply if you need the bank details resent.\n\nThanks,\nAlph4 Labs"
    email_url = f"mailto:{c['email']}?subject={qp(email_subj)}&body={qp(email_body)}" if c['email'] else ""

    acts = ""
    if wa_url: acts += f'<a class="a wa" href="{wa_url}" target="_blank" rel="noopener">WhatsApp</a>'
    if email_url: acts += f'<a class="a email" href="{email_url}">Email</a>'
    acts += f'<button class="a mark" data-id="{c["id"]}" data-name="{e(c["name"])}" data-total="{c["total"]:.2f}">Got paid</button>'

    # Attention dot (Vercel pattern) — amber >24h, red >72h
    dot_html = ""
    if c["hours_old"] >= 72:
        dot_html = '<span class="attn-dot red" title="More than 72h overdue"></span>'
    elif c["hours_old"] >= 24:
        dot_html = '<span class="attn-dot amber" title="More than 24h overdue"></span>'

    out += f"""        <div class="ord {'rep' if is_rep else ''}" id="unpaid-{c['id']}">
          <div>
            <div class="meta">
              {dot_html}
              <span class="id">#{c['id']}</span> · {e(c['date'])} · {days_str} · {flag(c['country'])} {e(c['city'])}
              {rep_tag}
            </div>
            <div class="nm">{e(c['name'])}</div>
            <div class="sub">{e(c['email'])}{' · ' + e(c['phone']) if c['phone'] else ''}<span class="addr">{e(c['addr'])[:90]}</span></div>
          </div>
          <div class="r">
            <div class="amt">{cur_split(c['total'])}</div>
            <div class="acts">{acts}</div>
          </div>
        </div>
"""

if unpaid:
    out += """      </div>
    </section>
  </div>
"""

out += f"""
  <!-- COUNTRY + PRODUCT mini-tables -->
  <div class="mini-row">
    <div class="mini">
      <h3>Customers by country</h3>
"""
total_country = sum(country_counts.values()) + sum(country_unpaid.values())
all_countries = set(country_counts.keys()) | set(country_unpaid.keys())
for cn in sorted(all_countries, key=lambda x: -(country_counts.get(x, 0) + country_unpaid.get(x, 0))):
    total_cn = country_counts.get(cn, 0) + country_unpaid.get(cn, 0)
    pct = (total_cn / total_country * 100) if total_country else 0
    out += f"""      <div class="row">
        <div class="l">{flag(cn)} {e(cn)}</div>
        <div class="r">
          <span class="n" style="color:var(--good);">{country_counts.get(cn, 0)} paid</span>
          <span class="n" style="color:var(--warn);">{country_unpaid.get(cn, 0)} unpaid</span>
          <span class="n">{pct:.0f}%</span>
        </div>
      </div>
"""

out += """    </div>
    <div class="mini">
      <h3>Products ordered</h3>
"""
max_units = max(product_units.values(), default=1)
for name, units in product_units.most_common():
    pct = units / max_units * 100
    out += f"""      <div class="row" style="flex-direction:column; align-items:stretch; gap:6px;">
        <div style="display:flex; justify-content:space-between;">
          <span style="color:var(--ink); font-weight:500; font-size:13px;">{e(name)}</span>
          <span class="n">{units} units</span>
        </div>
        <div class="bar"><span class="fill" style="width:{pct:.1f}%; display:block; height:100%;"></span></div>
      </div>
"""

out += f"""    </div>
  </div>
</main>

<footer class="footer">
  <div><span style="color: var(--good);">●</span> Synced from WooCommerce {e(now)} · {len(orders)} orders pulled</div>
  <div>Refresh: <code>cd ~/Downloads/alph4labs_new/preview &amp;&amp; python3 refresh_dashboard.py</code> · then hard-refresh (Cmd+Shift+R)</div>
</footer>

<div id="toast" class="toast"></div>

</body>
""" + r"""
<script>
  function toast(msg, isError) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast show' + (isError ? ' error' : '');
    setTimeout(() => t.classList.remove('show'), 3500);
  }

  async function markCompleted(id) {
    const r = await fetch(`/api/order/${id}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'completed' })
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  document.querySelectorAll('.a.mark').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id, name = btn.dataset.name, total = btn.dataset.total;
      if (!confirm(`Mark order #${id} as completed in WC?\n\n${name} · €${total}\n\nWrites to live alph4labs.com.`)) return;
      const orig = btn.textContent;
      btn.disabled = true; btn.textContent = 'Saving…';
      try {
        await markCompleted(id);
        const span = document.createElement('span');
        span.className = 'done';
        span.textContent = '✓ done';
        btn.replaceWith(span);
        toast(`✓ #${id} marked completed`);
      } catch (err) {
        btn.disabled = false; btn.textContent = orig;
        toast(`Error #${id}: ${err.message}`, true);
      }
    });
  });

  const bulk = document.getElementById('bulk-paid');
  if (bulk) {
    bulk.addEventListener('click', async () => {
      const ids = JSON.parse(bulk.dataset.ids);
      if (!ids.length) { toast('Nothing to mark.'); return; }
      if (!confirm(`Mark all ${ids.length} paid orders as completed in WC?\n\nWrites to live alph4labs.com for each one.`)) return;
      bulk.disabled = true;
      let ok = 0, fail = 0;
      for (const id of ids) {
        bulk.textContent = `Marking ${ok + fail + 1}/${ids.length}…`;
        try {
          await markCompleted(id);
          ok++;
          const indiv = document.querySelector(`#paid-${id} .a.mark`);
          if (indiv) {
            const sp = document.createElement('span');
            sp.className = 'done'; sp.textContent = '✓ done';
            indiv.replaceWith(sp);
          }
        } catch (e) {
          fail++;
          console.error(`Failed #${id}:`, e);
        }
      }
      bulk.textContent = `✓ ${ok} marked${fail ? `, ${fail} failed` : ''}`;
      bulk.disabled = false;
      toast(`✓ ${ok} of ${ids.length} marked completed${fail ? `, ${fail} failed` : ''}`, fail > 0);
    });
  }
</script>

</html>
"""

Path("dashboard.html").write_text(out)
print(f"✓ dashboard rebuilt")
print(f"  {len(active)} active orders · {len(cancelled)} cancelled")
print(f"  PAID:   {len(paid)} orders · {eur(paid_value)}")
print(f"  UNPAID: {len(unpaid)} orders · {eur(unpaid_value)}")
print(f"  Repeat offenders: {len(repeat_offenders)}")
print()
print(f"View: http://localhost:3000/dashboard.html")
