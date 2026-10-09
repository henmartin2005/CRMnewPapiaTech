import Combine
import SwiftUI

struct ConversationListView: View {
    @Environment(AppState.self) private var appState

    @State private var conversations: [Conversation]
    @State private var search = ""
    @State private var filter: Filter = .all
    @State private var path: [String] = []
    @State private var error: String?
    @State private var showNew = false
    @State private var loaded: Bool

    enum Filter: String, CaseIterable { case all = "Todos", unread = "No leídos", clients = "Clientes", unknown = "Sin registrar" }

    init(preview: [Conversation]? = nil) {
        _conversations = State(initialValue: preview ?? [])
        _loaded = State(initialValue: preview != nil)
    }

    private var filtered: [Conversation] {
        conversations.filter { c in
            let matchesFilter: Bool
            switch filter {
            case .all: matchesFilter = true
            case .unread: matchesFilter = c.unread > 0
            case .clients: matchesFilter = c.clientId != nil
            case .unknown: matchesFilter = c.clientId == nil
            }
            guard matchesFilter else { return false }
            guard !search.isEmpty else { return true }
            return c.title.localizedCaseInsensitiveContains(search)
                || c.phone.contains(search.filter(\.isNumber))
                || c.lastMessage.localizedCaseInsensitiveContains(search)
        }
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if let error {
                    ErrorBanner(message: error) { self.error = nil }
                        .listRowSeparator(.hidden)
                }
                Section {
                    ChipBar(items: Filter.allCases, selection: $filter, title: { $0.rawValue })
                        .listRowInsets(EdgeInsets())
                        .listRowSeparator(.hidden)
                }
                ForEach(filtered) { conversation in
                    NavigationLink(value: conversation.phone) {
                        ConversationRow(conversation: conversation)
                    }
                }
            }
            .listStyle(.plain)
            .overlay {
                if loaded && filtered.isEmpty {
                    ContentUnavailableView(search.isEmpty ? "Sin conversaciones" : "Sin resultados",
                                           systemImage: "message",
                                           description: Text(search.isEmpty ? "Los WhatsApp que entren aparecerán aquí." : ""))
                } else if !loaded {
                    ProgressView()
                }
            }
            .searchable(text: $search, prompt: "Buscar chat o número")
            .navigationTitle("WhatsApp")
            .navigationDestination(for: String.self) { phone in
                ChatView(phone: phone, title: conversations.first { $0.phone == phone }?.title)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNew = true } label: { Image(systemName: "square.and.pencil") }
                }
            }
            .sheet(isPresented: $showNew) {
                NewConversationSheet { phone in
                    showNew = false
                    path = [phone]
                }
            }
            .refreshable { await load() }
            .task { if !loaded { await load() } }
            .task(id: path.isEmpty) {
                // Refresco periódico mientras la lista está visible
                while path.isEmpty && !Task.isCancelled && loaded {
                    try? await Task.sleep(for: .seconds(15))
                    if path.isEmpty { await load() }
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .whatsappPushReceived)) { _ in
                Task { await load() }
            }
            .onChange(of: appState.pendingChatPhone) { _, phone in openPending(phone) }
            .onAppear { openPending(appState.pendingChatPhone) }
            .onChange(of: path) { _, newPath in
                if newPath.isEmpty { Task { await load() } }
            }
        }
    }

    private func openPending(_ phone: String?) {
        guard let phone else { return }
        appState.pendingChatPhone = nil
        path = [phone]
    }

    private func load() async {
        do {
            let res = try await APIClient.shared.conversations()
            conversations = res.conversations
            appState.whatsappUnread = res.unread
            UNUserNotificationCenterBadge.set(res.unread)
            error = nil
        } catch {
            if conversations.isEmpty { self.error = error.localizedDescription }
        }
        loaded = true
    }
}

struct ConversationRow: View {
    let conversation: Conversation

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            AvatarView(name: conversation.clientName.isEmpty ? "#" : conversation.clientName, size: 50)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(conversation.title)
                        .font(.headline)
                        .lineLimit(1)
                    Spacer()
                    Text(Formatters.chatListTime(conversation.lastAt))
                        .font(.caption)
                        .foregroundStyle(conversation.unread > 0 ? Theme.Colors.success : Theme.Colors.textSecondary)
                }
                HStack(spacing: 4) {
                    if conversation.lastDirection == "outbound" {
                        StatusTicks(status: conversation.lastStatus)
                    }
                    Text(conversation.lastMessage)
                        .font(.subheadline)
                        .foregroundStyle(Theme.Colors.textSecondary)
                        .lineLimit(2)
                    Spacer()
                    if conversation.unread > 0 {
                        Text("\(conversation.unread)")
                            .font(.caption2.bold())
                            .foregroundStyle(.white)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(Theme.Colors.success, in: Capsule())
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }
}

/// ✓ enviado · ✓✓ entregado · ✓✓ azul leído · ! falló
struct StatusTicks: View {
    let status: String

    var body: some View {
        switch status {
        case "failed":
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(Theme.Colors.danger)
        case "read":
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.Colors.aqua)
        case "delivered":
            Image(systemName: "checkmark.circle").foregroundStyle(Theme.Colors.textSecondary)
        case "pending":
            Image(systemName: "clock").foregroundStyle(Theme.Colors.textSecondary)
        default:
            Image(systemName: "checkmark").foregroundStyle(Theme.Colors.textSecondary)
        }
    }
}

#if DEBUG
#Preview {
    ConversationListView(preview: PreviewData.conversations)
        .environment(AppState.preview)
}
#endif
