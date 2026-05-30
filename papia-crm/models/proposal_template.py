from copy import deepcopy


PROPOSAL_LANGUAGES = [
    ('en', 'English'),
    ('es', 'Spanish'),
]

PROPOSAL_PURPOSES = [
    ('landing_page', 'Landing Page'),
    ('ecommerce', 'Ecommerce Website'),
    ('custom_crm', 'Custom CRM'),
    ('website_crm', 'Website + CRM'),
    ('automation', 'Automation'),
    ('other', 'Other'),
]

PROJECT_TYPES = PROPOSAL_PURPOSES

DEFAULT_TERMS_BY_LANGUAGE = {
    'en': [
        'Project starts after initial payment is received.',
        'Final delivery depends on receiving all required content from the client.',
        'Additional features outside the original scope may require an additional quote.',
        'Payments are non-refundable once development has started.',
        'Domain, hosting, third-party apps, plugins, or external software costs are not included unless specified.',
        'Client is responsible for providing logos, images, brand materials, and business information unless these services are included in the proposal.',
        'Delivery times may change if the client delays feedback or approval.',
    ],
    'es': [
        'El proyecto inicia despues de recibir el pago inicial.',
        'La entrega final depende de recibir todo el contenido requerido por parte del cliente.',
        'Funciones adicionales fuera del alcance original pueden requerir una cotizacion adicional.',
        'Los pagos no son reembolsables una vez que el desarrollo haya comenzado.',
        'Costos de dominio, hosting, aplicaciones externas, plugins o software de terceros no estan incluidos salvo que se especifique.',
        'El cliente es responsable de proveer logos, imagenes, materiales de marca e informacion del negocio salvo que estos servicios esten incluidos en la propuesta.',
        'Los tiempos de entrega pueden cambiar si el cliente retrasa comentarios o aprobaciones.',
    ],
}

DEFAULT_MILESTONES_BY_LANGUAGE = {
    'en': [
        'Initial setup',
        'Design approval',
        'First functional version',
        'Client review',
        'Final adjustments',
        'Deployment',
        'Delivery and training',
    ],
    'es': [
        'Configuracion inicial',
        'Aprobacion del diseno',
        'Primera version funcional',
        'Revision del cliente',
        'Ajustes finales',
        'Publicacion',
        'Entrega y capacitacion',
    ],
}

