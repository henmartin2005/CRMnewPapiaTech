import Foundation

extension Notification.Name {
    /// El servidor rechazó el token (venció o se desactivó el usuario).
    static let sessionExpired = Notification.Name("PapiaSessionExpired")
    /// Llegó un push de WhatsApp con la app abierta: refrescar listas y chats.
    static let whatsappPushReceived = Notification.Name("PapiaWhatsAppPushReceived")
}

enum APIError: LocalizedError {
    case unauthorized
    case templateRequired(String)
    case server(String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .unauthorized: return "Tu sesión venció. Vuelve a iniciar sesión."
        case .templateRequired(let message): return message
        case .server(let message): return message
        case .invalidResponse: return "Respuesta inesperada del servidor."
        }
    }
}

/// Cliente de la API móvil del CRM (/api/mobile/...).
final class APIClient {
    static let shared = APIClient()

    var token: String? {
        get { Keychain.get("token") }
        set { Keychain.set(newValue, for: "token") }
    }

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    // MARK: - Núcleo

    func url(_ path: String, query: [String: String?] = [:]) -> URL {
        var components = URLComponents(url: AppConfig.serverURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        let items = query.compactMap { key, value in value.map { URLQueryItem(name: key, value: $0) } }
        if !items.isEmpty { components.queryItems = items }
        return components.url!
    }

    /// URL absoluta para rutas que devuelve el servidor ("/api/mobile/...").
    func absoluteURL(_ path: String) -> URL {
        if let url = URL(string: path), url.scheme != nil { return url }
        return AppConfig.serverURL.appendingPathComponent(path)
    }

    private func authorized(_ request: URLRequest) -> URLRequest {
        var request = request
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("PapiaCRM-iOS/\(AppConfig.appVersion)", forHTTPHeaderField: "User-Agent")
        return request
    }

    @discardableResult
    func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: authorized(request))
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        if (200..<300).contains(http.statusCode) { return data }

        let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let message = (body["error"] as? String)
            ?? (body["errors"] as? [String])?.joined(separator: "\n")
            ?? "Error \(http.statusCode) del servidor."
        if http.statusCode == 401 {
            if token != nil { NotificationCenter.default.post(name: .sessionExpired, object: nil) }
            throw APIError.unauthorized
        }
        if body["template_required"] as? Bool == true { throw APIError.templateRequired(message) }
        throw APIError.server(message)
    }

    func get<T: Decodable>(_ path: String, query: [String: String?] = [:], as type: T.Type = T.self) async throws -> T {
        let data = try await send(URLRequest(url: url(path, query: query)))
        return try decoder.decode(T.self, from: data)
    }

    func json<T: Decodable>(_ method: String, _ path: String, body: Encodable? = nil, as type: T.Type = T.self) async throws -> T {
        var request = URLRequest(url: url(path))
        request.httpMethod = method
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try encoder.encode(AnyEncodable(body))
        }
        let data = try await send(request)
        return try decoder.decode(T.self, from: data)
    }

    func multipart<T: Decodable>(_ path: String, form: MultipartForm, as type: T.Type = T.self) async throws -> T {
        var request = URLRequest(url: url(path))
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(form.boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = form.finalized()
        request.timeoutInterval = 120
        let data = try await send(request)
        return try decoder.decode(T.self, from: data)
    }

    /// Descarga un archivo protegido (imagen de WhatsApp, PDF de un contrato).
    func download(_ path: String) async throws -> Data {
        try await send(URLRequest(url: absoluteURL(path)))
    }
}

// MARK: - Endpoints

struct OKResponse: Decodable { let ok: Bool }

extension APIClient {

    // Sesión
    struct LoginResponse: Decodable { let token: String; let user: User }
    struct MeResponse: Decodable { let user: User }

    func login(username: String, password: String) async throws -> User {
        struct Body: Encodable { let username: String; let password: String }
        token = nil
        let res: LoginResponse
        do {
            res = try await json("POST", "api/mobile/login", body: Body(username: username, password: password))
        } catch APIError.unauthorized {
            throw APIError.server("Usuario o contraseña incorrectos.")
        }
        token = res.token
        return res.user
    }

    func me() async throws -> User {
        try await get("api/mobile/me", as: MeResponse.self).user
    }

