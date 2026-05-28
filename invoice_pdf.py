"""
Invoice + shipping label PDF generator (single order).

Exposes one function: build_invoice_pdf(order: dict) -> bytes
Used by server.py to serve /api/invoice/<id>.pdf and by
generate_invoices.py for batch backfills.
"""
import datetime, io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas

SENDER_NAME = "Alph4 Labs Limited"
SENDER_ADDR = ["Unit 9E A R P Shelter 27A", "Engineer Lane", "Gibraltar GX11 1AA"]
INK   = colors.HexColor("#0F172A")
ACC   = colors.HexColor("#0F25D5")
GOOD  = colors.HexColor("#10B981")
FAINT = colors.HexColor("#64748B")
LINE  = colors.HexColor("#CBD5E1")


def build_invoice_pdf(o: dict) -> bytes:
    """Generate a single-order invoice + shipping label PDF, return as bytes."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    PAGE_W, PAGE_H = A4
    LM = 18 * mm
    RM = PAGE_W - 18 * mm

    # Header band
    c.setFillColor(INK)
    c.rect(0, PAGE_H - 25*mm, PAGE_W, 25*mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(LM, PAGE_H - 14*mm, "ALPH4 LABS")
    c.setFont("Helvetica", 9)
    c.drawString(LM, PAGE_H - 19*mm, "alph4labs.com  ·  sales@alph4labs.com")
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(RM, PAGE_H - 14*mm, f"ORDER #{o['id']}")
    c.setFont("Helvetica", 9)
    c.drawRightString(RM, PAGE_H - 19*mm, f"Date: {o['date_created'][:10]}")

    y = PAGE_H - 35*mm

    # FROM block
    c.setFillColor(FAINT); c.setFont("Helvetica-Bold", 8)
    c.drawString(LM, y, "FROM")
    c.setFillColor(INK); c.setFont("Helvetica-Bold", 10)
    c.drawString(LM, y - 5*mm, SENDER_NAME)
    c.setFont("Helvetica", 9)
    for i, line in enumerate(SENDER_ADDR):
        c.drawString(LM, y - 10*mm - i*4*mm, line)

    # DELIVER TO block
    b = o.get("billing") or {}
    s = o.get("shipping") or {}
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

    c.setFillColor(FAINT); c.setFont("Helvetica-Bold", 8)
    c.drawString(LM + 90*mm, y, "DELIVER TO")
    c.setFillColor(INK); c.setFont("Helvetica-Bold", 11)
    c.drawString(LM + 90*mm, y - 5*mm, name)
    c.setFont("Helvetica", 9.5)
    for i, line in enumerate(addr_lines):
        c.drawString(LM + 90*mm, y - 10*mm - i*4.5*mm, line)
    extra_y = y - 10*mm - len(addr_lines)*4.5*mm
    if b.get("phone"):
        c.setFillColor(FAINT)
        c.drawString(LM + 90*mm, extra_y, f"Tel: {b.get('phone')}")
        extra_y -= 4*mm
    if b.get("email"):
        c.drawString(LM + 90*mm, extra_y, b.get("email"))

    # Delivery notes
    note = (o.get("customer_note") or "").strip()
    if note:
        y2 = y - 30*mm
        c.setFillColor(FAINT); c.setFont("Helvetica-Bold", 8)
        c.drawString(LM, y2, "DELIVERY NOTES")
        c.setFillColor(INK); c.setFont("Helvetica-Oblique", 9)
        for i, line in enumerate(note[:240].split("\n")[:3]):
            c.drawString(LM, y2 - 4*mm - i*4*mm, line[:120])

    # Items table
    y_table = PAGE_H - 100*mm
    c.setStrokeColor(LINE)
    c.line(LM, y_table + 8*mm, RM, y_table + 8*mm)
    c.setFillColor(FAINT); c.setFont("Helvetica-Bold", 8)
    c.drawString(LM, y_table + 3*mm, "SKU")
    c.drawString(LM + 28*mm, y_table + 3*mm, "PRODUCT")
    c.drawRightString(RM - 50*mm, y_table + 3*mm, "QTY")
    c.drawRightString(RM - 25*mm, y_table + 3*mm, "UNIT PRICE")
    c.drawRightString(RM, y_table + 3*mm, "LINE TOTAL")
    c.line(LM, y_table, RM, y_table)

    c.setFillColor(INK); c.setFont("Helvetica", 9.5)
    row_y = y_table - 7*mm
    for li in o.get("line_items", []):
        c.drawString(LM, row_y, (li.get("sku") or "-")[:12])
        c.drawString(LM + 28*mm, row_y, (li.get("name") or "?")[:55])
        c.drawRightString(RM - 50*mm, row_y, str(li.get("quantity", "")))
        qty = max(li.get("quantity", 1) or 1, 1)
        unit_price = float(li.get("subtotal", 0)) / qty
        c.drawRightString(RM - 25*mm, row_y, f"€{unit_price:,.2f}")
        c.drawRightString(RM, row_y, f"€{float(li.get('total', 0)):,.2f}")
        row_y -= 6*mm

    # Totals
    y_tot = row_y - 10*mm
    c.setStrokeColor(LINE)
    c.line(LM + 100*mm, y_tot + 6*mm, RM, y_tot + 6*mm)
    c.setFillColor(FAINT); c.setFont("Helvetica-Bold", 9)
    c.drawString(LM + 100*mm, y_tot, "Subtotal")
    c.setFillColor(INK); c.setFont("Helvetica", 9.5)
    c.drawRightString(RM, y_tot, f"€{float(o.get('total', 0)) - float(o.get('shipping_total', 0)):,.2f}")
    y_tot -= 5*mm
    if float(o.get("shipping_total", 0)) > 0:
        c.setFillColor(FAINT); c.setFont("Helvetica-Bold", 9)
        c.drawString(LM + 100*mm, y_tot, "Shipping")
        c.setFillColor(INK); c.setFont("Helvetica", 9.5)
        c.drawRightString(RM, y_tot, f"€{float(o.get('shipping_total', 0)):,.2f}")
        y_tot -= 5*mm

    c.setStrokeColor(INK); c.setLineWidth(0.7)
    c.line(LM + 100*mm, y_tot + 3*mm, RM, y_tot + 3*mm)
    c.setFillColor(INK); c.setFont("Helvetica-Bold", 12)
    c.drawString(LM + 100*mm, y_tot - 3*mm, "ORDER TOTAL")
    c.drawRightString(RM, y_tot - 3*mm, f"€{float(o.get('total', 0)):,.2f}")
    y_tot -= 10*mm

    # Status badge
    status = o.get("status", "")
    is_paid_status = status in ("processing", "completed", "on-hold")
    badge_color = GOOD if status in ("processing", "completed") else (ACC if status == "on-hold" else FAINT)
    badge_text = "✓ PAID" if status in ("processing", "completed") else ("AWAITING PAYMENT" if status == "on-hold" else status.upper())
    badge_x = LM + 100*mm; badge_y = y_tot - 5*mm
    c.setFillColor(badge_color)
    c.roundRect(badge_x, badge_y - 2*mm, 38*mm, 7*mm, 3*mm, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(badge_x + 19*mm, badge_y + 0.5*mm, badge_text)
    c.setFillColor(FAINT); c.setFont("Helvetica", 9)
    method = o.get("payment_method_title") or "Bank transfer"
    c.drawString(badge_x + 42*mm, badge_y + 0.5*mm, f"via {method}")

    # Footer
    c.setFillColor(FAINT); c.setFont("Helvetica", 7.5)
    c.drawString(LM, 12*mm, f"Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M GMT')} · Alph4 Labs Limited · Gibraltar")
    c.drawRightString(RM, 12*mm, f"Order #{o['id']}")

    c.showPage()
    c.save()
    return buf.getvalue()
