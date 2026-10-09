import SwiftUI
import UniformTypeIdentifiers

/// Enviar un contrato desde el iPhone.
///
/// "Enviar ahora": el CRM agrega al final una página de firmas con un bloque
/// por firmante (firma, nombre y fecha) y lo envía. "Borrador": se guarda para
/// colocar los campos a mano en el CRM web.
struct NewContractView: View {
    var prefilledClient: Client?
    var onCreated: (Contract) -> Void

    init(prefilledClient: Client? = nil, onCreated: @escaping (Contract) -> Void) {
        self.prefilledClient = prefilledClient
        self.onCreated = onCreated
    }

    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState

    @State private var title = ""
    @State private var message = ""
    @State private var pdfData: Data?
    @State private var pdfName = ""
    @State private var client: Client?
    @State private var recipients: [RecipientDraft] = []
    @State private var requireOTP = true
    @State private var sendNow = true
    @State private var showImporter = false
    @State private var showClientPicker = false
    @State private var sending = false
    @State private var error: String?
    @State private var didSetup = false

    private var signers: [RecipientDraft] { recipients.filter { $0.role == "signer" } }
    private var canSend: Bool {
        pdfData != nil && !signers.isEmpty && recipients.allSatisfy(\.isValid) && (!sendNow || signers.count <= 5)
    }

