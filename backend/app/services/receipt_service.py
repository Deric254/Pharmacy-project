"""
Branded sale receipts.

Generated fresh from the real sale and business config on every
request -- never cached, never pre-rendered and stored. This is
deliberate: a receipt retrieved a year from now must reflect the
actual historical sale record (which itself never changes after the
fact -- see Sale's own docstring), and the business's current logo and
branding, not whatever happened to be true the moment it was first
printed. There is no separate "receipt" table to go stale or drift
from the real sale.
"""

import base64
import io
from datetime import UTC
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from app.models.sale import Sale


def _decode_logo(logo_url: str | None) -> Image | None:
    """
    logo_url is a data: URI (base64-encoded image), never a remote
    link -- see BusinessConfig's own docstring for why. Malformed data
    here must never break receipt generation entirely; a receipt
    without a logo is still useful, a 500 error isn't.

    Genuinely validates the image is decodable here, at this point --
    reportlab/PIL don't actually load pixel data until render time
    deep inside doc.build(), which is too late to safely catch.
    """
    if not logo_url or not logo_url.startswith("data:"):
        return None
    try:
        _, b64_data = logo_url.split(",", 1)
        image_bytes = base64.b64decode(b64_data)

        from PIL import Image as PILImage

        pil_img = PILImage.open(io.BytesIO(image_bytes))
        pil_img.load()  # force full decode now, not lazily at render time

        img = Image(io.BytesIO(image_bytes))
        img.drawHeight = 2 * cm
        img.drawWidth = img.drawHeight * (img.imageWidth / img.imageHeight)
        return img
    except Exception:  # noqa: BLE001 - a bad logo must never break the receipt itself
        return None


def generate_receipt_pdf(
    sale: Sale,
    cashier_name: str,
    customer_name: str | None,
    business_name: str,
    business_address: str | None,
    business_phone: str | None,
    logo_url: str | None,
    currency: str,
    tax_id: str | None = None,
    header_text: str | None = None,
    footer_text: str | None = None,
    timezone: str = "UTC",
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=f"Receipt {sale.id}",
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    business_style = ParagraphStyle(
        "Business", fontName="Helvetica-Bold", fontSize=14, spaceAfter=2
    )
    contact_style = ParagraphStyle(
        "Contact", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#475569")
    )
    meta_style = ParagraphStyle(
        "Meta", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#475569")
    )
    footer_style = ParagraphStyle(
        "Footer",
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=colors.HexColor("#6B7280"),
        spaceBefore=20,
    )

    def money(value: float) -> str:
        return f"{currency} {value:,.2f}"

    elements: list[Flowable] = []

    logo = _decode_logo(logo_url)
    if logo is not None:
        elements.append(logo)
        elements.append(Spacer(1, 6))
    elements.append(Paragraph(business_name, business_style))
    if business_address:
        elements.append(Paragraph(business_address, contact_style))
    if business_phone:
        elements.append(Paragraph(business_phone, contact_style))
    if tax_id:
        elements.append(Paragraph(f"Tax ID: {tax_id}", contact_style))
    if header_text:
        elements.append(Paragraph(header_text, contact_style))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph(f"Receipt #{sale.id}", business_style))
    try:
        tz = ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - a bad timezone name must never break the receipt
        tz = ZoneInfo("UTC")
    local_time = sale.created_at.replace(tzinfo=UTC).astimezone(tz)
    elements.append(Paragraph(f"{local_time.strftime('%d %b %Y, %H:%M')} ({timezone})", meta_style))
    elements.append(Paragraph(f"Served by {cashier_name}", meta_style))
    if customer_name:
        elements.append(Paragraph(f"Customer: {customer_name}", meta_style))
    elements.append(Spacer(1, 12))

    item_rows: list[list[str]] = [["Item", "Qty", "Price", "Total"]]
    for item in sale.items:
        item_rows.append(
            [
                item.product.name,
                str(item.quantity),
                money(item.unit_price),
                money(item.line_total),
            ]
        )
    item_table = Table(item_rows, colWidths=[8 * cm, 2 * cm, 3 * cm, 3 * cm])
    item_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#0F172A")),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    elements.append(item_table)
    elements.append(Spacer(1, 8))

    totals_rows = [["Subtotal", money(sale.subtotal)]]
    if sale.discount_amount > 0:
        totals_rows.append(["Discount", f"-{money(sale.discount_amount)}"])
    totals_rows.append(["Total", money(sale.total_amount)])
    for payment in sale.payments:
        label = payment.method.value.replace("_", " ").title()
        totals_rows.append([f"Paid ({label})", money(payment.amount)])

    total_row_index = -len(sale.payments) - 1
    totals_table = Table(totals_rows, colWidths=[13 * cm, 3 * cm])
    totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, total_row_index), (-1, total_row_index), "Helvetica-Bold"),
                (
                    "LINEABOVE",
                    (0, total_row_index),
                    (-1, total_row_index),
                    0.5,
                    colors.HexColor("#0F172A"),
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(totals_table)

    elements.append(
        Paragraph(
            footer_text
            or (
                "Thank you for your business. This receipt reflects the exact sale record "
                "and can be retrieved again at any time."
            ),
            footer_style,
        )
    )

    doc.build(elements)
    return buffer.getvalue()
