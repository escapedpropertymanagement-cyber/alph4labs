#!/usr/bin/env python3
"""
Pull all paid orders from alph4labs.com WooCommerce API and generate a
single combined PDF with one page per order:
  - Header: order number + status
  - FROM: Alph4 Labs Limited Gibraltar address
  - DELIVER TO: customer billing/shipping details
  - ORDER CONTENTS: line items table
  - Totals + payment confirmation

Output: ~/Downloads/alph4_labs_invoices_<date>.pdf
"""
import json, urllib.request, base64, os, datetime, sys
from pathlib import Path
from collections import defaultdict
import openpyxl
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

CK = os.environ.get("WC_CONSUMER_KEY", "")
CS = os.environ.get("WC_CONSUMER_SECRET", "")
if not CK or not CS:
    sys.exit("ERROR: set WC_CONSUMER_KEY and WC_CONSUMER_SECRET env vars before running.")
TRACKER_XLSX = Path("/Users/leonthick/Downloads/alph4_labs_payment_tracker.xlsx")

SENDER_NAME = "Alph4 Labs Limited"
SENDER_ADDR = ["Unit 9E A R P Shelter 27A", "Engineer Lane", "Gibraltar GX11 1AA"]


def wc_get(path):
    req = urllib.request.Request("https://alph4labs.com/wp-json/wc/v3" + path)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{CK}:{CS}".encode()).decode())
    req.add_header("User-Agent", "alph4-invoices/1.0")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


orders = wc_get("/orders?per_page=100&status=any&orderby=date&order=asc")

# Build tracker_status from spreadsheet if available
tracker_status = {}
if TRACKER_XLSX.exists():
    wb = openpyxl.load_workbook(TRACKER_XLSX, data_only=True)
    ws = wb["All Orders"]
    for row in ws.iter_rows(min_row=5, values_only=True):
        cells = (list(row) + [None]*10)[:10]
        status, oid, *_ = cells
        if oid and status in ("PAID", "UNPAID"):
            tracker_status[int(oid)] = status


def is_paid(o):
    if o["status"] in ("processing", "completed"):
        return True
    if o["status"] == "on-hold" and tracker_status.get(o["id"]) == "PAID":
        return True
    return False


paid = [o for o in orders if is_paid(o)]
print(f"Pulled {len(orders)} orders total, {len(paid)} paid.")


# ── PDF GENERATION ──────────────────────────────────────────────
date_stamp = datetime.date.today().strftime("%Y-%m-%d")
out_path = Path.home() / "Downloads" / f"alph4_labs_invoices_{date_stamp}.pdf"

c = canvas.Canvas(str(out_path), pagesize=A4)
PAGE_W, PAGE_H = A4
LM = 18 * mm  # left margin
RM = PAGE_W - 18 * mm  # right margin
TOP = PAGE_H - 18 * mm

INK = colors.HexColor("#0F172A")
ACC = colors.HexColor("#0F25D5")
GOOD = colors.HexColor("#10B981")
FAINT = colors.HexColor("#64748B")
LINE = colors.HexColor("#CBD5E1")

