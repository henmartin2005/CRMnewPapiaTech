#if DEBUG
import Foundation

/// Datos de ejemplo para los #Preview (Canvas de Xcode). No se usan en la app real.
enum PreviewData {
    static let user = User(id: 1, username: "henrry", displayName: "Henrry Martin", role: "superadmin", orgId: 1)

    static let clients: [Client] = [
        Client(id: 1, firstName: "Ana", lastName: "Pérez", email: "ana@studio.com", phone: "+17865550101",
               company: "Ana Studio", projectType: "website", projectDetails: "Landing page + reservas",
               pipelineStage: "negotiation", totalCost: 1500, amountPaid: 500, pending: 1000, brochureSent: true,
               createdAt: "2026-10-01 14:00:00", updatedAt: "2026-10-09 12:00:00"),
        Client(id: 2, firstName: "Carlos", lastName: "Rivera", email: "carlos@hvac.com", phone: "+13055550199",
               company: "Rivera HVAC", projectType: "crm", projectDetails: "CRM de técnicos en campo",
               pipelineStage: "proposal_sent", totalCost: 4800, amountPaid: 0, pending: 4800, brochureSent: false,
               createdAt: "2026-09-20 10:00:00", updatedAt: "2026-10-08 16:00:00"),
        Client(id: 3, firstName: "Milena", lastName: "Gómez", email: "", phone: "+17865550177",
               company: "Milenita's Flowers", projectType: "website", projectDetails: "Tienda online",
               pipelineStage: "new_lead", totalCost: 0, amountPaid: 0, pending: 0, brochureSent: false,
               createdAt: "2026-10-09 09:00:00", updatedAt: "2026-10-09 09:00:00"),
        Client(id: 4, firstName: "Dental", lastName: "Design", email: "info@dds.com", phone: "+13055550111",
               company: "Dental Design Smile", projectType: "crm", projectDetails: "IA + CRM",
               pipelineStage: "active_client", totalCost: 9000, amountPaid: 6000, pending: 3000, brochureSent: true,
               createdAt: "2026-07-01 09:00:00", updatedAt: "2026-10-02 09:00:00"),
    ]

    static var stages: [PipelineStage] {
        Metadata.fallback.pipelineStages.map { option in
            PipelineStage(key: option.key, label: option.label,
                          clients: clients.filter { $0.pipelineStage == option.key })
        }
    }

    static let conversations: [Conversation] = [
        Conversation(phone: "+17865550101", clientId: 1, clientName: "Ana Pérez",
                     lastMessage: "Perfecto, ¿me mandas el contrato hoy?", lastDirection: "inbound",
                     lastStatus: "received", lastAt: "2026-10-09 20:40:00", unread: 2),
        Conversation(phone: "+13055550199", clientId: 2, clientName: "Carlos Rivera",
                     lastMessage: "Te envié la propuesta por email 👍", lastDirection: "outbound",
                     lastStatus: "read", lastAt: "2026-10-09 15:10:00", unread: 0),
        Conversation(phone: "+17865550177", clientId: nil, clientName: "",
                     lastMessage: "[image]", lastDirection: "inbound",
                     lastStatus: "read", lastAt: "2026-10-07 11:00:00", unread: 0),
    ]

    static let messages: [ChatMessage] = [
        ChatMessage(id: 1, phone: "+17865550101", direction: "inbound", message: "Hola Henrry, vi la propuesta",
                    status: "read", waMessageId: nil, createdAt: "2026-10-08 18:00:00", mediaUrl: nil, mediaType: nil),
        ChatMessage(id: 2, phone: "+17865550101", direction: "outbound",
                    message: "¡Hola Ana! ¿Qué te pareció? Puedo ajustar el alcance si hace falta.",
                    status: "read", waMessageId: "w2", createdAt: "2026-10-08 18:05:00", mediaUrl: nil, mediaType: nil),
        ChatMessage(id: 3, phone: "+17865550101", direction: "inbound",
                    message: "Me encanta. Perfecto, ¿me mandas el contrato hoy?",
                    status: "received", waMessageId: nil, createdAt: "2026-10-09 20:40:00", mediaUrl: nil, mediaType: nil),
        ChatMessage(id: 4, phone: "+17865550101", direction: "outbound", message: "Claro, en un rato te llega para firmar.",
                    status: "delivered", waMessageId: "w4", createdAt: "2026-10-09 20:42:00", mediaUrl: nil, mediaType: nil),
    ]

