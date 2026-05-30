from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    ListFlowable,
    ListItem,
)
from models.proposal_whatsapp import acceptance_button_label, create_acceptance_link


BLUE = colors.HexColor('#2563EB')
LIGHT_BLUE = colors.HexColor('#EFF6FF')
TEXT = colors.HexColor('#111827')
MUTED = colors.HexColor('#6B7280')
BORDER = colors.HexColor('#E5E7EB')
SOFT = colors.HexColor('#F8FAFC')


def build_proposal_pdf(proposal, project_types):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=proposal['title'],
    )
    styles = _styles()
    labels = _labels(proposal.get('proposal_language', 'en'))
    whatsapp_link = proposal.get('whatsapp_acceptance_link') or create_acceptance_link(proposal)
    story = []

    story.append(_header(proposal, styles, labels))
    story.append(Spacer(1, 18))
    story.append(Paragraph(project_types.get(proposal['project_type'], proposal['project_type']).upper(), styles['eyebrow']))
    story.append(Paragraph(proposal['title'], styles['title']))
    story.append(Paragraph(proposal.get('project_description') or '', styles['body']))
    story.append(Spacer(1, 16))

    story.append(_section(labels['client_information'], styles))
    story.append(_info_table([
        (labels['client'], proposal['client_name']),
        (labels['business'], proposal.get('business_name') or '-'),
        ('Email', proposal['email']),
        (labels['phone'], proposal.get('phone') or '-'),
    ], styles))

    story.append(_section(labels['needs_objective'], styles))
    story.append(_two_col(
        (labels['client_needs'], proposal.get('client_needs') or '-'),
        (labels['main_objective'], proposal.get('project_objective') or '-'),
        styles,
    ))

    story.append(_section(labels['selected_services'], styles))
    for service in proposal['selected_services']:
        story.append(_service_row(service, styles))

    story.append(_section(labels['pricing'], styles))
    story.append(_pricing_table(proposal, styles, labels))
    story.append(Paragraph(f"<b>{labels['payment_schedule']}:</b> {proposal.get('payment_schedule') or '-'}", styles['body']))
    story.append(Paragraph(f"<b>{labels['payment_methods']}:</b> {proposal.get('payment_methods') or '-'}", styles['body']))
    story.append(Paragraph(f"<b>{labels['payment_terms']}:</b> {proposal.get('payment_terms') or '-'}", styles['body']))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<b>{acceptance_button_label(proposal.get('proposal_language', 'en'))}:</b><br/>{whatsapp_link}", styles['body']))

    story.append(_section(labels['timeline'], styles))
    story.append(_info_table([
        (labels['estimated_start'], proposal.get('start_date') or 'TBD'),
        (labels['estimated_delivery'], proposal.get('estimated_delivery_date') or 'TBD'),
        (labels['business_days'], proposal.get('business_days') or 'TBD'),
    ], styles))
    if proposal.get('milestones'):
        story.append(ListFlowable(
            [ListItem(Paragraph(item, styles['body']), bulletColor=BLUE) for item in proposal['milestones']],
            bulletType='bullet',
            leftIndent=14,
        ))

    story.append(_section(labels['terms'], styles))
    story.append(ListFlowable(
        [ListItem(Paragraph(term, styles['body']), bulletColor=BLUE) for term in proposal['terms_and_conditions']],
        bulletType='bullet',
        leftIndent=14,
    ))

    story.append(_section(labels['approval'], styles))
    story.append(Paragraph(
        labels['approval_text'],
        styles['body'],
    ))
    story.append(Spacer(1, 28))
    story.append(Table(
        [[labels['client_signature'], 'Papia Technology Solutions LLC / date']],
        colWidths=[3.3 * inch, 3.3 * inch],
        style=[
            ('LINEABOVE', (0, 0), (-1, 0), 1, colors.HexColor('#9CA3AF')),
            ('TEXTCOLOR', (0, 0), (-1, 0), MUTED),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
        ],
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer


class ProposalPdfService:
    def generate(self, proposal, project_types):
        return build_proposal_pdf(proposal, project_types)


proposalPdfService = ProposalPdfService()


def _styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle('proposalTitle', parent=base['Title'], fontName='Helvetica-Bold', fontSize=26, leading=30, textColor=TEXT, spaceAfter=8))
    base.add(ParagraphStyle('proposalSection', parent=base['Heading2'], fontName='Helvetica-Bold', fontSize=13, leading=16, textColor=TEXT, spaceBefore=16, spaceAfter=8))
    base.add(ParagraphStyle('proposalBody', parent=base['BodyText'], fontName='Helvetica', fontSize=9.5, leading=13, textColor=colors.HexColor('#374151'), spaceAfter=6))
    base.add(ParagraphStyle('proposalMuted', parent=base['BodyText'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=MUTED))
    base.add(ParagraphStyle('proposalEyebrow', parent=base['BodyText'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=BLUE, spaceAfter=3))
    return {
        'title': base['proposalTitle'],
        'section': base['proposalSection'],
        'body': base['proposalBody'],
        'muted': base['proposalMuted'],
        'eyebrow': base['proposalEyebrow'],
    }


def _header(proposal, styles, labels):
    logo = Table(
        [['PT']],
        colWidths=[0.45 * inch],
        rowHeights=[0.45 * inch],
        style=[
            ('BACKGROUND', (0, 0), (-1, -1), BLUE),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ],
    )
    left = [logo, Paragraph('<b>Papia Technology Solutions LLC</b>', styles['body']), Paragraph('Professional technology proposals and CRM solutions.', styles['muted'])]
    right = [Paragraph(f"<b>{labels['proposal']}</b>", styles['body']), Paragraph(f"#{proposal['id']}<br/>{proposal['created_at'][:10]}<br/>Status: {proposal['status'].title()}", styles['muted'])]
    return Table(
        [[left, right]],
        colWidths=[4.4 * inch, 2.2 * inch],
        style=[
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('LINEBELOW', (0, 0), (-1, -1), 1.5, BLUE),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ],
    )


def _section(title, styles):
    return Paragraph(title, styles['section'])


def _info_table(items, styles):
    rows = []
    for i in range(0, len(items), 2):
        row = []
        for label, value in items[i:i + 2]:
            row.append(Paragraph(f"<font color='#6B7280' size='7'>{label.upper()}</font><br/><b>{value}</b>", styles['body']))
        while len(row) < 2:
            row.append('')
        rows.append(row)
    return Table(rows, colWidths=[3.25 * inch, 3.25 * inch], style=_box_style())


def _two_col(left, right, styles):
    return Table(
        [[
            Paragraph(f"<b>{left[0]}</b><br/>{left[1]}", styles['body']),
            Paragraph(f"<b>{right[0]}</b><br/>{right[1]}", styles['body']),
        ]],
        colWidths=[3.25 * inch, 3.25 * inch],
        style=_box_style(),
    )


def _service_row(service, styles):
    return Table(
        [[
            Paragraph(f"<b>{service.get('name')}</b><br/>{service.get('description') or ''}<br/><font color='#6B7280'>{service.get('notes') or ''}</font>", styles['body']),
            Paragraph(f"<font color='#6B7280'>{service.get('deliveryTime') or 'TBD'}</font><br/><b>${float(service.get('price') or 0):,.2f}</b>", styles['body']),
        ]],
        colWidths=[5.1 * inch, 1.4 * inch],
        style=[
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ],
    )


def _pricing_table(proposal, styles, labels):
    rows = [
        ['Subtotal', f"${proposal['subtotal']:,.2f}"],
        ['Discount', f"-${proposal['discount']:,.2f}"],
        ['Taxes', f"${proposal['taxes']:,.2f}"],
        [labels['total_project_cost'], f"${proposal['total']:,.2f}"],
        [labels['initial_payment'], f"${proposal['initial_payment']:,.2f}"],
        [labels['remaining_balance'], f"${proposal['remaining_balance']:,.2f}"],
    ]
    return Table(
        rows,
        colWidths=[4.9 * inch, 1.6 * inch],
        style=[
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
            ('BACKGROUND', (0, 0), (-1, -1), SOFT),
            ('BACKGROUND', (0, 3), (-1, 3), LIGHT_BLUE),
            ('TEXTCOLOR', (0, 3), (-1, 3), BLUE),
            ('FONTNAME', (0, 3), (-1, 3), 'Helvetica-Bold'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTSIZE', (0, 0), (-1, -1), 9.5),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ],
    )


def _box_style():
    return [
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('BACKGROUND', (0, 0), (-1, -1), SOFT),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]


def _labels(language):
    if language == 'es':
        return {
            'proposal': 'Propuesta',
            'client_information': 'Información del cliente',
            'needs_objective': 'Necesidades y objetivo',
            'selected_services': 'Servicios seleccionados',
            'pricing': 'Precio',
            'total_project_cost': 'Costo total del proyecto',
            'initial_payment': 'Pago inicial',
            'remaining_balance': 'Balance restante',
            'timeline': 'Cronograma',
            'terms': 'Términos y condiciones',
            'approval': 'Aprobación del cliente',
            'client': 'Cliente',
            'business': 'Negocio',
            'phone': 'Teléfono',
            'client_needs': 'Necesidades / problema del negocio',
            'main_objective': 'Objetivo principal',
            'payment_schedule': 'Calendario de pagos',
            'payment_methods': 'Métodos de pago aceptados',
            'payment_terms': 'Términos de pago',
            'estimated_start': 'Inicio estimado',
            'estimated_delivery': 'Entrega estimada',
            'business_days': 'Días laborables',
            'approval_text': 'Al firmar abajo, el cliente aprueba esta propuesta y autoriza a Papia Technology Solutions LLC a comenzar el proyecto bajo los términos descritos.',
            'client_signature': 'Firma del cliente / fecha',
        }
    return {
        'proposal': 'Proposal',
        'client_information': 'Client Information',
        'needs_objective': 'Needs and Objective',
        'selected_services': 'Selected Services',
        'pricing': 'Pricing',
        'total_project_cost': 'Total Project Cost',
        'initial_payment': 'Initial Payment',
        'remaining_balance': 'Remaining Balance',
        'timeline': 'Timeline',
        'terms': 'Terms and Conditions',
        'approval': 'Client Approval',
        'client': 'Client',
        'business': 'Business',
        'phone': 'Phone',
        'client_needs': 'Client needs / business problem',
        'main_objective': 'Main objective',
        'payment_schedule': 'Payment schedule',
        'payment_methods': 'Accepted payment methods',
        'payment_terms': 'Payment terms',
        'estimated_start': 'Estimated start',
        'estimated_delivery': 'Estimated delivery',
        'business_days': 'Business days',
        'approval_text': 'By signing below, the client approves this proposal and authorizes Papia Technology Solutions LLC to begin the project under the terms described above.',
        'client_signature': 'Client signature / date',
    }
