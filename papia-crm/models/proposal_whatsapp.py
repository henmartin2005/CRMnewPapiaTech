from urllib.parse import quote


BUSINESS_WHATSAPP_NUMBER = '17869157096'


def create_acceptance_link(proposal):
    language = proposal.get('proposal_language', 'en')
    title = proposal.get('title', '')
    client_name = proposal.get('client_name', '')
    if language == 'es':
        message = f"Hola, revisé la propuesta para {title} y me gustaría aceptarla. Mi nombre es {client_name}."
    else:
        message = f"Hello, I reviewed the proposal for {title} and I would like to accept it. My name is {client_name}."
    return f"https://wa.me/{BUSINESS_WHATSAPP_NUMBER}?text={quote(message)}"


def acceptance_button_label(language):
    return 'Aceptar propuesta por WhatsApp' if language == 'es' else 'Accept Proposal by WhatsApp'


class ProposalWhatsappService:
    def createAcceptanceLink(self, proposal):
        return create_acceptance_link(proposal)


proposalWhatsappService = ProposalWhatsappService()