SERVICE_CATALOG_BY_LANGUAGE = {
    'en': [
        {'id': 'landing_page', 'name': 'Landing Page Development', 'category': 'Main services', 'description': 'Design and development of a focused landing page built to convert visitors into leads.', 'deliveryTime': '5-7 business days'},
        {'id': 'ecommerce', 'name': 'Ecommerce Website Development', 'category': 'Main services', 'description': 'Online store setup with product structure, checkout flow, and core ecommerce pages.', 'deliveryTime': '12-18 business days'},
        {'id': 'custom_crm', 'name': 'Custom CRM Development', 'category': 'Main services', 'description': 'Custom client management system tailored to the business workflow.', 'deliveryTime': '15-25 business days'},
        {'id': 'website_chatbot', 'name': 'Website chatbot agent', 'category': 'Add-on services', 'description': 'Chatbot agent embedded on the website to guide visitors and capture intent.', 'deliveryTime': '3-5 business days'},
        {'id': 'info_chatbot', 'name': 'Client information collection chatbot', 'category': 'Add-on services', 'description': 'Conversation flow to collect important client information before contact.', 'deliveryTime': '3-5 business days'},
        {'id': 'faq_chatbot', 'name': 'Basic FAQ chatbot', 'category': 'Add-on services', 'description': 'Chatbot answers common service questions using approved business information.', 'deliveryTime': '2-4 business days'},
        {'id': 'environment_setup', 'name': 'Environment setup', 'category': 'Add-on services', 'description': 'Initial technical environment setup for development and deployment.', 'deliveryTime': '1-2 business days'},
        {'id': 'domain_connection', 'name': 'Domain connection', 'category': 'Add-on services', 'description': 'Connect the selected domain to the final website or application.', 'deliveryTime': '1 business day'},
        {'id': 'website_deployment', 'name': 'Website deployment', 'category': 'Add-on services', 'description': 'Deploy and validate the website on the public internet.', 'deliveryTime': '1-2 business days'},
        {'id': 'initial_setup', 'name': 'Initial website setup', 'category': 'Add-on services', 'description': 'Configure initial pages, navigation, metadata, and launch-ready settings.', 'deliveryTime': '2-3 business days'},
        {'id': 'logo_design', 'name': 'Logo design', 'category': 'Add-on services', 'description': 'Basic logo concept and final export files for the project.', 'deliveryTime': '3-5 business days'},
        {'id': 'brochure_design', 'name': 'Business brochure design', 'category': 'Add-on services', 'description': 'Professional brochure layout for business presentation or sales use.', 'deliveryTime': '3-5 business days'},
        {'id': 'whatsapp_button', 'name': 'WhatsApp button integration', 'category': 'Add-on services', 'description': 'Floating WhatsApp contact button integrated into the landing page.', 'deliveryTime': '1 business day'},
        {'id': 'dashboard', 'name': 'Dashboard development', 'category': 'Add-on services', 'description': 'Dashboard with relevant metrics and client management data.', 'deliveryTime': '5-8 business days'},
        {'id': 'crm_pipeline', 'name': 'CRM pipeline setup', 'category': 'Add-on services', 'description': 'Pipeline stages and lead tracking workflow configured for the CRM.', 'deliveryTime': '4-7 business days'},
        {'id': 'lead_management', 'name': 'Lead management system', 'category': 'Add-on services', 'description': 'Lead capture, organization, and follow-up management features.', 'deliveryTime': '5-8 business days'},
        {'id': 'automation', 'name': 'Automation setup', 'category': 'Add-on services', 'description': 'Basic automations for follow-ups, notifications, or operational tasks.', 'deliveryTime': '3-6 business days'},
    ],
    'es': [
        {'id': 'landing_page', 'name': 'Desarrollo de Landing Page', 'category': 'Servicios principales', 'description': 'Diseno y desarrollo de una landing page enfocada en convertir visitantes en prospectos.', 'deliveryTime': '5-7 dias laborables'},
        {'id': 'ecommerce', 'name': 'Desarrollo de Sitio Web Ecommerce', 'category': 'Servicios principales', 'description': 'Configuracion de tienda online con estructura de productos, flujo de compra y paginas principales.', 'deliveryTime': '12-18 dias laborables'},
        {'id': 'custom_crm', 'name': 'Desarrollo de CRM a Medida', 'category': 'Servicios principales', 'description': 'Sistema personalizado de gestion de clientes adaptado al flujo de trabajo del negocio.', 'deliveryTime': '15-25 dias laborables'},
        {'id': 'website_chatbot', 'name': 'Agente chatbot para el sitio web', 'category': 'Servicios adicionales', 'description': 'Chatbot integrado en el sitio web para guiar visitantes y capturar intencion.', 'deliveryTime': '3-5 dias laborables'},
        {'id': 'info_chatbot', 'name': 'Chatbot para recopilar informacion del cliente', 'category': 'Servicios adicionales', 'description': 'Flujo conversacional para recopilar informacion importante antes del contacto.', 'deliveryTime': '3-5 dias laborables'},
        {'id': 'faq_chatbot', 'name': 'Chatbot basico de preguntas frecuentes', 'category': 'Servicios adicionales', 'description': 'Chatbot que responde preguntas comunes usando informacion aprobada del negocio.', 'deliveryTime': '2-4 dias laborables'},
        {'id': 'environment_setup', 'name': 'Configuracion del entorno', 'category': 'Servicios adicionales', 'description': 'Configuracion tecnica inicial para desarrollo y publicacion.', 'deliveryTime': '1-2 dias laborables'},
        {'id': 'domain_connection', 'name': 'Conexion de dominio', 'category': 'Servicios adicionales', 'description': 'Conexion del dominio seleccionado al sitio web o aplicacion final.', 'deliveryTime': '1 dia laborable'},
        {'id': 'website_deployment', 'name': 'Publicacion del sitio web', 'category': 'Servicios adicionales', 'description': 'Publicacion y validacion del sitio web en internet.', 'deliveryTime': '1-2 dias laborables'},
        {'id': 'initial_setup', 'name': 'Configuracion inicial del sitio web', 'category': 'Servicios adicionales', 'description': 'Configuracion de paginas iniciales, navegacion, metadata y ajustes de lanzamiento.', 'deliveryTime': '2-3 dias laborables'},
        {'id': 'logo_design', 'name': 'Diseno de logo', 'category': 'Servicios adicionales', 'description': 'Concepto basico de logo y archivos finales para el proyecto.', 'deliveryTime': '3-5 dias laborables'},
        {'id': 'brochure_design', 'name': 'Diseno de brochure empresarial', 'category': 'Servicios adicionales', 'description': 'Diseno profesional de brochure para presentacion comercial o ventas.', 'deliveryTime': '3-5 dias laborables'},
        {'id': 'whatsapp_button', 'name': 'Integracion de boton de WhatsApp', 'category': 'Servicios adicionales', 'description': 'Boton flotante de contacto por WhatsApp integrado en la landing page.', 'deliveryTime': '1 dia laborable'},
        {'id': 'dashboard', 'name': 'Desarrollo de dashboard', 'category': 'Servicios adicionales', 'description': 'Dashboard con metricas relevantes y datos de gestion de clientes.', 'deliveryTime': '5-8 dias laborables'},
        {'id': 'crm_pipeline', 'name': 'Configuracion de pipeline CRM', 'category': 'Servicios adicionales', 'description': 'Etapas de pipeline y flujo de seguimiento de leads configurado en el CRM.', 'deliveryTime': '4-7 dias laborables'},
        {'id': 'lead_management', 'name': 'Sistema de gestion de leads', 'category': 'Servicios adicionales', 'description': 'Funciones para capturar, organizar y dar seguimiento a prospectos.', 'deliveryTime': '5-8 dias laborables'},
        {'id': 'automation', 'name': 'Configuracion de automatizaciones', 'category': 'Servicios adicionales', 'description': 'Automatizaciones basicas para seguimientos, notificaciones o tareas operativas.', 'deliveryTime': '3-6 dias laborables'},
    ],
}

