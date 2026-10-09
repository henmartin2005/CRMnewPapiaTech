import SwiftUI
import UserNotifications

struct MoreView: View {
    @Environment(AppState.self) private var appState
    @State private var pushStatus = "…"
    @State private var confirmLogout = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: Theme.Spacing.md) {
                        AvatarView(name: appState.user?.name ?? "?", size: 52)
                        VStack(alignment: .leading) {
                            Text(appState.user?.name ?? "").font(.headline)
                            Text(AppConfig.serverURL.host() ?? "").font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                        }
                    }
                }

                Section("Trabajo") {
                    NavigationLink { TasksView() } label: { Label("Tareas y seguimientos", systemImage: "checklist") }
                    Link(destination: AppConfig.serverURL) {
                        Label("Abrir el CRM web", systemImage: "safari")
                    }
                }

                Section {
                    LabeledContent("Notificaciones", value: pushStatus)
                    Button("Configurar en Ajustes de iOS") {
                        if let url = URL(string: UIApplication.openNotificationSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    }
                } header: {
                    Text("Avisos de WhatsApp")
                } footer: {
                    Text("Recibes un aviso cuando entra un WhatsApp, y puedes responder desde la notificación.")
                }

                Section("Próximamente") {
                    Label("Emails", systemImage: "envelope").foregroundStyle(Theme.Colors.textSecondary)
                    Label("Propuestas", systemImage: "doc.richtext").foregroundStyle(Theme.Colors.textSecondary)
                    Label("Pagos y cronograma", systemImage: "creditcard").foregroundStyle(Theme.Colors.textSecondary)
                    Label("Pizarra", systemImage: "square.on.square").foregroundStyle(Theme.Colors.textSecondary)
                    Label("Instagram y Messenger", systemImage: "bubble.left.and.bubble.right").foregroundStyle(Theme.Colors.textSecondary)
                }

                Section {
                    Button("Cerrar sesión", role: .destructive) { confirmLogout = true }
                } footer: {
                    Text("Papia CRM \(AppConfig.appVersion)")
                }
            }
            .navigationTitle("Más")
            .confirmationDialog("¿Cerrar sesión?", isPresented: $confirmLogout, titleVisibility: .visible) {
                Button("Cerrar sesión", role: .destructive) { appState.logout() }
            }
            .task { await readPushStatus() }
        }
    }

    private func readPushStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral: pushStatus = "Activadas"
        case .denied: pushStatus = "Desactivadas"
        default: pushStatus = "Sin configurar"
        }
    }
}

struct TasksView: View {
    @State private var tasks: [FollowUp]
    @State private var showDone = false
    @State private var loaded: Bool
    @State private var error: String?

    init(preview: [FollowUp]? = nil) {
        _tasks = State(initialValue: preview ?? [])
        _loaded = State(initialValue: preview != nil)
    }

    private var visible: [FollowUp] { tasks.filter { showDone || !$0.completed } }

    var body: some View {
        List {
            if let error { ErrorBanner(message: error).listRowSeparator(.hidden) }
            ForEach(visible) { task in
                HStack(alignment: .top, spacing: Theme.Spacing.md) {
                    Button {
                        Task { await complete(task) }
                    } label: {
                        Image(systemName: task.completed ? "checkmark.circle.fill" : "circle")
                            .font(.title3)
                            .foregroundStyle(task.completed ? Theme.Colors.success : Theme.Colors.textSecondary)
                    }
                    .buttonStyle(.plain)
                    .disabled(task.completed)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(task.summary ?? "").strikethrough(task.completed)
                        HStack {
                            if let name = task.clientName { Text(name) }
                            if !task.dueText.isEmpty { Text("· \(Formatters.reminder(task.dueText))") }
                        }
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.textSecondary)
                    }
                }
            }
        }
        .listStyle(.plain)
        .overlay {
            if !loaded { ProgressView() }
            else if visible.isEmpty { ContentUnavailableView("Todo al día", systemImage: "checkmark.seal") }
        }
        .navigationTitle("Tareas")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Toggle(isOn: $showDone) { Image(systemName: "eye") }.toggleStyle(.button)
            }
        }
        .refreshable { await load() }
        .task { if !loaded { await load() } }
    }

    private func load() async {
        do { tasks = try await APIClient.shared.tasks(); error = nil }
        catch { self.error = error.localizedDescription }
        loaded = true
    }

    private func complete(_ task: FollowUp) async {
        do {
            try await APIClient.shared.completeTask(task.id)
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

#if DEBUG
#Preview("Más") {
    MoreView().environment(AppState.preview)
}

#Preview("Tareas") {
    NavigationStack { TasksView(preview: PreviewData.tasks) }
}
#endif
