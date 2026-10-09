import Foundation

// Modelos que devuelve /api/mobile del CRM.
// El decodificador convierte snake_case → camelCase (first_name → firstName).

struct User: Codable, Hashable {
    let id: Int
    let username: String
    let displayName: String?
    let role: String
    let orgId: Int?

    var name: String { (displayName?.isEmpty == false ? displayName : nil) ?? username }
}

struct Option: Codable, Hashable, Identifiable {
    let key: String
    let label: String
    var id: String { key }
}

struct Metadata: Codable {
    var pipelineStages: [Option]
    var projectTypes: [Option]
    var followUpMethods: [Option]

    static let fallback = Metadata(
        pipelineStages: [
            Option(key: "new_lead", label: "Nuevo Lead"),
            Option(key: "contacted", label: "Contactado"),
            Option(key: "proposal_sent", label: "Propuesta Enviada"),
            Option(key: "negotiation", label: "Negociación"),
            Option(key: "active_client", label: "Cliente Activo"),
            Option(key: "recurring", label: "Recurrente"),
        ],
        projectTypes: [
            Option(key: "website", label: "Website"),
            Option(key: "crm", label: "CRM"),
            Option(key: "mobile_app", label: "Mobile App"),
            Option(key: "consulting", label: "Consulting"),
            Option(key: "other", label: "Other"),
        ],
        followUpMethods: []
    )

    func stageLabel(_ key: String?) -> String {
        guard let key else { return "" }
        return pipelineStages.first { $0.key == key }?.label ?? key
    }

    func projectLabel(_ key: String?) -> String {
        guard let key else { return "" }
        return projectTypes.first { $0.key == key }?.label ?? key
    }
}

// MARK: - Clientes

struct Client: Codable, Identifiable, Hashable {
    let id: Int
    var firstName: String
    var lastName: String?
    var email: String?
    var phone: String?
    var company: String?
    var projectType: String?
    var projectDetails: String?
    var pipelineStage: String?
    var totalCost: Double
    var amountPaid: Double
    var pending: Double?
    var brochureSent: Bool?
    var createdAt: String?
    var updatedAt: String?

    var fullName: String {
        [firstName, lastName ?? ""].joined(separator: " ").trimmingCharacters(in: .whitespaces)
    }
    var balance: Double { pending ?? (totalCost - amountPaid) }
}

/// Lo que se manda al crear o editar un cliente.
struct ClientDraft: Codable, Equatable {
    var firstName = ""
    var lastName = ""
    var email = ""
    var phone = ""
    var company = ""
    var projectType = "website"
    var projectDetails = ""
    var pipelineStage = "new_lead"
    var totalCost: Double = 0
    var amountPaid: Double = 0
    var brochureSent = false

    init() {}

    init(client: Client) {
        firstName = client.firstName
        lastName = client.lastName ?? ""
        email = client.email ?? ""
        phone = client.phone ?? ""
        company = client.company ?? ""
        projectType = client.projectType ?? "website"
        projectDetails = client.projectDetails ?? ""
        pipelineStage = client.pipelineStage ?? "new_lead"
        totalCost = client.totalCost
        amountPaid = client.amountPaid
        brochureSent = client.brochureSent ?? false
    }

    var isValid: Bool {
        !firstName.trimmingCharacters(in: .whitespaces).isEmpty
            && (!email.trimmingCharacters(in: .whitespaces).isEmpty || !phone.trimmingCharacters(in: .whitespaces).isEmpty)
            && amountPaid <= totalCost
    }
}

struct Note: Codable, Identifiable, Hashable {
    let id: Int
    let noteType: String?
    let content: String?
    let createdAt: String?
}

struct FollowUp: Codable, Identifiable, Hashable {
    let id: Int
    let clientId: Int?
    let method: String?
    let summary: String?
    let result: String?
    let nextAt: String?
    let nextDate: String?
    let reminderComment: String?
    let completed: Bool
    let clientName: String?
    let createdAt: String?

    var dueText: String { nextAt ?? nextDate ?? "" }
}

