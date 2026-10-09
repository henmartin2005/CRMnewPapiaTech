import SwiftUI

/// Pipeline en el iPhone: una etapa a la vez (chips arriba) con sus tarjetas.
/// Para mover un cliente: mantener presionada la tarjeta → "Mover a…", o deslizar.
struct PipelineView: View {
    @Environment(AppState.self) private var appState

    @State private var stages: [PipelineStage]
    @State private var selected: String
    @State private var loaded: Bool
    @State private var error: String?
    @State private var showNew = false
    @State private var path: [Int] = []

    init(preview: [PipelineStage]? = nil) {
        _stages = State(initialValue: preview ?? [])
        _selected = State(initialValue: preview?.first?.key ?? "new_lead")
        _loaded = State(initialValue: preview != nil)
    }

    private var current: PipelineStage? { stages.first { $0.key == selected } }

    var body: some View {
        NavigationStack(path: $path) {
            VStack(spacing: 0) {
                ChipBar(items: stages.map(\.key), selection: $selected,
                        title: { key in
                            let stage = stages.first { $0.key == key }
                            return "\(stage?.label ?? key) · \(stage?.clients.count ?? 0)"
                        },
                        color: { Theme.Colors.stage($0) })
                if let current {
                    summary(current)
                }
                List {
                    if let error {
                        ErrorBanner(message: error) { self.error = nil }.listRowSeparator(.hidden)
                    }
                    ForEach(current?.clients ?? []) { client in
                        NavigationLink(value: client.id) {
                            PipelineCard(client: client)
                        }
                        .contextMenu { moveMenu(client) }
                        .swipeActions(edge: .trailing) {
                            if let next = nextStage(after: client.pipelineStage) {
                                Button {
                                    Task { await move(client, to: next.key) }
                                } label: {
                                    Label(next.label, systemImage: "arrow.right")
                                }
                                .tint(Theme.Colors.stage(next.key))
                            }
                        }
                        .swipeActions(edge: .leading) {
                            if let previous = previousStage(before: client.pipelineStage) {
                                Button {
                                    Task { await move(client, to: previous.key) }
                                } label: {
                                    Label(previous.label, systemImage: "arrow.left")
                                }
                                .tint(Theme.Colors.stage(previous.key))
                            }
                        }
                    }
                }
                .listStyle(.plain)
                .overlay {
                    if !loaded { ProgressView() }
                    else if (current?.clients ?? []).isEmpty {
                        ContentUnavailableView("Etapa vacía", systemImage: "tray",
                                               description: Text("Desliza una tarjeta o mantenla presionada para moverla de etapa."))
                    }
                }
            }
            .navigationTitle("Pipeline")
            .navigationDestination(for: Int.self) { id in
                ClientDetailView(clientId: id) { _ in Task { await load() } }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNew = true } label: { Image(systemName: "plus") }
                }
            }
            .sheet(isPresented: $showNew) {
                ClientFormView(mode: .create) { _ in Task { await load() } }
            }
            .refreshable { await load() }
            .task { if !loaded { await load() } }
            .onChange(of: path) { _, newPath in if newPath.isEmpty { Task { await load() } } }
        }
    }

    private func summary(_ stage: PipelineStage) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Valor").font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                MoneyText(amount: stage.totalValue)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text("Por cobrar").font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                MoneyText(amount: stage.totalPending, color: stage.totalPending > 0 ? Theme.Colors.coral : Theme.Colors.textSecondary)
            }
        }
        .padding(.horizontal, Theme.Spacing.lg)
        .padding(.vertical, Theme.Spacing.sm)
        .background(Theme.Colors.stage(stage.key).opacity(0.08))
    }

    @ViewBuilder
    private func moveMenu(_ client: Client) -> some View {
        Section("Mover a…") {
            ForEach(stages.filter { $0.key != client.pipelineStage }) { stage in
                Button(stage.label) { Task { await move(client, to: stage.key) } }
            }
        }
    }

    private func nextStage(after key: String?) -> PipelineStage? {
        guard let index = stages.firstIndex(where: { $0.key == key }), index + 1 < stages.count else { return nil }
        return stages[index + 1]
    }

    private func previousStage(before key: String?) -> PipelineStage? {
        guard let index = stages.firstIndex(where: { $0.key == key }), index > 0 else { return nil }
        return stages[index - 1]
    }

    private func move(_ client: Client, to key: String) async {
        // Movimiento optimista; si falla, se revierte con la recarga.
        var updated = client
        updated.pipelineStage = key
        withAnimation {
            for i in stages.indices { stages[i].clients.removeAll { $0.id == client.id } }
            if let target = stages.firstIndex(where: { $0.key == key }) { stages[target].clients.insert(updated, at: 0) }
        }
        do {
            try await APIClient.shared.move(clientId: client.id, to: key)
        } catch {
            self.error = error.localizedDescription
            await load()
        }
    }

    private func load() async {
        do {
            stages = try await APIClient.shared.pipeline()
            if !stages.contains(where: { $0.key == selected }), let first = stages.first { selected = first.key }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loaded = true
    }
}

struct PipelineCard: View {
    @Environment(AppState.self) private var appState
    let client: Client

    var body: some View {
        HStack(alignment: .top, spacing: Theme.Spacing.md) {
            AvatarView(name: client.fullName, size: 40)
            VStack(alignment: .leading, spacing: 4) {
                Text(client.fullName).font(.headline).lineLimit(1)
                if let company = client.company, !company.isEmpty {
                    Text(company).font(.caption).foregroundStyle(Theme.Colors.textSecondary).lineLimit(1)
                }
                HStack(spacing: 6) {
                    StatusBadge(text: appState.metadata.projectLabel(client.projectType), color: Theme.Colors.navy)
                    if let updated = client.updatedAt {
                        Text(Formatters.relative(updated)).font(.caption2).foregroundStyle(Theme.Colors.textSecondary)
                    }
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                if client.totalCost > 0 { MoneyText(amount: client.totalCost) }
                if client.balance > 0 {
                    Text("Debe \(Formatters.money(client.balance))").font(.caption2).foregroundStyle(Theme.Colors.coral)
                } else if client.totalCost > 0 {
                    Text("Pagado").font(.caption2).foregroundStyle(Theme.Colors.success)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

#if DEBUG
#Preview {
    PipelineView(preview: PreviewData.stages)
        .environment(AppState.preview)
}
#endif