TEMPLATE_PRESETS = {
    ('en', 'landing_page'): {
        'name': 'Landing Page Development Proposal',
        'title': 'Landing Page Development Proposal',
        'services': ['landing_page', 'environment_setup', 'domain_connection', 'website_deployment', 'whatsapp_button'],
        'description': 'A conversion-focused landing page designed to present the business clearly, capture leads, and make it easy for visitors to take action.',
        'needs': 'The business needs a professional online presence that explains the offer, builds trust, and converts visitors into qualified leads.',
        'objective': 'Launch a polished landing page with clear messaging, contact options, and a simple path for potential customers to request service.',
    },
    ('en', 'ecommerce'): {
        'name': 'Ecommerce Website Development Proposal',
        'title': 'Ecommerce Website Development Proposal',
        'services': ['ecommerce', 'environment_setup', 'domain_connection', 'website_deployment', 'initial_setup'],
        'description': 'A professional ecommerce website with product structure, purchase flow, and the core pages needed to start selling online.',
        'needs': 'The business needs a reliable online store to display products, support customer purchases, and manage its digital sales presence.',
        'objective': 'Build and publish an ecommerce website that supports product presentation, checkout readiness, and future growth.',
    },
    ('en', 'custom_crm'): {
        'name': 'Custom CRM Development Proposal',
        'title': 'Custom CRM Development Proposal',
        'services': ['custom_crm', 'dashboard', 'crm_pipeline', 'lead_management', 'automation'],
        'description': 'A custom CRM system designed around the client management workflow, follow-ups, pipeline tracking, and operational visibility.',
        'needs': 'The business needs a centralized system to manage clients, leads, tasks, pipeline status, and business follow-up activity.',
        'objective': 'Create a practical CRM that improves organization, reduces manual work, and gives the team a clear view of active opportunities.',
    },
    ('en', 'website_crm'): {
        'name': 'Website + CRM Proposal',
        'title': 'Website + CRM Development Proposal',
        'services': ['landing_page', 'custom_crm', 'website_chatbot', 'dashboard', 'crm_pipeline', 'lead_management', 'domain_connection', 'website_deployment'],
        'description': 'A combined website and CRM solution that captures leads from the public website and organizes them inside a custom client management system.',
        'needs': 'The business needs both a professional web presence and a structured internal workflow to manage new leads and client conversations.',
        'objective': 'Launch a connected website and CRM experience that helps attract, capture, manage, and follow up with potential customers.',
    },
    ('es', 'landing_page'): {
        'name': 'Propuesta de Desarrollo de Landing Page',
        'title': 'Propuesta de Desarrollo de Landing Page',
        'services': ['landing_page', 'environment_setup', 'domain_connection', 'website_deployment', 'whatsapp_button'],
        'description': 'Una landing page enfocada en conversion para presentar el negocio con claridad, capturar prospectos y facilitar que los visitantes tomen accion.',
        'needs': 'El negocio necesita una presencia online profesional que explique la oferta, genere confianza y convierta visitantes en prospectos calificados.',
        'objective': 'Lanzar una landing page profesional con mensaje claro, opciones de contacto y un camino simple para solicitar el servicio.',
    },
    ('es', 'ecommerce'): {
        'name': 'Propuesta de Sitio Web Ecommerce',
        'title': 'Propuesta de Sitio Web Ecommerce',
        'services': ['ecommerce', 'environment_setup', 'domain_connection', 'website_deployment', 'initial_setup'],
        'description': 'Un sitio ecommerce profesional con estructura de productos, flujo de compra y paginas principales necesarias para vender online.',
        'needs': 'El negocio necesita una tienda online confiable para mostrar productos, facilitar compras y gestionar su presencia digital de ventas.',
        'objective': 'Construir y publicar un sitio ecommerce preparado para presentar productos, iniciar ventas y crecer en el tiempo.',
    },
    ('es', 'custom_crm'): {
        'name': 'Propuesta de Desarrollo de CRM a Medida',
        'title': 'Propuesta de Desarrollo de CRM a Medida',
        'services': ['custom_crm', 'dashboard', 'crm_pipeline', 'lead_management', 'automation'],
        'description': 'Un CRM a medida disenado alrededor del flujo de gestion de clientes, seguimientos, pipeline y visibilidad operativa.',
        'needs': 'El negocio necesita centralizar clientes, leads, tareas, estado del pipeline y actividad de seguimiento comercial.',
        'objective': 'Crear un CRM practico que mejore la organizacion, reduzca trabajo manual y de visibilidad clara sobre oportunidades activas.',
    },
    ('es', 'website_crm'): {
        'name': 'Propuesta de Sitio Web + CRM',
        'title': 'Propuesta de Sitio Web + CRM',
        'services': ['landing_page', 'custom_crm', 'website_chatbot', 'dashboard', 'crm_pipeline', 'lead_management', 'domain_connection', 'website_deployment'],
        'description': 'Una solucion combinada de sitio web y CRM que captura leads desde el sitio publico y los organiza en un sistema de gestion personalizado.',
        'needs': 'El negocio necesita una presencia web profesional y un flujo interno estructurado para gestionar nuevos leads y conversaciones con clientes.',
        'objective': 'Lanzar una experiencia conectada de sitio web y CRM para atraer, capturar, gestionar y dar seguimiento a potenciales clientes.',
    },
}


