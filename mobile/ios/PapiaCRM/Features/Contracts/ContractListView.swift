import SwiftUI

struct ContractListView: View {
    @State private var contracts: [Contract]
    @State private var stats: ContractStats?
    @State private var tab = "all"
    @State private var search = ""
    @State private var loaded: Bool
    @State private var error: String?
    @State private var showNew = false
    @State private var path: [Int] = []

    private let tabs: [Option] = [
        Option(key: "all", label: "Todos"),
        Option(key: "waiting", label: "Esperando"),
        Option(key: "completed", label: "Completados"),
        Option(key: "draft", label: "Borradores"),
        Option(key: "closed", label: "Cerrados"),
    ]

    init(preview: [Contract]? = nil) {
        _contracts = State(initialValue: preview ?? [])
        _loaded = State(initialValue: preview != nil)
        _stats = State(initialValue: preview == nil ? nil : ContractStats(waiting: 1, drafts: 1, completed30: 4, expiring: 0))
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if let stats { statsRow(stats).listRowSeparator(.hidden) }
                Section {
                    ChipBar(items: tabs.map(\.key), selection: $tab,
                            title: { key in tabs.first { $0.key == key }?.label ?? key })
                        .listRowInsets(EdgeInsets())
                        .listRowSeparator(.hidden)
                }
                if let error {
                    ErrorBanner(message: error) { self.error = nil }.listRowSeparator(.hidden)
                }
                ForEach(contracts) { contract in
                    NavigationLink(value: contract.id) { ContractRow(contract: contract) }
                }
            }
            .listStyle(.plain)
            .overlay {
                if !loaded { ProgressView() }
                else if contracts.isEmpty {
                    ContentUnavailableView("Sin contratos", systemImage: "signature",
                                           description: Text("Envía uno con + para que lo firmen desde su teléfono."))
                }
            }
            .searchable(text: $search, prompt: "Título, cliente o ID")
            .onSubmit(of: .search) { Task { await load() } }
            .onChange(of: search) { _, value in if value.isEmpty { Task { await load() } } }
            .onChange(of: tab) { _, _ in Task { await load() } }
            .navigationTitle("Contratos")
            .navigationDestination(for: Int.self) { id in ContractDetailView(contractId: id) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNew = true } label: { Image(systemName: "plus") }
                }
            }
            .sheet(isPresented: $showNew) {
                NewContractView { contract in
                    Task { await load() }
                    path = [contract.id]
                }
            }
            .refreshable { await load() }
            .task { if !loaded { await load() } }
            .onChange(of: path) { _, newPath in if newPath.isEmpty { Task { await load() } } }
        }
    }

    private func statsRow(_ stats: ContractStats) -> some View {
        HStack(spacing: Theme.Spacing.sm) {
            stat("\(stats.waiting)", "Esperando", Theme.Colors.warning)
            stat("\(stats.completed30)", "Firmados 30d", Theme.Colors.success)
            stat("\(stats.drafts)", "Borradores", Theme.Colors.textSecondary)
            stat("\(stats.expiring)", "Por vencer", Theme.Colors.danger)
        }
    }

    private func stat(_ value: String, _ label: String, _ color: Color) -> some View {
        VStack(spacing: 2) {
            Text(value).font(.title3.bold()).foregroundStyle(color)
            Text(label).font(.caption2).foregroundStyle(Theme.Colors.textSecondary).lineLimit(1).minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Theme.Spacing.sm)
        .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: Theme.Radius.sm))
    }

    private func load() async {
        do {
            let res = try await APIClient.shared.contracts(tab: tab, search: search.isEmpty ? nil : search)
            contracts = res.contracts
            stats = res.stats
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loaded = true
    }
}

struct ContractRow: View {
    let contract: Contract

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            ZStack {
                RoundedRectangle(cornerRadius: Theme.Radius.sm)
                    .fill(Theme.Colors.contract(contract.status).opacity(0.12))
                    .frame(width: 44, height: 52)
                Image(systemName: icon).foregroundStyle(Theme.Colors.contract(contract.status))
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(contract.title).font(.headline).lineLimit(2)
                if !contract.clientName.isEmpty {
                    Text(contract.clientName).font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                }
                HStack(spacing: 6) {
                    StatusBadge(text: contract.statusLabel, color: Theme.Colors.contract(contract.status))
                    if contract.signerTotal > 0 && contract.status == "sent" {
                        Text("\(contract.signerDone)/\(contract.signerTotal) firmas")
                            .font(.caption2).foregroundStyle(Theme.Colors.textSecondary)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
    }

    private var icon: String {
        switch contract.status {
        case "completed": return "checkmark.seal.fill"
        case "sent": return "hourglass"
        case "draft": return "doc"
        default: return "xmark.octagon"
        }
    }
}

#if DEBUG
#Preview {
    ContractListView(preview: PreviewData.contracts)
        .environment(AppState.preview)
}
#endif
