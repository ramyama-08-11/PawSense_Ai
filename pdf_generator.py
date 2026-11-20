"""
PAWSENSE AI — Dynamic Clinical PDF Report Generator
Uses ReportLab Platypus to generate professional, multi-page, veterinary-ready
consultation summaries, diagnostic reports, and image showcases.
"""

import os
import re
import html
from io import BytesIO
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as PlatypusImage,
    KeepTogether,
    HRFlowable
)
from reportlab.pdfgen import canvas
from PIL import Image as PILImage


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and display total page count."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        
        # Header rule & title (on pages 2+)
        if self._pageNumber > 1:
            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.setLineWidth(0.5)
            self.line(40, letter[1] - 40, letter[0] - 40, letter[1] - 40)
            self.drawString(40, letter[1] - 34, "PawSense AI — Veterinary Clinical Consultation Summary")
            self.drawRightString(letter[0] - 40, letter[1] - 34, datetime.utcnow().strftime("%Y-%m-%d"))

        # Footer rule & page number
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(40, 42, letter[0] - 40, 42)
        
        self.drawString(40, 30, "PawSense AI • Confidential Pet Healthcare Record • Not a Substitute for In-Person Veterinary Exam")
        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(letter[0] - 40, 30, page_str)
        self.restoreState()


def detect_pdf_intent(text):
    """
    Detects if the user's input expresses intent to generate, export, or convert to a PDF.
    Returns True if PDF intent is detected, False otherwise.
    """
    if not text:
        return False
    t = text.strip()

    # 1. Slash commands: /pdf, /export-pdf, /report
    if re.match(r'^/(?:pdf|export-pdf|report)(?:\s.*)?$', t, re.IGNORECASE):
        return True

    # 2. Must contain 'pdf' somewhere (case-insensitive)
    if not re.search(r'\bpdf\b', t, re.IGNORECASE):
        return False

    # 3. Intent patterns with 'pdf'
    pdf_patterns = [
        r'\b(?:convert|turn|save|format)\b.*\bpdf\b',
        r'\bpdf\b.*\b(?:format|file|report|version|document|download|copy)\b',
        r'\b(?:generate|make|create|build|produce)\b.*\bpdf\b',
        r'\b(?:export|download|give|send|get|provide|show)\b.*\bpdf\b',
        r'\b(?:print|need|want|have)\b.*\bpdf\b',
        r'\bpdf\b.*\b(?:please|pls|now)\b',
        r'\b(?:into|as|to|in)\s+pdf\b',
    ]
    for pat in pdf_patterns:
        if re.search(pat, t, re.IGNORECASE):
            return True

    # If the user literally just says "pdf", "pdf please", "into pdf", "in pdf"
    cleaned = re.sub(r'[^\w\s]', '', t.lower()).split()
    if len(cleaned) <= 6 and 'pdf' in cleaned:
        return True

    return False


def clean_markdown_for_pdf(text):
    """Converts basic markdown formatting (*, **, lists, newlines) into ReportLab Paragraph XML."""
    if not text:
        return ""
    # Escape standard XML characters
    s = html.escape(text.strip())
    
    # Bold **text** -> <b>text</b>
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    # Italic *text* -> <i>\1</i>
    s = re.sub(r'\*([^\*\n]+?)\*', r'<i>\1</i>', s)
    
    # Convert bullet points (- item or * item)
    lines = s.split('\n')
    formatted_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            bullet_body = stripped[2:].strip()
            formatted_lines.append(f"&nbsp;&nbsp;•&nbsp;{bullet_body}")
        elif re.match(r'^\d+\.\s+', stripped):
            formatted_lines.append(f"&nbsp;&nbsp;{stripped}")
        else:
            formatted_lines.append(stripped)
            
    return "<br/>".join(formatted_lines)


def get_safe_image_flowable(image_rel_path, base_folder, max_w=220, max_h=220):
    """Locates an image on disk, resizes proportionally, and returns a Platypus Image flowable."""
    if not image_rel_path:
        return None
        
    fname = image_rel_path.split('/')[-1]
    # Check upload folder or archive folder
    possible_paths = [
        os.path.join(base_folder, fname),
        os.path.join(base_folder, 'uploads', fname),
        os.path.join(base_folder, '..', 'static', 'uploads', fname),
        os.path.abspath(image_rel_path)
    ]
    
    real_path = None
    for p in possible_paths:
        if os.path.isfile(p):
            real_path = p
            break
            
    if not real_path:
        return None
        
    try:
        with PILImage.open(real_path) as im:
            orig_w, orig_h = im.size
            if orig_w == 0 or orig_h == 0:
                return None
            ratio = min(max_w / orig_w, max_h / orig_h, 1.0)
            target_w = orig_w * ratio
            target_h = orig_h * ratio
        return PlatypusImage(real_path, width=target_w, height=target_h)
    except Exception as e:
        print(f"Warning: could not process image {real_path} for PDF: {e}")
        return None