def get_service(service_id, language='en'):
    catalog = SERVICE_CATALOG_BY_LANGUAGE.get(language, SERVICE_CATALOG_BY_LANGUAGE['en'])
    return next((s for s in catalog if s['id'] == service_id), None)


def get_template(language='en', purpose='landing_page'):
    language = language if language in {'en', 'es'} else 'en'
    purpose = purpose if (language, purpose) in TEMPLATE_PRESETS else 'landing_page'
    preset = TEMPLATE_PRESETS[(language, purpose)]
    services = []
    for service_id in preset['services']:
        service = deepcopy(get_service(service_id, language))
        if service:
            service.update({'price': 0, 'notes': ''})
            services.append(service)
    return {
        'proposal_language': language,
        'proposal_purpose': purpose,
        'template': purpose,
        'title': preset['title'],
        'project_type': purpose,
        'project_description': preset['description'],
        'client_needs': preset['needs'],
        'project_objective': preset['objective'],
        'selected_services': services,
        'milestones': deepcopy(DEFAULT_MILESTONES_BY_LANGUAGE[language]),
        'terms_and_conditions': deepcopy(DEFAULT_TERMS_BY_LANGUAGE[language]),
        'payment_terms': (
            '50% initial payment to begin development. Remaining balance due before final delivery.'
            if language == 'en'
            else '50% de pago inicial para comenzar el desarrollo. El balance restante se paga antes de la entrega final.'
        ),
        'payment_schedule': (
            'Initial payment at approval, remaining balance before launch.'
            if language == 'en'
            else 'Pago inicial al aprobar la propuesta y balance restante antes del lanzamiento.'
        ),
        'payment_methods': (
            'Zelle, ACH transfer, credit/debit card, or agreed business payment method.'
            if language == 'en'
            else 'Zelle, transferencia ACH, tarjeta de credito/debito o metodo de pago comercial acordado.'
        ),
    }


def get_templates_for_ui():
    return [
        {'key': f'{language}:{purpose}', 'language': language, 'purpose': purpose, 'name': preset['name'], 'services': preset['services']}
        for (language, purpose), preset in TEMPLATE_PRESETS.items()
    ]


def get_service_catalog(language='en'):
    return deepcopy(SERVICE_CATALOG_BY_LANGUAGE.get(language, SERVICE_CATALOG_BY_LANGUAGE['en']))


class ProposalTemplateService:
    def getTemplate(self, language='en', purpose='landing_page'):
        return get_template(language, purpose)

    def getServiceCatalog(self, language='en'):
        return get_service_catalog(language)


proposalTemplateService = ProposalTemplateService()