    static let contracts: [Contract] = [
        contract(id: 1, title: "Acuerdo de servicios – Ana Studio", status: "sent", label: "Esperando firmas",
                 client: "Ana Pérez", done: 1, total: 2),
        contract(id: 2, title: "CRM Rivera HVAC – Fase 1", status: "completed", label: "Completado",
                 client: "Carlos Rivera", done: 1, total: 1),
        contract(id: 3, title: "Mantenimiento mensual", status: "draft", label: "Borrador",
                 client: "Dental Design", done: 0, total: 1),
    ]

    static func contract(id: Int, title: String, status: String, label: String, client: String,
                         done: Int, total: Int) -> Contract {
        let recipients = [
            ContractRecipient(id: id * 10 + 1, name: client, email: "cliente@ejemplo.com", phone: "", role: "signer",
                              routingOrder: 1, status: done > 0 ? "completed" : "sent",
                              statusLabel: done > 0 ? "Firmó" : "Enviado", signedAt: done > 0 ? "2026-10-09 15:00:00" : nil,
                              viewedAt: nil, declineReason: nil),
            ContractRecipient(id: id * 10 + 2, name: "Henrry Martin", email: "henrry@papiatech.com", phone: "",
                              role: "signer", routingOrder: 2, status: done > 1 ? "completed" : "created",
                              statusLabel: done > 1 ? "Firmó" : "En cola", signedAt: nil, viewedAt: nil, declineReason: nil),
        ]
        return Contract(id: id, uid: "A1B2-\(id)", title: title, status: status, statusLabel: label, clientId: 1,
                        clientName: client, message: "", pageCount: 3, createdAt: "2026-10-08 10:00:00",
                        sentAt: "2026-10-08 10:05:00", completedAt: status == "completed" ? "2026-10-09 10:00:00" : nil,
                        expiresAt: "2026-10-22 10:05:00", lastActivity: "2026-10-09 15:00:00",
                        signerTotal: total, signerDone: done, recipients: Array(recipients.prefix(max(total, 1))),
                        documentUrl: "/api/mobile/contracts/\(id)/document",
                        finalUrl: status == "completed" ? "/api/mobile/contracts/\(id)/final" : nil,
                        webUrl: "/contratos/\(id)", hasFields: true,
                        events: [
                            ContractEvent(label: "Sobre creado", event: "created", actor: "Henrry Martin", detail: "",
                                          device: "", createdAt: "2026-10-08 10:00:00"),
                            ContractEvent(label: "Sobre enviado", event: "sent", actor: "Henrry Martin",
                                          detail: "2 firmante(s)", device: "", createdAt: "2026-10-08 10:05:00"),
                            ContractEvent(label: "Firmado", event: "signed", actor: client, detail: "",
                                          device: "Safari / iOS", createdAt: "2026-10-09 15:00:00"),
                        ])
    }

    static let tasks: [FollowUp] = [
        FollowUp(id: 1, clientId: 1, method: "whatsapp", summary: "Enviar contrato a Ana", result: nil,
                 nextAt: "2026-10-10 10:00:00", nextDate: nil, reminderComment: nil, completed: false,
                 clientName: "Ana Pérez", createdAt: nil),
        FollowUp(id: 2, clientId: 2, method: "phone", summary: "Llamar a Carlos por la propuesta", result: nil,
                 nextAt: "2026-10-11 15:00:00", nextDate: nil, reminderComment: nil, completed: false,
                 clientName: "Carlos Rivera", createdAt: nil),
    ]
}

@MainActor
extension AppState {
    /// Estado con sesión iniciada para los previews.
    static var preview: AppState {
        let state = AppState()
        state.user = PreviewData.user
        state.phase = .loggedIn
        state.whatsappUnread = 2
        return state
    }
}
#endif
