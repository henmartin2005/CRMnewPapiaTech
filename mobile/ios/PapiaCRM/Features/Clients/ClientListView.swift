import SwiftUI

struct ClientListView: View {
    @Environment(AppState.self) private var appState

    @State private var clients: [Client]
    @State private var search = ""
    @State private var stageFilter = "all"
    @State private var loaded: Bool
    @State private var error: String?
    @State private var showNew = false
    @State private var path: [Int] = []

    init(preview: [Client]? = nil) {
        _clients = State(initialValue: preview ?? [])
        _loaded = State(initialValue: preview != nil)
    }

    private var filtered: [Client] {
        clients.filter { client in
            (stageFilter == "all" || client.pipelineStage == stageFilter)
                && (search.isEmpty
                    || client.fullName.localizedCaseInsensitiveContains(search)
                    || (client.company ?? "").localizedCaseInsensitiveContains(search)
                    || (client.email ?? "").localizedCaseInsensitiveContains(search)
                    || (client.phone ?? "").contains(search))
        }
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if let error {
                    ErrorBanner(message: error) { self.error = nil }.listRowSeparator(.hidden)
                }
                Section {
                    ChipBar(items: ["all"] + appState.metadata.pipelineStages.map(\.key),
                            selection: $stageFilter,
                            title: { $0 == "all" ? "Todos" : appState.metadata.stageLabel($0) },
                            color: { $0 == "all" ? Theme.Colors.accent : Theme.Colors.stage($0) })
                        .listRowInsets(EdgeInsets())
                        .listRowSeparator(.hidden)
                }
                ForEach(filtered) { client in
                    NavigationLink(value: client.id) {
                        ClientRow(client: client)
                    }
                }
            }
            .listStyle(.plain)
            .overlay {
                if !loaded { ProgressView() }
                else if filtered.isEmpty {
                    ContentUnavailableView("Sin clientes", systemImage: "person.2",
                                           description: Text(search.isEmpty ? "Crea el primero con +" : "No hay coincidencias."))
                }
            }
            .searchable(text: $search, prompt: "Nombre, empresa, email o teléfono")
            .navigationTitle("Clientes")
            .navigationDestination(for: Int.self) { id in
                ClientDetailView(clientId: id) { updated in replace(updated) }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNew = true } label: { Image(systemName: "plus") }
                }
            }
            .sheet(isPresented: $showNew) {
                ClientFormView(mode: .create) { created in
                    clients.insert(created, at: 0)
                    path = [created.id]
                }
            }
            .refreshable { await load() }
            .task { if !loaded { await load() } }
        }
    }

    private func replace(_ client: Client) {
        if let index = clients.firstIndex(where: { $0.id == client.id }) { clients[index] = client }
    }

    private func load() async {
        do {
            clients = try await APIClient.shared.clients()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loaded = true
    }
}

struct ClientRow: View {
    @Environment(AppState.self) private var appState
    let client: Client

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            AvatarView(name: client.fullName)
            VStack(alignment: .leading, spacing: 3) {
                Text(client.fullName).font(.headline).lineLimit(1)
                if let company = client.company, !company.isEmpty {
                    Text(company).font(.subheadline).foregroundStyle(Theme.Colors.textSecondary).lineLimit(1)
                }
                StatusBadge(text: appState.metadata.stageLabel(client.pipelineStage),
                            color: Theme.Colors.stage(client.pipelineStage ?? ""))
            }
            Spacer()
            if client.totalCost > 0 {
                VStack(alignment: .trailing, spacing: 2) {
                    MoneyText(amount: client.totalCost)
                    if client.balance > 0 {
                        Text("Pendiente \(Formatters.money(client.balance))")
                            .font(.caption2)
                            .foregroundStyle(Theme.Colors.coral)
                    }
                }
            }
        }
        .padding(.vertical, 2)
    }
}

#if DEBUG
#Preview {
    ClientListView(preview: PreviewData.clients)
        .environment(AppState.preview)
}
#endif
