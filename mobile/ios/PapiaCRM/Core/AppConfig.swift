import Foundation

/// Configuración de la app.
///
/// El servidor se puede cambiar en la pantalla de login (por ejemplo, para
/// usar la misma app con otra instancia del CRM como Maybis CRM).
enum AppConfig {
    static let defaultServer = "https://datos.papiatech.com"

    private static let serverKey = "server_url"

    static var serverURL: URL {
        get {
            let raw = UserDefaults.standard.string(forKey: serverKey) ?? defaultServer
            return URL(string: raw) ?? URL(string: defaultServer)!
        }
        set {
            UserDefaults.standard.set(newValue.absoluteString, forKey: serverKey)
        }
    }

    /// Normaliza lo que escribe el usuario: "datos.papiatech.com" → "https://datos.papiatech.com".
    static func normalizedServer(_ input: String) -> URL? {
        var text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        while text.hasSuffix("/") { text.removeLast() }
        guard !text.isEmpty else { return nil }
        if !text.lowercased().hasPrefix("http") { text = "https://" + text }
        guard let url = URL(string: text), url.host != nil else { return nil }
        return url
    }

    /// Ambiente de APNs que corresponde a este build.
    static var pushEnvironment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }

    static var appVersion: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = info?["CFBundleVersion"] as? String ?? "1"
        return "\(version) (\(build))"
    }
}
