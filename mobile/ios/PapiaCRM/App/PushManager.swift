import UIKit
import UserNotifications

/// Notificaciones push de WhatsApp.
///
/// Flujo: pedir permiso → iOS entrega el device token → se registra en el CRM
/// (/api/mobile/devices) → el CRM avisa por APNs cuando entra un WhatsApp.
/// Desde la notificación se puede responder sin abrir la app.
@MainActor
final class PushManager: NSObject {
    static let shared = PushManager()

    static let whatsappCategory = "WHATSAPP_MESSAGE"
    static let replyAction = "WHATSAPP_REPLY"

    weak var appState: AppState?
    private(set) var deviceToken: String?

    /// Chat abierto en pantalla: no se muestra el banner de ese mismo contacto.
    var activeChatPhone: String?

    func configure() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let reply = UNTextInputNotificationAction(
            identifier: Self.replyAction, title: "Responder", options: [],
            textInputButtonTitle: "Enviar", textInputPlaceholder: "Mensaje")
        let category = UNNotificationCategory(identifier: Self.whatsappCategory, actions: [reply],
                                              intentIdentifiers: [], options: [])
        center.setNotificationCategories([category])
    }

    func requestAuthorizationAndRegister() async {
        let center = UNUserNotificationCenter.current()
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        guard granted else { return }
        UIApplication.shared.registerForRemoteNotifications()
    }

    func didRegister(deviceToken data: Data) {
        let token = data.map { String(format: "%02x", $0) }.joined()
        deviceToken = token
        guard APIClient.shared.token != nil else { return }
        Task {
            do { try await APIClient.shared.registerDevice(token: token) }
            catch { print("No se pudo registrar el dispositivo para push: \(error)") }
        }
    }
}

extension PushManager: UNUserNotificationCenterDelegate {

    // Push con la app abierta
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        let info = notification.request.content.userInfo
        let phone = info["phone"] as? String
        Task { @MainActor in
            NotificationCenter.default.post(name: .whatsappPushReceived, object: phone)
            await self.appState?.refreshBadge()
            if let phone, phone == self.activeChatPhone {
                completionHandler([])                    // ya estás en ese chat
            } else {
                completionHandler([.banner, .list, .sound])
            }
        }
    }

    // Tocar la notificación o responder desde ella
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse,
                                            withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let phone = info["phone"] as? String
        let clientId = info["client_id"] as? Int
        let replyText = (response as? UNTextInputNotificationResponse)?.userText
        let action = response.actionIdentifier

        Task { @MainActor in
            defer { completionHandler() }
            guard let phone else { return }
            if action == Self.replyAction, let text = replyText?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
                try? await APIClient.shared.sendWhatsApp(phone: phone, message: text, clientId: clientId)
                try? await APIClient.shared.markRead(phone: phone)
                await self.appState?.refreshBadge()
            } else if action == UNNotificationDefaultActionIdentifier {
                self.appState?.openChat(phone: phone)
            }
        }
    }
}

/// Puente con UIKit para recibir el device token.
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        MainActor.assumeIsolated { PushManager.shared.configure() }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        MainActor.assumeIsolated { PushManager.shared.didRegister(deviceToken: deviceToken) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        print("APNs no disponible: \(error.localizedDescription)")
    }
}