def page(o):
    y = TOP

    # Header band
    c.setFillColor(INK)
    c.rect(0, PAGE_H - 25*mm, PAGE_W, 25*mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(LM, PAGE_H - 14*mm, "ALPH4 LABS")
    c.setFont("Helvetica", 9)
    c.drawString(LM, PAGE_H - 19*mm, "labs.alph4labs.com  ·  sales@alph4labs.com")
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(RM, PAGE_H - 14*mm, f"ORDER #{o['id']}")
    c.setFont("Helvetica", 9)
    date_str = o['date_created'][:10]
    c.drawRightString(RM, PAGE_H - 19*mm, f"Date: {date_str}")

    y = PAGE_H - 35*mm

    # FROM block
    c.setFillColor(FAINT)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(LM, y, "FROM")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(LM, y - 5*mm, SENDER_NAME)
    c.setFont("Helvetica", 9)
    for i, line in enumerate(SENDER_ADDR):
        c.drawString(LM, y - 10*mm - i*4*mm, line)

    # DELIVER TO block (right side)
    b = o.get("billing") or {}
    s = o.get("shipping") or {}
    # Prefer shipping address if present, else billing
    use = s if (s.get("address_1") or s.get("city")) else b
    name = f"{use.get('first_name','').strip()} {use.get('last_name','').strip()}".strip() or "(no name)"
    addr_lines = [
        use.get("address_1") or "",
        use.get("address_2") or "",
        f"{use.get('postcode','').strip()} {use.get('city','').strip()}".strip(),
        use.get("state") or "",
        use.get("country") or "",
    ]
    addr_lines = [a for a in addr_lines if a]

    c.setFillColor(FAINT)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(LM + 90*mm, y, "DELIVER TO")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(LM + 90*mm, y - 5*mm, name)
    c.setFont("Helvetica", 9.5)
    for i, line in enumerate(addr_lines):
        c.drawString(LM + 90*mm, y - 10*mm - i*4.5*mm, line)
    if b.get("phone"):
        c.setFillColor(FAINT)
        c.drawString(LM + 90*mm, y - 10*mm - len(addr_lines)*4.5*mm, f"Tel: {b.get('phone')}")
    if b.get("email"):
        c.drawString(LM + 90*mm, y - 14*mm - len(addr_lines)*4.5*mm, b.get("email"))

    # Delivery notes (customer_note)
    note = (o.get("customer_note") or "").strip()
    if note:
        y2 = y - 14*mm - max(len(addr_lines), 3)*4.5*mm - 4*mm
        c.setFillColor(FAINT)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(LM, y2, "DELIVERY NOTES")
        c.setFillColor(INK)
        c.setFont("Helvetica-Oblique", 9)
        for i, line in enumerate(note[:240].split("\n")[:3]):
            c.drawString(LM, y2 - 4*mm - i*4*mm, line[:120])

    # Items table
    y_table = PAGE_H - 100*mm
    c.setStrokeColor(LINE)
    c.line(LM, y_table + 8*mm, RM, y_table + 8*mm)
    c.setFillColor(FAINT)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(LM, y_table + 3*mm, "SKU")
    c.drawString(LM + 28*mm, y_table + 3*mm, "PRODUCT")
    c.drawRightString(RM - 50*mm, y_table + 3*mm, "QTY")
    c.drawRightString(RM - 25*mm, y_table + 3*mm, "UNIT PRICE")
    c.drawRightString(RM, y_table + 3*mm, "LINE TOTAL")
    c.line(LM, y_table, RM, y_table)

    c.setFillColor(INK)
    c.setFont("Helvetica", 9.5)
    row_y = y_table - 7*mm
    for li in o.get("line_items", []):
        c.drawString(LM, row_y, (li.get("sku") or "-")[:12])
        c.drawString(LM + 28*mm, row_y, (li.get("name") or "?")[:55])
        c.drawRightString(RM - 50*mm, row_y, str(li.get("quantity", "")))
        unit_price = float(li.get("subtotal", 0)) / max(li.get("quantity", 1) or 1, 1)
        c.drawRightString(RM - 25*mm, row_y, f"€{unit_price:,.2f}")
        c.drawRightString(RM, row_y, f"€{float(li.get('total', 0)):,.2f}")
        row_y -= 6*mm

    # Totals box
    y_tot = row_y - 10*mm
    c.setStrokeColor(LINE)
    c.line(LM + 100*mm, y_tot + 6*mm, RM, y_tot + 6*mm)

    c.setFillColor(FAINT)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(LM + 100*mm, y_tot, "Subtotal")
    c.setFillColor(INK)
    c.setFont("Helvetica", 9.5)
    c.drawRightString(RM, y_tot, f"€{float(o.get('total', 0)) - float(o.get('shipping_total', 0)):,.2f}")
    y_tot -= 5*mm
    if float(o.get("shipping_total", 0)) > 0:
        c.setFillColor(FAINT)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(LM + 100*mm, y_tot, "Shipping")
        c.setFillColor(INK)
        c.setFont("Helvetica", 9.5)
        c.drawRightString(RM, y_tot, f"€{float(o.get('shipping_total', 0)):,.2f}")
        y_tot -= 5*mm

    c.setStrokeColor(INK)
    c.setLineWidth(0.7)
    c.line(LM + 100*mm, y_tot + 3*mm, RM, y_tot + 3*mm)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(LM + 100*mm, y_tot - 3*mm, "ORDER TOTAL")
    c.drawRightString(RM, y_tot - 3*mm, f"€{float(o.get('total', 0)):,.2f}")
    y_tot -= 10*mm

    # PAID badge
    badge_x = LM + 100*mm
    badge_y = y_tot - 5*mm
    c.setFillColor(GOOD)
    c.roundRect(badge_x, badge_y - 2*mm, 30*mm, 7*mm, 3*mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(badge_x + 15*mm, badge_y + 0.5*mm, "✓ PAID")
    c.setFillColor(FAINT)
    c.setFont("Helvetica", 9)
    method = o.get("payment_method_title") or "Bank transfer"
    c.drawString(badge_x + 35*mm, badge_y + 0.5*mm, f"via {method}")

    # Footer
    c.setFillColor(FAINT)
    c.setFont("Helvetica", 7.5)
    c.drawString(LM, 12*mm, f"Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M GMT')} · Alph4 Labs Limited · Gibraltar")
    c.drawRightString(RM, 12*mm, f"Order #{o['id']}")

    c.showPage()


# Sort by date asc so they print in chronological order
paid_sorted = sorted(paid, key=lambda o: o.get("date_created", ""))
for o in paid_sorted:
    page(o)

c.save()
print(f"✓ Wrote {out_path}  ({out_path.stat().st_size:,} bytes, {len(paid_sorted)} pages)")
