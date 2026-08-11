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
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
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
    # Real 80mm thermal roll dimensions, not a shrunk-down A4 page --
    # ~3mm margins each side is standard for most thermal printers,
    # leaving ~70mm of real usable width. Height is computed from the
    # actual content below (this sale's real item count), never a
    # fixed page size: a receipt is a continuous roll that gets cut
    # after printing, not a sheet with a predetermined height.
    page_width = 80 * mm
    margin = 3 * mm
    usable_width = page_width - 2 * margin
    estimated_height = (
        55 * mm  # logo + business info + meta block
        + len(sale.items) * 11 * mm  # two-line item layout per line
        + (2 + len(sale.payments)) * 5 * mm  # subtotal/discount/total + each payment line
        + 20 * mm  # footer + breathing room
    )
    page_height = max(estimated_height, 90 * mm)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=(page_width, page_height),
        title=f"Receipt {sale.id}",
        topMargin=margin,
        bottomMargin=margin,
        leftMargin=margin,
        rightMargin=margin,
    )

    business_style = ParagraphStyle(
        "Business", fontName="Helvetica-Bold", fontSize=11, spaceAfter=2, alignment=1
    )
    contact_style = ParagraphStyle(
        "Contact",
        fontName="Helvetica",
        fontSize=7,
        textColor=colors.HexColor("#475569"),
        alignment=1,
    )
    meta_style = ParagraphStyle("Meta", fontName="Helvetica", fontSize=7.5)
    item_name_style = ParagraphStyle("ItemName", fontName="Helvetica-Bold", fontSize=8)
    footer_style = ParagraphStyle(
        "Footer",
        fontName="Helvetica-Oblique",
        fontSize=6.5,
        textColor=colors.HexColor("#6B7280"),
        spaceBefore=10,
        alignment=1,
    )

    def money(value: float) -> str:
        return f"{currency} {value:,.2f}"

    elements: list[Flowable] = []

    logo = _decode_logo(logo_url)
    if logo is not None:
        # Rescaled to fit the actual 70mm usable width -- the old
        # fixed 2cm height was sized for a full A4 page, not an 80mm
        # roll, and would have overrun the receipt's own margins.
        logo.drawHeight = 1 * cm
        logo.drawWidth = logo.drawHeight * (logo.imageWidth / logo.imageHeight)
        max_logo_width = usable_width * 0.6
        if logo.drawWidth > max_logo_width:
            scale = max_logo_width / logo.drawWidth
            logo.drawWidth *= scale
            logo.drawHeight *= scale
        logo.hAlign = "CENTER"
        elements.append(logo)
        elements.append(Spacer(1, 4))
    elements.append(Paragraph(business_name, business_style))
    if business_address:
        elements.append(Paragraph(business_address, contact_style))
    if business_phone:
        elements.append(Paragraph(business_phone, contact_style))
    if tax_id:
        elements.append(Paragraph(f"Tax ID: {tax_id}", contact_style))
    if header_text:
        elements.append(Paragraph(header_text, contact_style))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph(f"Receipt #{sale.id}", item_name_style))
    try:
        tz = ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - a bad timezone name must never break the receipt
        tz = ZoneInfo("UTC")
    local_time = sale.created_at.replace(tzinfo=UTC).astimezone(tz)
    elements.append(Paragraph(f"{local_time.strftime('%d %b %Y, %H:%M')} ({timezone})", meta_style))
    elements.append(Paragraph(f"Served by {cashier_name}", meta_style))
    if customer_name:
        elements.append(Paragraph(f"Customer: {customer_name}", meta_style))
    elements.append(Spacer(1, 8))

    # The standard thermal-receipt item layout: the product name gets
    # its own full-width line (real product names routinely don't fit
    # in a narrow column), with quantity/price/line-total on the line
    # directly below it -- not the old four-column table, which
    # assumed A4's width and would have overflowed an 80mm roll.
    for item in sale.items:
        elements.append(Paragraph(item.product.name, item_name_style))
        detail_row = Table(
            [[f"{item.quantity} x {money(item.unit_price)}", money(item.line_total)]],
            colWidths=[usable_width * 0.6, usable_width * 0.4],
        )
        detail_row.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#334155")),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(detail_row)
    elements.append(Spacer(1, 6))

    totals_rows = [["Subtotal", money(sale.subtotal)]]
    if sale.discount_amount > 0:
        totals_rows.append(["Discount", f"-{money(sale.discount_amount)}"])
    totals_rows.append(["Total", money(sale.total_amount)])
    for payment in sale.payments:
        label = payment.method.value.replace("_", " ").title()
        totals_rows.append([f"Paid ({label})", money(payment.amount)])

    total_row_index = -len(sale.payments) - 1
    totals_table = Table(totals_rows, colWidths=[usable_width * 0.6, usable_width * 0.4])
    totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("FONTNAME", (0, total_row_index), (-1, total_row_index), "Helvetica-Bold"),
                (
                    "LINEABOVE",
                    (0, total_row_index),
                    (-1, total_row_index),
                    0.5,
                    colors.HexColor("#0F172A"),
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
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
