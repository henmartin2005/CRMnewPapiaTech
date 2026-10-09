import Combine
import PhotosUI
import SwiftUI

struct ChatView: View {
    @Environment(AppState.self) private var appState

    let phone: String
    @State private var title: String
    @State private var messages: [ChatMessage]
    @State private var client: Client?
    @State private var bot: BotState?
    @State private var draft = ""
    @State private var sending = false
    @State private var error: String?
    @State private var photoItem: PhotosPickerItem?
    @State private var showTemplates = false
    @State private var showClient = false
    @State private var loaded: Bool
    private let isPreview: Bool

    init(phone: String, title: String? = nil, preview: [ChatMessage]? = nil, previewBot: BotState? = nil) {
        self.phone = phone
        _title = State(initialValue: title ?? Formatters.phone(phone))
        _messages = State(initialValue: preview ?? [])
        _bot = State(initialValue: previewBot)
        _loaded = State(initialValue: preview != nil)
        isPreview = preview != nil
    }

    var body: some View {
        VStack(spacing: 0) {
            if let bot {
                BotBar(bot: bot) { enabled in Task { await setBot(enabled) } }
            }
            messageList
            if let error {
                ErrorBanner(message: error) { self.error = nil }
                    .padding(.horizontal)
                    .padding(.bottom, 4)
            }
            inputBar
        }
        .background(Theme.Colors.chatBackground)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbar }
        .navigationDestination(isPresented: $showClient) {
            if let client { ClientDetailView(clientId: client.id) }
        }
        .sheet(isPresented: $showTemplates) {
            TemplatePickerSheet(phone: phone, clientId: client?.id) {
                showTemplates = false
                Task { await load() }
            }
        }
        .task {
            PushManager.shared.activeChatPhone = phone
            guard !isPreview else { return }
            await load(markRead: true)
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(4))
                if Task.isCancelled { break }
                await load(markRead: true)
            }
        }
        .onDisappear {
            if PushManager.shared.activeChatPhone == phone { PushManager.shared.activeChatPhone = nil }
        }
        .onReceive(NotificationCenter.default.publisher(for: .whatsappPushReceived)) { note in
            if (note.object as? String) == phone { Task { await load(markRead: true) } }
        }
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            Task { await sendPhoto(item) }
        }
    }

    // MARK: Mensajes

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 6) {
                    ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                        if index == 0 || Formatters.dayHeader(messages[index - 1].createdAt) != Formatters.dayHeader(message.createdAt) {
                            Text(Formatters.dayHeader(message.createdAt))
                                .font(.caption.weight(.medium))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 4)
                                .background(Theme.Colors.surface.opacity(0.9), in: Capsule())
                                .foregroundStyle(Theme.Colors.textSecondary)
                                .padding(.vertical, 6)
                        }
                        MessageBubble(message: message)
                            .id(message.id)
                    }
                }
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.vertical, Theme.Spacing.sm)
            }
            .scrollDismissesKeyboard(.interactively)
            .defaultScrollAnchor(.bottom)
            .overlay {
                if !loaded { ProgressView() }
            }
            .onChange(of: messages.last?.id) { _, id in
                guard let id else { return }
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(id, anchor: .bottom) }
            }
        }
    }

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: Theme.Spacing.sm) {
            PhotosPicker(selection: $photoItem, matching: .images) {
                Image(systemName: "photo.on.rectangle")
                    .font(.title3)
                    .frame(width: 36, height: 36)
            }
            TextField("Mensaje", text: $draft, axis: .vertical)
                .lineLimit(1...5)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Theme.Colors.surface, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            Button {
                Task { await sendText() }
            } label: {
                Image(systemName: sending ? "hourglass" : "paperplane.fill")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 38, height: 38)
                    .background(Theme.Colors.accent, in: Circle())
            }
            .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || sending)
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, Theme.Spacing.sm)
        .background(.bar)
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                if client != nil {
                    Button { showClient = true } label: { Label("Ver cliente", systemImage: "person.crop.circle") }
                }
                if let url = URL(string: "tel:\(phone.filter { $0.isNumber || $0 == "+" })") {
                    Link(destination: url) { Label("Llamar", systemImage: "phone") }
                }
                Button { showTemplates = true } label: { Label("Enviar plantilla", systemImage: "doc.text") }
                if let bot {
                    Button {
                        Task { await setBot(bot.status == "off") }
                    } label: {
                        Label(bot.status == "off" ? "Activar asistente IA" : "Apagar asistente IA",
                              systemImage: bot.status == "off" ? "sparkles" : "xmark.circle")
                    }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
        }
    }

    // MARK: Acciones

    private func load(markRead: Bool = false) async {
        let firstLoad = !loaded
        do {
            let res = try await APIClient.shared.messages(phone: phone, markRead: markRead)
            if res.messages != messages.filter({ $0.id > 0 }) || messages.contains(where: { $0.id < 0 }) {
                messages = res.messages
            }
            client = res.client
            bot = res.bot
            if let client { title = client.fullName }
            loaded = true
            if markRead && (firstLoad || res.messages.last?.direction == "inbound") { await appState.refreshBadge() }
        } catch {
            if messages.isEmpty { self.error = error.localizedDescription }
            loaded = true
        }
    }

    private func sendText() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        sending = true
        defer { sending = false }
        let temp = ChatMessage(id: -Int(Date().timeIntervalSince1970), phone: phone, direction: "outbound",
                               message: text, status: "pending", waMessageId: nil,
                               createdAt: Formatters.nowSQLite(), mediaUrl: nil, mediaType: nil)
        messages.append(temp)
        draft = ""
        do {
            try await APIClient.shared.sendWhatsApp(phone: phone, message: text, clientId: client?.id)
            await load()
        } catch APIError.templateRequired(let message) {
            messages.removeAll { $0.id == temp.id }
            draft = text
            error = message
            showTemplates = true
        } catch {
            messages.removeAll { $0.id == temp.id }
            draft = text
            self.error = error.localizedDescription
        }
    }

    private func sendPhoto(_ item: PhotosPickerItem) async {
        defer { photoItem = nil }
        guard let data = try? await item.loadTransferable(type: Data.self),
              let image = UIImage(data: data),
              let jpeg = image.jpegData(compressionQuality: 0.8) else {
            error = "No se pudo leer la imagen."
            return
        }
        sending = true
        defer { sending = false }
        do {
            try await APIClient.shared.sendWhatsAppMedia(phone: phone, data: jpeg, filename: "foto.jpg",
                                                         mimeType: "image/jpeg", kind: "image", clientId: client?.id)
            await load()
        } catch APIError.templateRequired(let message) {
            error = message
            showTemplates = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func setBot(_ enabled: Bool) async {
        do {
            bot = try await APIClient.shared.setBot(phone: phone, enabled: enabled)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Burbuja

struct MessageBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.isOutgoing { Spacer(minLength: 48) }
            VStack(alignment: .trailing, spacing: 4) {
                if let mediaUrl = message.mediaUrl, message.mediaType == "image" || message.mediaType == "sticker" {
                    AuthImage(path: mediaUrl)
                        .frame(maxWidth: 240, maxHeight: 300)
                        .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.sm))
                } else if let mediaType = message.mediaType {
                    Label(mediaLabel(mediaType), systemImage: mediaIcon(mediaType))
                        .font(.callout)
                        .foregroundStyle(Theme.Colors.textSecondary)
                }
                if !message.text.isEmpty && !message.isPlaceholderText {
                    Text(message.text)
                        .font(.body)
                        .foregroundStyle(Theme.Colors.textPrimary)
                        .multilineTextAlignment(.leading)
                        .textSelection(.enabled)
                }
                HStack(spacing: 4) {
                    Text(Formatters.time(message.createdAt))
                    if message.isOutgoing { StatusTicks(status: message.status ?? "sent") }
                }
                .font(.caption2)
                .foregroundStyle(Theme.Colors.textSecondary)
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(message.isOutgoing ? Theme.Colors.bubbleOutgoing : Theme.Colors.bubbleIncoming,
                        in: UnevenRoundedRectangle(
                            topLeadingRadius: Theme.Radius.bubble,
                            bottomLeadingRadius: message.isOutgoing ? Theme.Radius.bubble : 4,
                            bottomTrailingRadius: message.isOutgoing ? 4 : Theme.Radius.bubble,
                            topTrailingRadius: Theme.Radius.bubble,
                            style: .continuous))
            .shadow(color: .black.opacity(0.05), radius: 1, y: 1)
            .opacity(message.status == "pending" ? 0.7 : 1)
            if !message.isOutgoing { Spacer(minLength: 48) }
        }
    }

    private func mediaLabel(_ type: String) -> String {
        switch type {
        case "audio": return "Nota de voz"
        case "video": return "Video"
        case "image": return "Imagen"
        default: return "Archivo"
        }
    }

    private func mediaIcon(_ type: String) -> String {
        switch type {
        case "audio": return "waveform"
        case "video": return "video"
        case "image": return "photo"
        default: return "doc"
        }
    }
}

