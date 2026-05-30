from database import get_db
from models.proposal_whatsapp import create_acceptance_link


def build_proposal_email(proposal):
    language = proposal.get('proposal_language', 'en')
    title = proposal.get('title', '')
    client = proposal.get('client_name', '')
    whatsapp_link = proposal.get('whatsapp_acceptance_link') or create_acceptance_link(proposal)

    if language == 'es':
        subject = f"Propuesta para {title} - Papia Technology Solutions LLC"
        body = (
            f"Hola {client},\n\n"
            "Gracias por considerar a Papia Technology Solutions LLC para tu proyecto.\n\n"
            f"Hemos adjuntado la propuesta para {title}, donde se incluye el alcance del proyecto, "
            "los servicios seleccionados, el tiempo estimado de entrega, el precio y los términos de pago.\n\n"
            "Por favor revisa el documento adjunto. Si todo está correcto, puedes responder este correo "
            "o confirmar tu aprobación por WhatsApp.\n\n"
            f"Aceptar propuesta por WhatsApp:\n{whatsapp_link}\n\n"
            "Saludos cordiales,\n"
            "Papia Technology Solutions LLC\n"
            "www.papiatech.com"
        )
    else:
        subject = f"Proposal for {title} - Papia Technology Solutions LLC"
        body = (
            f"Hi {client},\n\n"
            "Thank you for considering Papia Technology Solutions LLC for your project.\n\n"
            f"We have attached the proposal for {title}, including the project scope, selected services, "
            "estimated timeline, pricing, and payment terms.\n\n"
            "Please review the attached document. If everything looks good, you can reply to this email "
            "or confirm your approval through WhatsApp.\n\n"
            f"Accept Proposal by WhatsApp:\n{whatsapp_link}\n\n"
            "Best regards,\n"
            "Papia Technology Solutions LLC\n"
            "www.papiatech.com"
        )
    return subject, body, whatsapp_link


def create_draft_from_proposal(proposal):
    subject, body, whatsapp_link = build_proposal_email(proposal)
    attachment_name = f"proposal-{proposal['id']}.pdf"
    attachment_url = proposal.get('pdf_url') or f"/proposals/{proposal['id']}/pdf"

    db = get_db()
    cursor = db.execute("""
        INSERT INTO email_drafts (
            proposal_id, client_id, to_email, subject, body, attachment_name, attachment_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        proposal['id'],
        proposal.get('client_id'),
        proposal['email'],
        subject,
        body,
        attachment_name,
        attachment_url,
    ))
    draft_id = cursor.lastrowid
    db.execute("""
        UPDATE proposals
        SET email_draft_id=?, whatsapp_acceptance_link=?, status='ready_to_send',
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (draft_id, whatsapp_link, proposal['id']))
    db.commit()
    db.close()
    return draft_id


def get_email_draft(draft_id):
    db = get_db()
    row = db.execute("SELECT * FROM email_drafts WHERE id=?", (draft_id,)).fetchone()
    db.close()
    return dict(row) if row else None


class ProposalEmailService:
    def createDraftFromProposal(self, proposal):
        return create_draft_from_proposal(proposal)

    def buildEmail(self, proposal):
        return build_proposal_email(proposal)


proposalEmailService = ProposalEmailService()