    func metadata() async throws -> Metadata {
        try await get("api/mobile/metadata")
    }

    // Clientes
    struct ClientsResponse: Decodable { let clients: [Client] }
    struct ClientResponse: Decodable { let client: Client }
    struct ClientDetailResponse: Decodable {
        let client: Client
        let notes: [Note]
        let followups: [FollowUp]
    }

    func clients(search: String? = nil) async throws -> [Client] {
        try await get("api/mobile/clients", query: ["q": search], as: ClientsResponse.self).clients
    }

    func client(_ id: Int) async throws -> ClientDetailResponse {
        try await get("api/mobile/clients/\(id)")
    }

    func createClient(_ draft: ClientDraft) async throws -> Client {
        try await json("POST", "api/mobile/clients", body: draft, as: ClientResponse.self).client
    }

    func updateClient(_ id: Int, _ draft: ClientDraft) async throws -> Client {
        try await json("PATCH", "api/mobile/clients/\(id)", body: draft, as: ClientResponse.self).client
    }

    func addFollowUp(clientId: Int, method: String, summary: String, reminderAt: Date) async throws {
        struct Body: Encodable { let method: String; let summary: String; let reminderAt: String }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm"      // mismo formato que el campo de fecha del CRM web
        let _: OKResponse = try await json("POST", "api/mobile/clients/\(clientId)/followups",
                                           body: Body(method: method, summary: summary,
                                                      reminderAt: formatter.string(from: reminderAt)))
    }

    // Pipeline
    struct PipelineResponse: Decodable { let stages: [PipelineStage] }

    func pipeline() async throws -> [PipelineStage] {
        try await get("api/mobile/pipeline", as: PipelineResponse.self).stages
    }

    func move(clientId: Int, to stage: String) async throws {
        struct Body: Encodable { let clientId: Int; let stage: String }
        let _: OKResponse = try await json("POST", "api/mobile/pipeline/move", body: Body(clientId: clientId, stage: stage))
    }

    // Tareas
    struct TasksResponse: Decodable { let tasks: [FollowUp] }

    func tasks() async throws -> [FollowUp] {
        try await get("api/mobile/tasks", as: TasksResponse.self).tasks
    }

    func completeTask(_ id: Int) async throws {
        let _: OKResponse = try await json("POST", "api/mobile/tasks/\(id)/complete")
    }

    // WhatsApp
    struct ConversationsResponse: Decodable { let unread: Int; let conversations: [Conversation] }
    struct MessagesResponse: Decodable {
        let phone: String
        let messages: [ChatMessage]
        let client: Client?
        let bot: BotState?
    }
    struct TemplatesResponse: Decodable { let templates: [WhatsAppTemplate] }
    struct BotResponse: Decodable { let bot: BotState? }

    func conversations() async throws -> ConversationsResponse {
        try await get("api/mobile/whatsapp")
    }

    func messages(phone: String, markRead: Bool) async throws -> MessagesResponse {
        try await get("api/mobile/whatsapp/messages", query: ["phone": phone, "mark_read": markRead ? "1" : nil])
    }

    func markRead(phone: String) async throws {
        struct Body: Encodable { let phone: String }
        let _: OKResponse = try await json("POST", "api/mobile/whatsapp/read", body: Body(phone: phone))
    }

    func sendWhatsApp(phone: String, message: String, clientId: Int?, template: WhatsAppTemplate? = nil,
                      templateParams: [String] = []) async throws {
        struct Template: Encodable { let name: String; let language: String; let params: [String] }
        struct Body: Encodable { let phone: String; let message: String; let clientId: Int?; let template: Template? }
        let tpl = template.map { Template(name: $0.name, language: $0.language ?? "es", params: templateParams) }
        let _: OKResponse = try await json("POST", "api/mobile/whatsapp/send",
                                           body: Body(phone: phone, message: message, clientId: clientId, template: tpl))
    }

    func sendWhatsAppMedia(phone: String, data: Data, filename: String, mimeType: String, kind: String,
                           clientId: Int?) async throws {
        var form = MultipartForm()
        form.add("phone", phone)
        form.add("kind", kind)
        if let clientId { form.add("client_id", String(clientId)) }
        form.addFile("file", filename: filename, mimeType: mimeType, data: data)
        let _: OKResponse = try await multipart("api/mobile/whatsapp/send-media", form: form)
    }

