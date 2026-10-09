import SwiftUI

@main
struct PapiaCRMApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var appState = AppState()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .tint(Theme.Colors.accent)
                .task {
                    PushManager.shared.appState = appState
                    await appState.restoreSession()
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active, appState.phase == .loggedIn {
                        Task { await appState.refreshBadge() }
                    }
                }
        }
    }
}

struct RootView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        switch appState.phase {
        case .launching:
            ZStack {
                Theme.Colors.navy.ignoresSafeArea()
                ProgressView().tint(.white)
            }
        case .loggedOut:
            LoginView()
        case .loggedIn:
            MainTabView()
        }
    }
}

struct MainTabView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var appState = appState
        TabView(selection: $appState.selectedTab) {
            ConversationListView()
                .tabItem { Label("WhatsApp", systemImage: "message.fill") }
                .badge(appState.whatsappUnread)
                .tag(AppState.Tab.whatsapp)

            ClientListView()
                .tabItem { Label("Clientes", systemImage: "person.2.fill") }
                .tag(AppState.Tab.clients)

            PipelineView()
                .tabItem { Label("Pipeline", systemImage: "rectangle.split.3x1.fill") }
                .tag(AppState.Tab.pipeline)

            ContractListView()
                .tabItem { Label("Contratos", systemImage: "signature") }
                .tag(AppState.Tab.contracts)

            MoreView()
                .tabItem { Label("Más", systemImage: "ellipsis.circle.fill") }
                .tag(AppState.Tab.more)
        }
    }
}
