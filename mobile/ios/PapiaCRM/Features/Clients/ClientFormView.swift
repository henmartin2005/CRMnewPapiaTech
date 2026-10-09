import SwiftUI

struct ClientFormView: View {
    enum Mode {
        case create
        case edit(Client)
    }

    let mode: Mode
    var onSaved: (Client) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState
    @State private var draft = ClientDraft()
    @State private var saving = false
    @State private var error: String?
    @State private var didLoad = false

    private var isEdit: Bool {
        if case .edit = mode { return true }
        return false
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Contacto") {
                    TextField("Nombre *", text: $draft.firstName).textContentType(.givenName)
                    TextField("Apellido", text: $draft.lastName).textContentType(.familyName)
                    TextField("Empresa", text: $draft.company).textContentType(.organizationName)
                    TextField("Teléfono", text: $draft.phone).keyboardType(.phonePad).textContentType(.telephoneNumber)
                    TextField("Email", text: $draft.email)
                        .keyboardType(.emailAddress).textContentType(.emailAddress)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }

                Section("Proyecto") {
                    Picker("Tipo", selection: $draft.projectType) {
                        ForEach(appState.metadata.projectTypes) { Text($0.label).tag($0.key) }
                    }
                    Picker("Etapa", selection: $draft.pipelineStage) {
                        ForEach(appState.metadata.pipelineStages) { Text($0.label).tag($0.key) }
                    }
                    TextField("Detalles del proyecto", text: $draft.projectDetails, axis: .vertical)
                        .lineLimit(3...8)
                }

                Section {
                    LabeledContent("Costo total") {
                        TextField("0", value: $draft.totalCost, format: .number)
                            .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                    }
                    LabeledContent("Pagado") {
                        TextField("0", value: $draft.amountPaid, format: .number)
                            .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                    }
                    Toggle("Brochure enviado", isOn: $draft.brochureSent)
                } header: {
                    Text("Finanzas")
                } footer: {
                    if draft.amountPaid > draft.totalCost {
                        Text("Lo pagado no puede ser mayor que el total.").foregroundStyle(Theme.Colors.danger)
                    }
                }

                if let error {
                    Section { ErrorBanner(message: error) }
                }
            }
            .navigationTitle(isEdit ? "Editar cliente" : "Nuevo cliente")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isEdit ? "Guardar" : "Crear") { Task { await save() } }
                        .disabled(!draft.isValid || saving)
                }
            }
            .onAppear {
                guard !didLoad else { return }
                didLoad = true
                if case .edit(let client) = mode { draft = ClientDraft(client: client) }
            }
            .interactiveDismissDisabled(saving)
        }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        do {
            let saved: Client
            switch mode {
            case .create: saved = try await APIClient.shared.createClient(draft)
            case .edit(let client): saved = try await APIClient.shared.updateClient(client.id, draft)
            }
            onSaved(saved)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

#if DEBUG
#Preview("Nuevo") {
    ClientFormView(mode: .create) { _ in }
        .environment(AppState.preview)
}

#Preview("Editar") {
    ClientFormView(mode: .edit(PreviewData.clients[0])) { _ in }
        .environment(AppState.preview)
}
#endif
