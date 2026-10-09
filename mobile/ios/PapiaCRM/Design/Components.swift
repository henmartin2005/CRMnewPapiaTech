import SwiftUI

// MARK: - Avatar con iniciales

struct AvatarView: View {
    let name: String
    var size: CGFloat = 44

    private var initials: String {
        let parts = name.split(separator: " ").prefix(2)
        let letters = parts.compactMap { $0.first.map(String.init) }.joined()
        return letters.isEmpty ? "#" : letters.uppercased()
    }

    private var color: Color {
        let palette = Theme.Colors.avatarPalette
        let sum = name.unicodeScalars.reduce(0) { $0 + Int($1.value) }
        return palette[sum % palette.count]
    }

    var body: some View {
        Circle()
            .fill(color.gradient)
            .frame(width: size, height: size)
            .overlay(
                Text(initials)
                    .font(.system(size: size * 0.38, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
            )
            .accessibilityHidden(true)
    }
}

// MARK: - Etiqueta de estado

struct StatusBadge: View {
    let text: String
    var color: Color = Theme.Colors.accent

    var body: some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.14), in: Capsule())
    }
}

// MARK: - Tarjeta

struct CardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(Theme.Spacing.lg)
            .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous)
                    .stroke(Theme.Colors.border, lineWidth: 0.5)
            )
    }
}

extension View {
    func card() -> some View { modifier(CardModifier()) }
}

// MARK: - Botón principal

struct PrimaryButtonStyle: ButtonStyle {
    var color: Color = Theme.Colors.accent

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .foregroundStyle(.white)
            .background(color.opacity(configuration.isPressed ? 0.8 : 1),
                        in: RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous))
    }
}

// MARK: - Dinero

struct MoneyText: View {
    let amount: Double
    var color: Color = Theme.Colors.textPrimary

    var body: some View {
        Text(Formatters.money(amount))
            .font(Theme.Fonts.money)
            .foregroundStyle(color)
    }
}

// MARK: - Fila etiqueta / valor

struct InfoRow: View {
    let icon: String
    let label: String
    let value: String

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            Image(systemName: icon)
                .frame(width: 22)
                .foregroundStyle(Theme.Colors.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                Text(value.isEmpty ? "—" : value).font(.body).foregroundStyle(Theme.Colors.textPrimary)
                    .textSelection(.enabled)
            }
            Spacer(minLength: 0)
        }
    }
}

// MARK: - Aviso de error

struct ErrorBanner: View {
    let message: String
    var onClose: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: Theme.Spacing.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text(message).font(.callout)
            Spacer(minLength: 0)
            if let onClose {
                Button(action: onClose) { Image(systemName: "xmark") }
                    .buttonStyle(.plain)
            }
        }
        .foregroundStyle(Theme.Colors.danger)
        .padding(Theme.Spacing.md)
        .background(Theme.Colors.danger.opacity(0.1), in: RoundedRectangle(cornerRadius: Theme.Radius.sm))
    }
}

// MARK: - Chips horizontales (filtros, etapas)

struct ChipBar<Item: Hashable>: View {
    let items: [Item]
    @Binding var selection: Item
    let title: (Item) -> String
    var color: (Item) -> Color = { _ in Theme.Colors.accent }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Theme.Spacing.sm) {
                ForEach(items, id: \.self) { item in
                    let selected = item == selection
                    Button {
                        withAnimation(.snappy) { selection = item }
                    } label: {
                        Text(title(item))
                            .font(.subheadline.weight(selected ? .semibold : .regular))
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .foregroundStyle(selected ? .white : Theme.Colors.textPrimary)
                            .background(selected ? color(item) : Theme.Colors.surface, in: Capsule())
                            .overlay(Capsule().stroke(Theme.Colors.border, lineWidth: selected ? 0 : 0.5))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, Theme.Spacing.lg)
            .padding(.vertical, Theme.Spacing.sm)
        }
    }
}

#if DEBUG
#Preview("Componentes") {
    ScrollView {
        VStack(alignment: .leading, spacing: 16) {
            HStack { AvatarView(name: "Ana Pérez"); AvatarView(name: "Henrry Martin", size: 56) }
            HStack { StatusBadge(text: "Completado", color: Theme.Colors.success); StatusBadge(text: "Esperando firmas", color: Theme.Colors.warning) }
            MoneyText(amount: 1500)
            InfoRow(icon: "envelope", label: "Email", value: "ana@ejemplo.com").card()
            ErrorBanner(message: "No se pudo conectar con el servidor.")
            Button("Guardar") {}.buttonStyle(PrimaryButtonStyle())
        }
        .padding()
    }
    .background(Theme.Colors.surfaceAlt)
}
#endif
