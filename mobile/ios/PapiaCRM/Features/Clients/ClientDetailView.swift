import SwiftUI

struct ClientDetailView: View {
    @Environment(AppState.self) private var appState

    let clientId: Int
    var onUpdate: ((Client) -> Void)?

    @State private var client: Client?
    @State private var notes: [Note] = []
    @State private var followups: [FollowUp] = []
    @State private var contracts: [Contract] = []
    @State private var error: String?
    @State private var showEdit = false
    @State private var showFollowUp = false
    @State private var showContract = false
    @State private var openChat = false
    private let isPreview: Bool

    init(clientId: Int, onUpdate: ((Client) -> Void)? = nil) {
        self.clientId = clientId
        self.onUpdate = onUpdate
        isPreview = false
    }

    #if DEBUG
    init(preview: Client) {
        clientId = preview.id
        _client = State(initialValue: preview)
        _followups = State(initialValue: PreviewData.tasks.filter { $0.clientId == preview.id })
        _contracts = State(initialValue: PreviewData.contracts.prefix(1).map { $0 })
        isPreview = true
    }
    #endif

    var body: some View {
        ScrollView {
            if let client {
                VStack(spacing: Theme.Spacing.lg) {
                    header(client)
                    actions(client)
                    finances(client)
                    infoCard(client)
                    contractsCard
                    followUpsCard
                    if !notes.isEmpty { notesCard }
                }
                .padding(Theme.Spacing.lg)
            } else if let error {
                ErrorBanner(message: error).padding()
            } else {
                ProgressView().padding(.top, 80)
            }
        }
        .background(Theme.Colors.surfaceAlt)
        .navigationTitle(client?.firstName ?? "Cliente")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Editar") { showEdit = true }.disabled(client == nil)
            }
        }
        .sheet(isPresented: $showEdit) {
            if let client {
                ClientFormView(mode: .edit(client)) { updated in
                    self.client = updated
                    onUpdate?(updated)
                }
            }
        }
        .sheet(isPresented: $showFollowUp) {
            FollowUpFormView(clientId: clientId) { Task { await load() } }
        }
        .sheet(isPresented: $showContract) {
            if let client {
                NewContractView(prefilledClient: client) { _ in Task { await loadContracts() } }
            }
        }
        .navigationDestination(isPresented: $openChat) {
            if let phone = client?.phone, !phone.isEmpty {
                ChatView(phone: Formatters.e164(phone), title: client?.fullName)
            }
        }
        .refreshable { await load() }
        .task { if !isPreview { await load() } }
    }

    // MARK: Secciones

    private func header(_ client: Client) -> some View {
        VStack(spacing: Theme.Spacing.sm) {
            AvatarView(name: client.fullName, size: 76)
            Text(client.fullName).font(Theme.Fonts.title)
            if let company = client.company, !company.isEmpty {
                Text(company).foregroundStyle(Theme.Colors.textSecondary)
            }
            Menu {
                ForEach(appState.metadata.pipelineStages) { stage in
                    Button {
                        Task { await move(to: stage.key) }
                    } label: {
                        if stage.key == client.pipelineStage { Label(stage.label, systemImage: "checkmark") }
                        else { Text(stage.label) }
                    }
                }
            } label: {
                HStack(spacing: 4) {
                    StatusBadge(text: appState.metadata.stageLabel(client.pipelineStage),
                                color: Theme.Colors.stage(client.pipelineStage ?? ""))
                    Image(systemName: "chevron.down").font(.caption2)
                        .foregroundStyle(Theme.Colors.textSecondary)
                }
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func actions(_ client: Client) -> some View {
        HStack(spacing: Theme.Spacing.md) {
            actionButton("WhatsApp", icon: "message.fill", enabled: !(client.phone ?? "").isEmpty) { openChat = true }
            actionButton("Llamar", icon: "phone.fill", enabled: !(client.phone ?? "").isEmpty) {
                if let url = URL(string: "tel:\(Formatters.e164(client.phone ?? ""))") { UIApplication.shared.open(url) }
            }
            actionButton("Email", icon: "envelope.fill", enabled: !(client.email ?? "").isEmpty) {
                if let url = URL(string: "mailto:\(client.email ?? "")") { UIApplication.shared.open(url) }
            }
            actionButton("Contrato", icon: "signature", enabled: true) { showContract = true }
        }
    }

    private func actionButton(_ title: String, icon: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Image(systemName: icon).font(.title3)
                Text(title).font(.caption)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, Theme.Spacing.md)
            .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: Theme.Radius.md))
        }
        .buttonStyle(.plain)
        .foregroundStyle(enabled ? Theme.Colors.accent : Theme.Colors.textSecondary.opacity(0.5))
        .disabled(!enabled)
    }

    private func finances(_ client: Client) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            HStack {
                money("Total", client.totalCost, Theme.Colors.textPrimary)
                money("Pagado", client.amountPaid, Theme.Colors.success)
                money("Pendiente", client.balance, client.balance > 0 ? Theme.Colors.coral : Theme.Colors.textSecondary)
            }
            if client.totalCost > 0 {
                ProgressView(value: min(client.amountPaid / client.totalCost, 1))
                    .tint(Theme.Colors.success)
            }
        }
        .card()
    }

    private func money(_ label: String, _ value: Double, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(Theme.Colors.textSecondary)
            MoneyText(amount: value, color: color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func infoCard(_ client: Client) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            InfoRow(icon: "phone", label: "Teléfono", value: Formatters.phone(client.phone ?? ""))
            InfoRow(icon: "envelope", label: "Email", value: client.email ?? "")
            InfoRow(icon: "square.stack.3d.up", label: "Proyecto", value: appState.metadata.projectLabel(client.projectType))
            if let details = client.projectDetails, !details.isEmpty {
                InfoRow(icon: "text.alignleft", label: "Detalles", value: details)
            }
        }
        .card()
    }

    private var contractsCard: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            sectionHeader("Contratos", action: "Nuevo") { showContract = true }
            if contracts.isEmpty {
                Text("Sin contratos todavía.").font(.callout).foregroundStyle(Theme.Colors.textSecondary)
            }
            ForEach(contracts) { contract in
                NavigationLink {
                    ContractDetailView(contractId: contract.id)
                } label: {
                    ContractRow(contract: contract)
                }
                .buttonStyle(.plain)
            }
        }
        .card()
    }

    private var followUpsCard: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            sectionHeader("Seguimientos", action: "Agregar") { showFollowUp = true }
            if followups.isEmpty {
                Text("Sin seguimientos.").font(.callout).foregroundStyle(Theme.Colors.textSecondary)
            }
            ForEach(followups.prefix(6)) { item in
                HStack(alignment: .top) {
                    Image(systemName: item.completed ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(item.completed ? Theme.Colors.success : Theme.Colors.textSecondary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.summary ?? "").font(.callout)
                        if !item.dueText.isEmpty {
                            Text(Formatters.reminder(item.dueText)).font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                        }
                    }
                }
            }
        }
        .card()
    }

    private var notesCard: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            Text("Historial").font(.headline)
            ForEach(notes.prefix(8)) { note in
                VStack(alignment: .leading, spacing: 2) {
                    Text(note.content ?? "").font(.callout).lineLimit(4)
                    Text(Formatters.dateTime(note.createdAt)).font(.caption2).foregroundStyle(Theme.Colors.textSecondary)
                }
                Divider()
            }
        }
        .card()
    }

    private func sectionHeader(_ title: String, action: String, perform: @escaping () -> Void) -> some View {
        HStack {
            Text(title).font(.headline)
            Spacer()
            Button(action: perform) { Label(action, systemImage: "plus") }
                .font(.subheadline)
        }
    }

    // MARK: Datos

    private func load() async {
        do {
            let res = try await APIClient.shared.client(clientId)
            client = res.client
            notes = res.notes
            followups = res.followups
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        await loadContracts()
    }

    private func loadContracts() async {
        if let res = try? await APIClient.shared.contracts(clientId: clientId) { contracts = res.contracts }
    }

    private func move(to stage: String) async {
        do {
            try await APIClient.shared.move(clientId: clientId, to: stage)
            client?.pipelineStage = stage
            if let client { onUpdate?(client) }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Nuevo seguimiento

struct FollowUpFormView: View {
    let clientId: Int
    var onSaved: () -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState
    @State private var summary = ""
    @State private var method = "whatsapp"
    @State private var reminder = Date().addingTimeInterval(24 * 3600)
    @State private var error: String?
    @State private var saving = false

    private var methods: [Option] {
        appState.metadata.followUpMethods.isEmpty
            ? [Option(key: "whatsapp", label: "WhatsApp"), Option(key: "phone", label: "Teléfono"),
               Option(key: "email", label: "Email"), Option(key: "other", label: "Otro")]
            : appState.metadata.followUpMethods
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("¿Qué hay que hacer?", text: $summary, axis: .vertical).lineLimit(2...4)
                Picker("Medio", selection: $method) {
                    ForEach(methods) { Text($0.label).tag($0.key) }
                }
                DatePicker("Recordatorio", selection: $reminder)
                if let error { ErrorBanner(message: error) }
            }
            .navigationTitle("Seguimiento")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Guardar") { Task { await save() } }
                        .disabled(summary.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func save() async {
        saving = true
        defer { saving = false }
        do {
            try await APIClient.shared.addFollowUp(clientId: clientId, method: method, summary: summary, reminderAt: reminder)
            onSaved()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        ClientDetailView(preview: PreviewData.clients[0])
    }
    .environment(AppState.preview)
}
#endif