// MARK: - Barra del asistente IA

struct BotBar: View {
    let bot: BotState
    let onToggle: (Bool) -> Void

    var body: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: bot.status == "on" ? "sparkles" : "pause.circle")
            Text(bot.label).font(.footnote)
            Spacer()
            Toggle("", isOn: Binding(get: { bot.status != "off" }, set: { onToggle($0) }))
                .labelsHidden()
                .controlSize(.mini)
        }
        .foregroundStyle(bot.status == "on" ? Theme.Colors.accent : Theme.Colors.textSecondary)
        .padding(.horizontal, Theme.Spacing.lg)
        .padding(.vertical, 6)
        .background(.bar)
    }
}

// MARK: - Imagen protegida con el token

struct AuthImage: View {
    let path: String
    @State private var image: UIImage?
    @State private var failed = false

    private static let cache = NSCache<NSString, UIImage>()

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image).resizable().scaledToFit()
            } else if failed {
                Image(systemName: "photo").font(.largeTitle).foregroundStyle(Theme.Colors.textSecondary)
                    .frame(width: 160, height: 120)
            } else {
                ProgressView().frame(width: 160, height: 120)
            }
        }
        .task(id: path) {
            if let cached = Self.cache.object(forKey: path as NSString) { image = cached; return }
            do {
                let data = try await APIClient.shared.download(path)
                if let img = UIImage(data: data) {
                    Self.cache.setObject(img, forKey: path as NSString)
                    image = img
                } else { failed = true }
            } catch { failed = true }
        }
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        ChatView(phone: "+17865550101", title: "Ana Pérez", preview: PreviewData.messages,
                 previewBot: BotState(status: "paused", globalEnabled: true))
    }
    .environment(AppState.preview)
}
#endif
