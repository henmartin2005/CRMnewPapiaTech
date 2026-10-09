import SwiftUI

/// Sistema de diseño de la app.
///
/// Todo el look de la app sale de aquí. Para rediseñar:
///  • Colores: Assets.xcassets (cada color tiene versión clara y oscura) o los valores de abajo.
///  • Tipografía, espacios y radios: las constantes de este archivo.
///  • Componentes reutilizables: Components.swift.
/// Cada pantalla tiene su #Preview con datos de ejemplo, así que puedes diseñar
/// en el Canvas de Xcode (⌥⌘↩) sin conectarte al servidor.
enum Theme {

    // MARK: Colores (definidos en Assets.xcassets)
    enum Colors {
        static let navy = Color("BrandNavy")
        static let aqua = Color("BrandAqua")
        static let coral = Color("BrandCoral")
        static let accent = Color.accentColor

        static let surface = Color("Surface")
        static let surfaceAlt = Color("SurfaceAlt")
        static let border = Color("Border")
        static let textPrimary = Color("TextPrimary")
        static let textSecondary = Color("TextSecondary")

        static let bubbleOutgoing = Color("BubbleOutgoing")
        static let bubbleIncoming = Color("BubbleIncoming")
        static let chatBackground = Color("ChatBackground")

        static let success = Color("Success")
        static let danger = Color("Danger")
        static let warning = Color("Warning")

        /// Colores de los avatares (se elige uno según el nombre).
        static let avatarPalette: [Color] = [
            Color(red: 0.00, green: 0.50, blue: 0.54),
            Color(red: 0.55, green: 0.36, blue: 0.96),
            Color(red: 1.00, green: 0.42, blue: 0.29),
            Color(red: 0.96, green: 0.62, blue: 0.04),
            Color(red: 0.06, green: 0.73, blue: 0.51),
            Color(red: 0.93, green: 0.28, blue: 0.60),
            Color(red: 0.16, green: 0.36, blue: 1.00),
        ]

        /// Color de cada etapa del pipeline (clave del CRM → color).
        static func stage(_ key: String) -> Color {
            switch key {
            case "new_lead": return Color(red: 0.16, green: 0.36, blue: 1.00)
            case "contacted": return Color(red: 0.55, green: 0.36, blue: 0.96)
            case "proposal_sent": return Color(red: 0.96, green: 0.62, blue: 0.04)
            case "negotiation": return Color(red: 1.00, green: 0.42, blue: 0.29)
            case "active_client": return Color(red: 0.06, green: 0.73, blue: 0.51)
            case "recurring": return Color(red: 0.00, green: 0.50, blue: 0.54)
            default: return textSecondary
            }
        }

        /// Color del estado de un contrato.
        static func contract(_ status: String) -> Color {
            switch status {
            case "draft": return textSecondary
            case "sent": return warning
            case "completed": return success
            case "declined", "voided", "expired": return danger
            default: return textSecondary
            }
        }
    }

    // MARK: Tipografía
    enum Fonts {
        static let largeTitle = Font.system(.largeTitle, design: .rounded).weight(.bold)
        static let title = Font.system(.title2, design: .rounded).weight(.semibold)
        static let headline = Font.system(.headline, design: .default)
        static let body = Font.system(.body)
        static let callout = Font.system(.callout)
        static let caption = Font.system(.caption)
        static let money = Font.system(.headline, design: .rounded).monospacedDigit()
    }

    // MARK: Espacios y formas
    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
    }

    enum Radius {
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 18
        static let bubble: CGFloat = 16
    }
}