    func templates() async throws -> [WhatsAppTemplate] {
        try await get("api/mobile/whatsapp/templates", as: TemplatesResponse.self).templates
    }

    func setBot(phone: String, enabled: Bool) async throws -> BotState? {
        struct Body: Encodable { let phone: String; let enabled: Bool }
        return try await json("POST", "api/mobile/whatsapp/bot", body: Body(phone: phone, enabled: enabled), as: BotResponse.self).bot
    }

    // Notificaciones push
    func registerDevice(token deviceToken: String) async throws {
        struct Body: Encodable {
            let token: String; let platform: String; let environment: String
            let appVersion: String; let deviceName: String
        }
        let _: OKResponse = try await json("POST", "api/mobile/devices", body: Body(
            token: deviceToken, platform: "ios", environment: AppConfig.pushEnvironment,
            appVersion: AppConfig.appVersion, deviceName: "iPhone"))
    }

    func unregisterDevice(token deviceToken: String) async throws {
        struct Body: Encodable { let token: String }
        let _: OKResponse = try await json("DELETE", "api/mobile/devices", body: Body(token: deviceToken))
    }

    // Contratos
    struct ContractsResponse: Decodable {
        let tabs: [Option]
        let stats: ContractStats
        let contracts: [Contract]
    }
    struct ContractResponse: Decodable { let contract: Contract; let warning: String? }

    func contracts(tab: String = "all", search: String? = nil, clientId: Int? = nil) async throws -> ContractsResponse {
        try await get("api/mobile/contracts", query: ["tab": tab, "q": search, "client_id": clientId.map(String.init)])
    }

    func contract(_ id: Int) async throws -> Contract {
        try await get("api/mobile/contracts/\(id)", as: ContractResponse.self).contract
    }

    /// mode "send": agrega página de firmas y envía · "draft": borrador para preparar en la web.
    func createContract(title: String, pdf: Data, filename: String, clientId: Int?, message: String,
                        recipients: [RecipientDraft], requireOTP: Bool, mode: String) async throws -> ContractResponse {
        var form = MultipartForm()
        form.add("title", title)
        form.add("message", message)
        form.add("mode", mode)
        form.add("require_otp", requireOTP ? "1" : "0")
        if let clientId { form.add("client_id", String(clientId)) }
        let recipientsJSON = try encoder.encode(recipients)
        form.add("recipients", String(data: recipientsJSON, encoding: .utf8) ?? "[]")
        form.addFile("document", filename: filename, mimeType: "application/pdf", data: pdf)
        return try await multipart("api/mobile/contracts", form: form)
    }

    func sendContract(_ id: Int) async throws -> ContractResponse {
        try await json("POST", "api/mobile/contracts/\(id)/send")
    }

    func resendContract(_ id: Int, recipient: Int) async throws {
        let _: OKResponse = try await json("POST", "api/mobile/contracts/\(id)/resend/\(recipient)")
    }

    func voidContract(_ id: Int, reason: String) async throws -> Contract {
        struct Body: Encodable { let reason: String }
        return try await json("POST", "api/mobile/contracts/\(id)/void", body: Body(reason: reason), as: ContractResponse.self).contract
    }

    func deleteContract(_ id: Int) async throws {
        let _: OKResponse = try await json("DELETE", "api/mobile/contracts/\(id)")
    }
}

// MARK: - Utilidades

/// Permite pasar cualquier Encodable a json(_:_:body:).
private struct AnyEncodable: Encodable {
    let value: Encodable
    init(_ value: Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}

struct MultipartForm {
    let boundary = "Papia-\(UUID().uuidString)"
    private var body = Data()

    mutating func add(_ name: String, _ value: String) {
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n")
    }

    mutating func addFile(_ name: String, filename: String, mimeType: String, data: Data) {
        let safeName = filename.replacingOccurrences(of: "\"", with: "")
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"; filename=\"\(safeName)\"\r\n")
        body.append("Content-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        body.append("\r\n")
    }

    func finalized() -> Data {
        var out = body
        out.append("--\(boundary)--\r\n")
        return out
    }
}

private extension Data {
    mutating func append(_ string: String) {
        if let data = string.data(using: .utf8) { append(data) }
    }
}
