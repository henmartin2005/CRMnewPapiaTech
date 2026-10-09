import SwiftUI

/// Nuevo chat: número + mensaje. Si el contacto nunca escribió, WhatsApp exige plantilla.
struct NewConversationSheet: View {
    var onOpen: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var phone = ""
    @State private var message = ""
    @State private var sending = false
    @State private var error: String?
    @State private var needsTemplate = false

    private var e164: String { Formatters.e164(phone) }

    var body: some View {
        NavigationStack {
            Form {
                Section("Número") {
                    TextField("+1 786 555 0101", text: $phone)
                        .keyboardType(.phonePad)
                        .textContentType(.telephoneNumber)
                }
                Section {
                    TextField("Escribe un mensaje (opcional)", text: $message, axis: .vertical)
                        .lineLimit(3...6)
                } header: {
                    Text("Mensaje")
                } footer: {
                    Text("Si el contacto no te ha escrito en las últimas 24 horas, WhatsApp exige una plantilla aprobada.")
                }
                if let error {
                    Section { ErrorBanner(message: error) }
                }
                if needsTemplate {
                    Section {
                        NavigationLink("Elegir plantilla aprobada") {
                            TemplatePickerSheet(phone: e164, clientId: nil, embedded: true) { onOpen(e164) }
                        }
                    }
                }
            }
            .navigationTitle("Nuevo chat")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(message.isEmpty ? "Abrir" : "Enviar") { Task { await start() } }
                        .disabled(e164.count < 8 || sending)
                }
            }
        }
    }

    private func start() async {
        guard !message.trimmingCharacters(in: .whitespaces).isEmpty else {
            onOpen(e164)
            return
        }
        sending = true
        defer { sending = false }
        do {
            try await APIClient.shared.sendWhatsApp(phone: e164, message: message, clientId: nil)
            onOpen(e164)
        } catch APIError.templateRequired(let msg) {
            error = msg
            needsTemplate = true
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Lista de plantillas aprobadas por Meta y envío con sus variables.
struct TemplatePickerSheet: View {
    let phone: String
    let clientId: Int?
    var embedded = false
    var onSent: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var templates: [WhatsAppTemplate] = []
    @State private var selected: WhatsAppTemplate?
    @State private var params = ""
    @State private var loading = true
    @State private var sending = false
    @State private var error: String?

    var body: some View {
        if embedded { content } else { NavigationStack { content } }
    }

    private var content: some View {
        Form {
            Section("Plantillas aprobadas") {
                if loading {
                    ProgressView()
                } else if templates.isEmpty {
                    Text("No hay plantillas aprobadas.").foregroundStyle(Theme.Colors.textSecondary)
                }
                ForEach(templates) { template in
                    Button {
                        selected = template
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(template.name).foregroundStyle(Theme.Colors.textPrimary)
                                Text([template.language, template.category].compactMap { $0 }.joined(separator: " · "))
                                    .font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                            }
                            Spacer()
                            if selected == template { Image(systemName: "checkmark").foregroundStyle(Theme.Colors.accent) }
                        }
                    }
                }
            }
            if selected != nil {
                Section {
                    TextField("Ana | 15 de octubre", text: $params)
                } header: {
                    Text("Variables")
                } footer: {
                    Text("Separa cada variable con | en el orden de la plantilla ({{1}}, {{2}}…). Déjalo vacío si no tiene.")
                }
            }
            if let error { Section { ErrorBanner(message: error) } }
        }
        .navigationTitle("Plantilla")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !embedded {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Enviar") { Task { await send() } }
                    .disabled(selected == nil || sending)
            }
        }
        .task {
            do { templates = try await APIClient.shared.templates() }
            catch { self.error = error.localizedDescription }
            loading = false
        }
    }

    private func send() async {
        guard let selected else { return }
        sending = true
        defer { sending = false }
        let values = params.split(separator: "|").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        do {
            try await APIClient.shared.sendWhatsApp(phone: phone, message: "", clientId: clientId,
                                                    template: selected, templateParams: values)
            onSent()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

#if DEBUG
#Preview("Nuevo chat") {
    NewConversationSheet { _ in }
}
#endif