struct PipelineStage: Codable, Identifiable, Hashable {
    let key: String
    let label: String
    var clients: [Client]
    var id: String { key }

    var totalValue: Double { clients.reduce(0) { $0 + $1.totalCost } }
    var totalPending: Double { clients.reduce(0) { $0 + $1.balance } }
}

// MARK: - WhatsApp

struct Conversation: Codable, Identifiable, Hashable {
    let phone: String
    let clientId: Int?
    let clientName: String
    let lastMessage: String
    let lastDirection: String
    let lastStatus: String
    let lastAt: String
    let unread: Int

    var id: String { phone }
    var title: String { clientName.isEmpty ? Formatters.phone(phone) : clientName }
}

struct ChatMessage: Codable, Identifiable, Hashable {
    let id: Int
    let phone: String
    let direction: String
    let message: String?
    let status: String?
    let waMessageId: String?
    let createdAt: String?
    let mediaUrl: String?
    let mediaType: String?

    var isOutgoing: Bool { direction == "outbound" }
    var text: String { message ?? "" }
    var isPlaceholderText: Bool { mediaType != nil && text.hasPrefix("[") && text.hasSuffix("]") }
}

struct BotState: Codable, Hashable {
    let status: String          // on | off | paused
    let globalEnabled: Bool?

    var label: String {
        switch status {
        case "on": return "Asistente IA activo"
        case "paused": return "Asistente en pausa (respondiste tú)"
        default: return "Asistente IA apagado"
        }
    }
}

struct WhatsAppTemplate: Codable, Identifiable, Hashable {
    let name: String
    let language: String?
    let status: String?
    let category: String?
    var id: String { "\(name)-\(language ?? "")" }
}

// MARK: - Contratos (Papia Sign)

struct ContractRecipient: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let email: String
    let phone: String
    let role: String
    let routingOrder: Int
    let status: String
    let statusLabel: String
    let signedAt: String?
    let viewedAt: String?
    let declineReason: String?

    var isSigner: Bool { role == "signer" }
    var isPending: Bool { isSigner && (status == "sent" || status == "viewed") }
}

struct ContractEvent: Codable, Hashable {
    let label: String
    let event: String
    let actor: String
    let detail: String
    let device: String
    let createdAt: String
}

struct Contract: Codable, Identifiable, Hashable {
    let id: Int
    let uid: String
    let title: String
    let status: String
    let statusLabel: String
    let clientId: Int?
    let clientName: String
    let message: String
    let pageCount: Int?
    let createdAt: String?
    let sentAt: String?
    let completedAt: String?
    let expiresAt: String?
    let lastActivity: String?
    let signerTotal: Int
    let signerDone: Int
    let recipients: [ContractRecipient]
    let documentUrl: String
    let finalUrl: String?
    let webUrl: String
    let hasFields: Bool?
    let events: [ContractEvent]?

    var progress: Double { signerTotal == 0 ? 0 : Double(signerDone) / Double(signerTotal) }
}

struct ContractStats: Codable, Hashable {
    let waiting: Int
    let drafts: Int
    let completed30: Int
    let expiring: Int

    enum CodingKeys: String, CodingKey {
        case waiting, drafts, expiring
        case completed30 = "completed30"
    }
}

/// Destinatario al crear un contrato desde la app.
struct RecipientDraft: Codable, Identifiable, Hashable {
    var id = UUID()
    var name = ""
    var email = ""
    var phone = ""
    var role = "signer"
    var notifyEmail = true
    var notifyWhatsapp = false

    enum CodingKeys: String, CodingKey {
        case name, email, phone, role, notifyEmail, notifyWhatsapp
    }

    var isValid: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty
            && (!email.trimmingCharacters(in: .whitespaces).isEmpty || !phone.trimmingCharacters(in: .whitespaces).isEmpty)
    }
}

// MARK: - Dashboard

struct DashboardStats: Codable, Hashable {
    let totalLeads: Int?
    let activeClients: Int?
    let pendingProposals: Int?
}
