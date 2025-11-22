"""
PDF Generation Utilities for Contia365
Generates ledger reports and other PDF documents
"""

from datetime import datetime
from typing import List, Dict, Any
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
)
from reportlab.lib.units import inch


def generate_ledger_pdf(ledger_entries: List[Dict[str, Any]], user_info: Dict[str, Any],
                       filters: Dict[str, Any] = None) -> BytesIO:
    """
    Generate a comprehensive ledger report PDF

    Args:
        ledger_entries: List of ledger entry dictionaries
        user_info: User/organization information
        filters: Optional filters applied (date range, type, etc.)

    Returns:
        BytesIO: PDF file in memory
    """
    # Create PDF in memory
    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    elements = []
    styles = getSampleStyleSheet()

    # Add custom styles
    styles.add(ParagraphStyle(
        name='RightAlign',
        parent=styles['Normal'],
        alignment=TA_RIGHT
    ))
    styles.add(ParagraphStyle(
        name='CenterAlign',
        parent=styles['Normal'],
        alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        name='Bold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold'
    ))
    styles.add(ParagraphStyle(
        name='LedgerTitle',
        parent=styles['Title'],
        fontSize=24,
        textColor=colors.HexColor("#003366"),
        spaceAfter=12,
        alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        name='SectionHeader',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.HexColor("#003366"),
        fontName='Helvetica-Bold',
        spaceAfter=6
    ))

    # Title
    elements.append(Paragraph("LEDGER REPORT", styles['LedgerTitle']))
    elements.append(Spacer(1, 12))

    # User/Organization Information
    user_id = user_info.get('user_id', 'N/A')
    org_id = user_info.get('organization_id', user_id)

    info_text = f"<b>Organization ID:</b> {org_id}<br/>"
    info_text += f"<b>Report Generated:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>"

    if filters:
        if filters.get('from_date'):
            info_text += f"<b>From Date:</b> {filters['from_date']}<br/>"
        if filters.get('to_date'):
            info_text += f"<b>To Date:</b> {filters['to_date']}<br/>"
        if filters.get('entry_type') and filters['entry_type'] != 'all':
            info_text += f"<b>Type Filter:</b> {filters['entry_type']}<br/>"

    elements.append(Paragraph(info_text, styles['Normal']))
    elements.append(Spacer(1, 20))

    # Summary Statistics
    total_entries = len(ledger_entries)
    bank_count = sum(1 for e in ledger_entries if e.get('data_type') == 'bank_transaction')
    ocr_count = total_entries - bank_count

    summary_text = f"<b>Total Entries:</b> {total_entries} | "
    summary_text += f"<b>Bank Transactions:</b> {bank_count} | "
    summary_text += f"<b>OCR Entries:</b> {ocr_count}"

    elements.append(Paragraph(summary_text, styles['SectionHeader']))
    elements.append(Spacer(1, 12))

    if not ledger_entries:
        elements.append(Paragraph("No ledger entries found for the specified criteria.", styles['Normal']))
    else:
        # Create custom header style with white text
        header_style = ParagraphStyle(
            name='TableHeader',
            parent=styles['Bold'],
            textColor=colors.white,
            fontSize=10,
            alignment=TA_CENTER
        )

        # Create detailed ledger table
        table_data = [[
            Paragraph('<b>Date</b>', header_style),
            Paragraph('<b>Type</b>', header_style),
            Paragraph('<b>Invoice #</b>', header_style),
            Paragraph('<b>Supplier/Account</b>', header_style),
            Paragraph('<b>Description</b>', header_style),
            Paragraph('<b>Amount</b>', header_style),
            Paragraph('<b>VAT</b>', header_style),
            Paragraph('<b>Total</b>', header_style)
        ]]

        total_amount = 0
        total_vat = 0
        total_with_tax = 0

        for entry in ledger_entries:
            invoice_data = entry.get('invoice_data', {})
            data_type = entry.get('data_type', 'unknown')

            # Extract common fields
            invoice_info = invoice_data.get('invoice', {})
            invoice_date = invoice_info.get('invoice_date', entry.get('created_at', 'N/A'))
            invoice_number = invoice_info.get('invoice_number') or 'N/A'

            # Handle different entry types
            if data_type == 'bank_transaction':
                # Bank transaction
                account_info = invoice_data.get('account', {})
                supplier_text = f"{account_info.get('account_code', 'N/A')}"
                account_name = account_info.get('account_name', '')
                if account_name:
                    supplier_text += f"<br/><font size=8>{account_name}</font>"

                description = entry.get('ocr_text', 'Bank Transaction')
                entry_type = "Bank"

                totals = invoice_data.get('totals', {})
                amount = totals.get('total', 0)
                vat_amount = 0
                total = amount
                running_balance = totals.get('running_balance', 0)

                # Add running balance to description
                description += f"<br/><font size=8>Balance: {running_balance:,.2f}</font>"

            else:
                # OCR/Toon entry
                supplier_info = invoice_data.get('supplier', {})
                supplier_text = supplier_info.get('business_name', 'N/A')

                customer_info = invoice_data.get('customer', {})
                customer_name = customer_info.get('company_name', '')
                if customer_name:
                    supplier_text += f"<br/><font size=8>To: {customer_name}</font>"

                # Get items description
                items = invoice_data.get('items', [])
                if items:
                    first_item = items[0]
                    description = first_item.get('description', 'N/A')
                    if len(items) > 1:
                        description += f"<br/><font size=8>+{len(items)-1} more items</font>"
                else:
                    description = entry.get('ocr_text', 'N/A')[:100]

                entry_type = data_type.upper() if data_type else "OCR"

                totals = invoice_data.get('totals', {})
                amount = totals.get('total', 0)
                vat_amount = totals.get('VAT_amount', 0)
                total = totals.get('Total_with_Tax', amount)

            # Format date
            try:
                if isinstance(invoice_date, str):
                    # Try to parse and format
                    date_str = invoice_date.split()[0] if ' ' in invoice_date else invoice_date
                    if len(date_str) > 10:
                        date_str = date_str[:10]
                else:
                    date_str = str(invoice_date)
            except:
                date_str = str(invoice_date)[:10]

            # Add row to table
            table_data.append([
                Paragraph(f'<font size=8>{date_str}</font>', styles['Normal']),
                Paragraph(f'<font size=8>{entry_type}</font>', styles['Normal']),
                Paragraph(f'<font size=8>{invoice_number}</font>', styles['Normal']),
                Paragraph(f'<font size=8>{supplier_text}</font>', styles['Normal']),
                Paragraph(f'<font size=8>{description}</font>', styles['Normal']),
                Paragraph(f'<font size=8>{amount:,.2f}</font>', styles['RightAlign']),
                Paragraph(f'<font size=8>{vat_amount:,.2f}</font>', styles['RightAlign']),
                Paragraph(f'<font size=8>{total:,.2f}</font>', styles['RightAlign'])
            ])

            # Accumulate totals
            total_amount += amount
            total_vat += vat_amount
            total_with_tax += total

        # Add totals row
        table_data.append([
            Paragraph('<b>TOTALS</b>', styles['Bold']),
            '', '', '', '',
            Paragraph(f'<b>{total_amount:,.2f}</b>', styles['RightAlign']),
            Paragraph(f'<b>{total_vat:,.2f}</b>', styles['RightAlign']),
            Paragraph(f'<b>{total_with_tax:,.2f}</b>', styles['RightAlign'])
        ])

        # Create table
        table = Table(
            table_data,
            colWidths=[60, 40, 70, 100, 120, 60, 50, 60],
            repeatRows=1
        )

        # Style the table with vibrant header
        table.setStyle(TableStyle([
            # Header row - Modern gradient-like effect with brighter color
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e5a96")),  # Brighter blue
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),  # Larger font
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, 0), 10),  # More padding
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('LEFTPADDING', (0, 0), (-1, 0), 8),
            ('RIGHTPADDING', (0, 0), (-1, 0), 8),

            # Data rows
            ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -2), 9),  # Slightly larger
            ('ALIGN', (5, 1), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 1), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 1), (-1, -2), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -2), 6),

            # Alternating row colors with better contrast
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor("#f5f5f5")]),

            # Totals row - Match header color
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e8f4f8")),  # Light blue tint
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor("#1e5a96")),  # Dark blue text
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 10),
            ('TOPPADDING', (0, -1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 8),

            # Grid with subtle lines
            ('LINEBELOW', (0, 0), (-1, 0), 2, colors.HexColor("#1e5a96")),  # Thick line under header
            ('GRID', (0, 1), (-1, -2), 0.5, colors.HexColor("#d0d0d0")),  # Lighter grid
            ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor("#1e5a96")),  # Line above totals
            ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor("#1e5a96")),  # Border
        ]))

        elements.append(table)

    # Footer
    elements.append(Spacer(1, 20))
    footer_text = f"<i>Generated by Contia365 on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
    elements.append(Paragraph(footer_text, styles['CenterAlign']))

    # Build PDF
    pdf.build(elements)

    # Get PDF from buffer
    buffer.seek(0)
    return buffer
