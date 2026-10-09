import Foundation
import Observation
import UIKit
import UserNotifications

/// Estado global: sesión, catálogos del CRM, badge de WhatsApp y navegación desde un push.
@MainActor
@Observable
final class AppState {
    enum Phase { case launching, loggedOut, loggedIn }
    enum Tab: Hashable { case whatsapp, clients, pipeline, contracts, more }

    var phase: Phase = .launching
    var user: User?
    var metadata = Metadata.fallback
    var whatsappUnread = 0
    var selectedTab: Tab = .whatsapp

    /// Teléfono del chat a abrir (al tocar una notificación).
    var pendingChatPhone: String?

    private let api = APIClient.shared

    init() {
        NotificationCenter.default.addObserver(forName: .sessionExpired, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.logout(remote: false) }
        }
    }

    // MARK: Sesión

    func restoreSession() async {
        guard api.token != nil else { phase = .loggedOut; return }
        do {
            user = try await api.me()
            phase = .loggedIn
            await afterLogin()
        } catch APIError.unauthorized {
            phase = .loggedOut
        } catch {
            // Sin conexión: se entra igual con el token guardado y se reintenta luego.
            phase = .loggedIn
        }
    }

    func login(server: URL, username: String, password: String) async throws {
        AppConfig.serverURL = server
        user = try await api.login(username: username, password: password)
        phase = .loggedIn
        await afterLogin()
    }

    func logout(remote: Bool = true) {
        let deviceToken = PushManager.shared.deviceToken
        if remote, let deviceToken {
            Task { try? await api.unregisterDevice(token: deviceToken) }
        }
        api.token = nil
        user = nil
        whatsappUnread = 0
        UNUserNotificationCenterBadge.set(0)
        phase = .loggedOut
    }

    private func afterLogin() async {
        if let meta = try? await api.metadata() { metadata = meta }
        await refreshBadge()
        await PushManager.shared.requestAuthorizationAndRegister()
    }

    // MARK: WhatsApp

    func refreshBadge() async {
        guard let res = try? await api.conversations() else { return }
        whatsappUnread = res.unread
        UNUserNotificationCenterBadge.set(res.unread)
    }

    func openChat(phone: String) {
        selectedTab = .whatsapp
        pendingChatPhone = phone
    }
}

/// Pequeño envoltorio para el número del ícono de la app.
enum UNUserNotificationCenterBadge {
    static func set(_ value: Int) {
        UNUserNotificationCenter.current().setBadgeCount(value) { _ in }
    }
}