    var body: some View {
        NavigationStack {
            Form {
                documentSection
                Section("Cliente") {
                    Button {
                        showClientPicker = true
                    } label: {
                        HStack {
                            Text(client?.fullName ?? "Vincular a un cliente (opcional)")
                                .foregroundStyle(client == nil ? Theme.Colors.textSecondary : Theme.Colors.textPrimary)
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                        }
                    }
                }
                recipientsSection
                Section {
                    TextField("Mensaje para los firmantes (opcional)", text: $message, axis: .vertical).lineLimit(2...5)
                    Toggle("Verificar identidad con código", isOn: $requireOTP)
                    Picker("Al guardar", selection: $sendNow) {
                        Text("Enviar ahora").tag(true)
                        Text("Borrador (preparar en la web)").tag(false)
                    }
                } header: {
                    Text("Opciones")
                } footer: {
                    Text(sendNow
                         ? "Se agrega una página de firmas al final del PDF con un bloque por firmante. Máximo 5 firmantes."
                         : "Lo encontrarás en Contratos → Borradores para colocar los campos en el CRM web.")
                }
                if let error { Section { ErrorBanner(message: error) } }
            }
            .navigationTitle("Nuevo contrato")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if sending { ProgressView() } else {
                        Button(sendNow ? "Enviar" : "Guardar") { Task { await submit() } }.disabled(!canSend)
                    }
                }
            }
            .fileImporter(isPresented: $showImporter, allowedContentTypes: [.pdf]) { result in
                importPDF(result)
            }
            .sheet(isPresented: $showClientPicker) {
                ClientPickerView { picked in
                    selectClient(picked)
                    showClientPicker = false
                }
            }
            .onAppear(perform: setup)
            .interactiveDismissDisabled(sending)
        }
    }

    // MARK: Secciones

    private var documentSection: some View {
        Section("Documento") {
            Button {
                showImporter = true
            } label: {
                HStack {
                    Image(systemName: pdfData == nil ? "doc.badge.plus" : "doc.fill")
                        .font(.title2)
                        .foregroundStyle(pdfData == nil ? Theme.Colors.accent : Theme.Colors.coral)
                    VStack(alignment: .leading) {
                        Text(pdfData == nil ? "Elegir PDF" : pdfName).foregroundStyle(Theme.Colors.textPrimary)
                        if let pdfData {
                            Text(ByteCountFormatter.string(fromByteCount: Int64(pdfData.count), countStyle: .file))
                                .font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                        } else {
                            Text("Desde Archivos, iCloud Drive o Google Drive").font(.caption)
                                .foregroundStyle(Theme.Colors.textSecondary)
                        }
                    }
                }
            }
            TextField("Título del contrato", text: $title)
        }
    }

    private var recipientsSection: some View {
        Section {
            ForEach($recipients) { $recipient in
                RecipientEditor(recipient: $recipient)
            }
            .onDelete { recipients.remove(atOffsets: $0) }
            Menu {
                Button("Firmante") { recipients.append(RecipientDraft()) }
                Button("Agregarme como firmante") {
                    var me = RecipientDraft()
                    me.name = appState.user?.name ?? ""
                    recipients.append(me)
                }
                Button("Copia (no firma)") {
                    var cc = RecipientDraft()
                    cc.role = "cc"
                    recipients.append(cc)
                }
            } label: {
                Label("Agregar destinatario", systemImage: "person.badge.plus")
            }
        } header: {
            Text("Destinatarios")
        } footer: {
            Text("Firman en el orden de la lista. Cada uno necesita email o teléfono para recibir su enlace.")
        }
    }

    // MARK: Lógica

    private func setup() {
        guard !didSetup else { return }
        didSetup = true
        if let prefilledClient { selectClient(prefilledClient) }
    }

    private func selectClient(_ picked: Client) {
        client = picked
        let already = recipients.contains { $0.name == picked.fullName }
        if !already {
            var signer = RecipientDraft()
            signer.name = picked.fullName
            signer.email = picked.email ?? ""
            signer.phone = picked.phone ?? ""
            signer.notifyWhatsapp = (picked.email ?? "").isEmpty && !(picked.phone ?? "").isEmpty
            recipients.insert(signer, at: 0)
        }
    }

    private func importPDF(_ result: Result<URL, Error>) {
        switch result {
        case .success(let url):
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            do {
                let data = try Data(contentsOf: url)
                guard data.count <= 20 * 1024 * 1024 else {
                    error = "El PDF debe pesar menos de 20 MB."
                    return
                }
                pdfData = data
                pdfName = url.lastPathComponent
                if title.isEmpty { title = url.deletingPathExtension().lastPathComponent }
            } catch {
                self.error = "No se pudo leer el archivo: \(error.localizedDescription)"
            }
        case .failure(let failure):
            error = failure.localizedDescription
        }
    }

    private func submit() async {
        guard let pdfData else { return }
        sending = true
        defer { sending = false }
        do {
            let res = try await APIClient.shared.createContract(
                title: title.isEmpty ? pdfName : title, pdf: pdfData, filename: pdfName.isEmpty ? "contrato.pdf" : pdfName,
                clientId: client?.id, message: message, recipients: recipients, requireOTP: requireOTP,
                mode: sendNow ? "send" : "draft")
            onCreated(res.contract)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Editor de destinatario

struct RecipientEditor: View {
    @Binding var recipient: RecipientDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: recipient.role == "signer" ? "signature" : "doc.on.doc")
                    .foregroundStyle(Theme.Colors.accent)
                TextField(recipient.role == "signer" ? "Nombre del firmante" : "Nombre (copia)", text: $recipient.name)
                    .font(.headline)
            }
            TextField("Email", text: $recipient.email)
                .keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
            TextField("Teléfono (para WhatsApp)", text: $recipient.phone).keyboardType(.phonePad)
            if !recipient.phone.isEmpty {
                Toggle("Enviar enlace por WhatsApp", isOn: $recipient.notifyWhatsapp).font(.callout)
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Selector de cliente

struct ClientPickerView: View {
    var onPick: (Client) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var clients: [Client] = []
    @State private var search = ""

    private var filtered: [Client] {
        search.isEmpty ? clients : clients.filter {
            $0.fullName.localizedCaseInsensitiveContains(search) || ($0.company ?? "").localizedCaseInsensitiveContains(search)
        }
    }

    var body: some View {
        NavigationStack {
            List(filtered) { client in
                Button { onPick(client) } label: { ClientRow(client: client) }
                    .buttonStyle(.plain)
            }
            .listStyle(.plain)
            .searchable(text: $search)
            .navigationTitle("Elegir cliente")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } } }
            .task { clients = (try? await APIClient.shared.clients()) ?? [] }
        }
    }
}

#if DEBUG
#Preview {
    NewContractView(prefilledClient: PreviewData.clients[0]) { _ in }
        .environment(AppState.preview)
}
#endif
