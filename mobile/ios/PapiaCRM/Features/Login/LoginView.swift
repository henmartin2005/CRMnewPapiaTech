import SwiftUI

struct LoginView: View {
    @Environment(AppState.self) private var appState

    @State private var username = ""
    @State private var password = ""
    @State private var server = AppConfig.serverURL.absoluteString
    @State private var showServer = false
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        ZStack {
            LinearGradient(colors: [Theme.Colors.navy, Color(red: 0.02, green: 0.10, blue: 0.20)],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()

            ScrollView {
                VStack(spacing: Theme.Spacing.xl) {
                    header
                    form
                }
                .padding(Theme.Spacing.xl)
                .padding(.top, 60)
            }
            .scrollDismissesKeyboard(.interactively)
        }
    }

    private var header: some View {
        VStack(spacing: Theme.Spacing.md) {
            Image("Logo")
                .resizable()
                .frame(width: 84, height: 84)
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                .shadow(color: .black.opacity(0.3), radius: 12, y: 6)
            Text("Papia CRM")
                .font(Theme.Fonts.largeTitle)
                .foregroundStyle(.white)
            Text("Clientes, WhatsApp y contratos en tu iPhone")
                .font(.callout)
                .foregroundStyle(.white.opacity(0.7))
        }
    }

    private var form: some View {
        VStack(spacing: Theme.Spacing.md) {
            field(icon: "person", placeholder: "Usuario", text: $username)
                .textContentType(.username)
            secureField
            if showServer {
                field(icon: "server.rack", placeholder: "Servidor", text: $server)
                    .keyboardType(.URL)
            }
            if let error {
                ErrorBanner(message: error) { self.error = nil }
            }
            Button {
                Task { await login() }
            } label: {
                if loading { ProgressView().tint(.white) } else { Text("Entrar") }
            }
            .buttonStyle(PrimaryButtonStyle(color: Theme.Colors.aqua))
            .disabled(loading || username.isEmpty || password.isEmpty)
            .opacity(username.isEmpty || password.isEmpty ? 0.6 : 1)

            Button(showServer ? "Ocultar servidor" : "Cambiar servidor") {
                withAnimation { showServer.toggle() }
            }
            .font(.footnote)
            .foregroundStyle(.white.opacity(0.7))
        }
        .padding(Theme.Spacing.xl)
        .background(.ultraThinMaterial.opacity(0.35), in: RoundedRectangle(cornerRadius: Theme.Radius.lg))
    }

    private var secureField: some View {
        HStack {
            Image(systemName: "lock").frame(width: 22).foregroundStyle(.white.opacity(0.7))
            SecureField("", text: $password, prompt: Text("Contraseña").foregroundColor(.white.opacity(0.5)))
                .textContentType(.password)
                .foregroundStyle(.white)
                .submitLabel(.go)
                .onSubmit { Task { await login() } }
        }
        .padding(14)
        .background(.white.opacity(0.1), in: RoundedRectangle(cornerRadius: Theme.Radius.md))
    }

    private func field(icon: String, placeholder: String, text: Binding<String>) -> some View {
        HStack {
            Image(systemName: icon).frame(width: 22).foregroundStyle(.white.opacity(0.7))
            TextField("", text: text, prompt: Text(placeholder).foregroundColor(.white.opacity(0.5)))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .foregroundStyle(.white)
        }
        .padding(14)
        .background(.white.opacity(0.1), in: RoundedRectangle(cornerRadius: Theme.Radius.md))
    }

    private func login() async {
        guard !username.isEmpty, !password.isEmpty else { return }
        guard let url = AppConfig.normalizedServer(server) else {
            error = "La dirección del servidor no es válida."
            return
        }
        loading = true
        defer { loading = false }
        do {
            try await appState.login(server: url, username: username.trimmingCharacters(in: .whitespaces),
                                     password: password)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

#if DEBUG
#Preview {
    LoginView()
        .environment(AppState())
}
#endif