def generate_session_pdf(chat_session, user, upload_folder):
    """
    Builds a complete, multi-page, branded veterinary consultation summary
    for an entire chat session.
    Returns: BytesIO containing the PDF binary.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()
    
    # Custom Typography Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#0f172a')
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#64748b')
    )
    meta_label = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#475569')
    )
    meta_val = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#0f172a')
    )
    user_header_style = ParagraphStyle(
        'UserHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#4338ca')
    )
    model_header_style = ParagraphStyle(
        'ModelHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#059669')
    )
    body_style = ParagraphStyle(
        'BodyMsg',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=14,
        textColor=colors.HexColor('#1e293b')
    )
    caption_style = ParagraphStyle(
        'CaptionStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#64748b')
    )
    disclaimer_style = ParagraphStyle(
        'Disclaimer',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10.5,
        textColor=colors.HexColor('#991b1b')
    )

    story = []

    # 1. Header Banner Table
    header_data = [
        [
            Paragraph("<b>🐾 PAWSENSE AI</b><br/><font size='10' color='#6366f1'>Intelligent Pet Care & Veterinary Consultation Report</font>", title_style),
            Paragraph(f"<b>CONSULTATION SUMMARY</b><br/>Ref: PS-{chat_session.id}-{datetime.utcnow().strftime('%Y%m%d')}<br/>Date: {datetime.utcnow().strftime('%B %d, %Y %H:%M UTC')}", subtitle_style)
        ]
    ]
    header_table = Table(header_data, colWidths=[340, 200])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#4f46e5'), spaceBefore=2, spaceAfter=12))

    # 2. Metadata Box
    owner_name = getattr(user, 'username', 'Pet Parent')
    session_title = chat_session.title or "General Consultation"
    msg_count = len(chat_session.messages)

    meta_table_data = [
        [
            Paragraph("<b>Pet Owner:</b>", meta_label), Paragraph(owner_name, meta_val),
            Paragraph("<b>Session ID:</b>", meta_label), Paragraph(f"#{chat_session.id}", meta_val),
        ],
        [
            Paragraph("<b>Topic:</b>", meta_label), Paragraph(session_title, meta_val),
            Paragraph("<b>Records:</b>", meta_label), Paragraph(f"{msg_count} Message exchanges", meta_val),
        ]
    ]
    meta_table = Table(meta_table_data, colWidths=[70, 200, 70, 200])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#f1f5f9')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    # 3. Chronological Consultation Exchanges
    for idx, msg in enumerate(chat_session.messages):
        is_user = msg.role == 'user'
        time_str = msg.created_at.strftime("%H:%M") if getattr(msg, 'created_at', None) else ""
        
        role_label = f"👤 Pet Owner ({time_str})" if is_user else f"🐾 PawSense AI Expert Consultation ({time_str})"
        role_style = user_header_style if is_user else model_header_style
        box_bg = colors.HexColor('#f0f9ff') if is_user else colors.HexColor('#f0fdf4')
        border_col = colors.HexColor('#bae6fd') if is_user else colors.HexColor('#bbf7d0')

        card_content = []
        card_content.append(Paragraph(role_label, role_style))
        card_content.append(Spacer(1, 4))
        
        # Message text
        formatted_text = clean_markdown_for_pdf(msg.content)
        if formatted_text:
            card_content.append(Paragraph(formatted_text, body_style))
            
        # Check attached or generated images
        img_flowable = None
        if getattr(msg, 'image_path', None):
            img_flowable = get_safe_image_flowable(msg.image_path, upload_folder)
            
        if img_flowable:
            card_content.append(Spacer(1, 6))
            card_content.append(img_flowable)
            if getattr(msg, 'image_caption', None):
                card_content.append(Spacer(1, 3))
                card_content.append(Paragraph(f"<b>Visual Reference:</b> {html.escape(msg.image_caption)}", caption_style))

        # Wrap in a neat table card
        msg_table = Table([[card_content]], colWidths=[540])
        msg_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,0), box_bg),
            ('BOX', (0,0), (0,0), 0.5, border_col),
            ('TOPPADDING', (0,0), (0,0), 8),
            ('BOTTOMPADDING', (0,0), (0,0), 8),
            ('LEFTPADDING', (0,0), (0,0), 10),
            ('RIGHTPADDING', (0,0), (0,0), 10),
        ]))
        
        story.append(msg_table)
        story.append(Spacer(1, 10))

    # 4. Clinical Disclaimer Box
    story.append(Spacer(1, 10))
    disclaimer_text = (
        "<b>⚠️ CLINICAL HANDOVER DISCLAIMER:</b> This summary is compiled by PawSense AI for informational triage, "
        "first-aid orientation, and veterinary communication purposes only. It does not constitute a formal prescription, "
        "veterinary diagnosis, or laboratory examination. If your pet presents acute lethargy, respiratory distress, seizures, "
        "or suspected toxin ingestion, please transport them immediately to a licensed emergency veterinary hospital."
    )
    disclaimer_table = Table([[Paragraph(disclaimer_text, disclaimer_style)]], colWidths=[540])
    disclaimer_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#fef2f2')),
        ('BOX', (0,0), (0,0), 0.75, colors.HexColor('#fca5a5')),
        ('TOPPADDING', (0,0), (0,0), 8),
        ('BOTTOMPADDING', (0,0), (0,0), 8),
        ('LEFTPADDING', (0,0), (0,0), 10),
        ('RIGHTPADDING', (0,0), (0,0), 10),
    ]))
    story.append(KeepTogether(disclaimer_table))

    # Build PDF with multi-page numbered canvas
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer


def generate_single_message_pdf(msg, user, upload_folder):
    """
    Generates a concise 1-page executive clinical summary for a specific AI diagnostic message.
    Works whether an image is attached or not.
    Returns: BytesIO containing the PDF binary.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=50,
        bottomMargin=50
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#0f172a')
    )
    body_style = ParagraphStyle(
        'BodyMsg',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=14.5,
        textColor=colors.HexColor('#1e293b')
    )
    caption_style = ParagraphStyle(
        'Caption',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor('#64748b')
    )
    disclaimer_style = ParagraphStyle(
        'Disc',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10.5,
        textColor=colors.HexColor('#991b1b')
    )

    story = []
    
    # Header
    hdr = [
        [
            Paragraph("<b>🐾 PAWSENSE AI — Clinical Diagnostic Sheet</b>", title_style),
            Paragraph(f"Report ID: PS-M{msg.id}<br/>{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}", caption_style)
        ]
    ]
    t_hdr = Table(hdr, colWidths=[360, 180])
    t_hdr.setStyle(TableStyle([('ALIGN', (1,0), (1,0), 'RIGHT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
    story.append(t_hdr)
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#4f46e5'), spaceBefore=4, spaceAfter=10))

    # Assessment Content
    formatted = clean_markdown_for_pdf(msg.content)
    story.append(Paragraph(formatted, body_style))
    story.append(Spacer(1, 12))

    # Optional Image Attachment
    if getattr(msg, 'image_path', None):
        img_fl = get_safe_image_flowable(msg.image_path, upload_folder, max_w=300, max_h=240)
        if img_fl:
            story.append(img_fl)
            if getattr(msg, 'image_caption', None):
                story.append(Spacer(1, 4))
                story.append(Paragraph(f"<b>Caption:</b> {html.escape(msg.image_caption)}", caption_style))
            story.append(Spacer(1, 12))

    # Disclaimer
    disc = (
        "<b>Clinical Disclaimer:</b> This report is generated by PawSense AI for informational and preliminary triage use. "
        "It does not replace an in-person physical clinical consultation by a certified veterinary doctor."
    )
    t_disc = Table([[Paragraph(disc, disclaimer_style)]], colWidths=[540])
    t_disc.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#fef2f2')),
        ('BOX', (0,0), (0,0), 0.5, colors.HexColor('#fca5a5')),
        ('TOPPADDING', (0,0), (0,0), 6),
        ('BOTTOMPADDING', (0,0), (0,0), 6),
        ('LEFTPADDING', (0,0), (0,0), 8),
        ('RIGHTPADDING', (0,0), (0,0), 8),
    ]))
    story.append(t_disc)

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer

