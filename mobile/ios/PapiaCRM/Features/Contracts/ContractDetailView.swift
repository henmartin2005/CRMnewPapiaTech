import PDFKit
import SwiftUI

struct ContractDetailView: View {
    let contractId: Int

    @State private var contract: Contract?
    @State private var error: String?
    @State private var notice: String?
    @State private var busy = false
    @State private var showVoid = false
    @State private var voidReason = ""
    @State private var pdf: PDFFile?
    private let isPreview: Bool

    init(contractId: Int) {
        self.contractId = contractId
        isPreview = false
    }

    #if DEBUG
    init(preview: Contract) {
        contractId = preview.id
        _contract = State(initialValue: preview)
        isPreview = true
    }
    #endif

    var body: some View {
        ScrollView {
            if let contract {
                VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                    header(contract)
                    if let notice {
                        Label(notice, systemImage: "checkmark.circle.fill")
                            .font(.callout).foregroundStyle(Theme.Colors.success)
                    }
                    if let error { ErrorBanner(message: error) { self.error = nil } }
                    documents(contract)
                    recipients(contract)
                    if let events = contract.events, !events.isEmpty { timeline(events) }
                    actions(contract)
                }
                .padding(Theme.Spacing.lg)
            } else if let error {
                ErrorBanner(message: error).padding()
            } else {
                ProgressView().padding(.top, 80)
            }
        }
        .background(Theme.Colors.surfaceAlt)
        .navigationTitle("Contrato")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $pdf) { file in
            NavigationStack { PDFViewer(file: file) }
        }
        .alert("Anular contrato", isPresented: $showVoid) {
            TextField("Motivo (opcional)", text: $voidReason)
            Button("Anular", role: .destructive) { Task { await voidContract() } }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Los enlaces de firma dejarán de funcionar.")
        }
        .refreshable { await load() }
        .task { if !isPreview { await load() } }
    }

    // MARK: Secciones

    private func header(_ c: Contract) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            StatusBadge(text: c.statusLabel, color: Theme.Colors.contract(c.status))
            Text(c.title).font(Theme.Fonts.title)
            if !c.clientName.isEmpty {
                Label(c.clientName, systemImage: "person").foregroundStyle(Theme.Colors.textSecondary)
            }
            if c.signerTotal > 0 {
                ProgressView(value: c.progress) {
                    Text("\(c.signerDone) de \(c.signerTotal) firmas").font(.caption)
                }
                .tint(Theme.Colors.contract(c.status))
            }
            HStack {
                if let sent = c.sentAt { Text("Enviado \(Formatters.relative(sent))") }
                if let expires = c.expiresAt, c.status == "sent" { Text("· vence \(Formatters.dateTime(expires))") }
            }
            .font(.caption)
            .foregroundStyle(Theme.Colors.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func documents(_ c: Contract) -> some View {
        VStack(spacing: Theme.Spacing.sm) {
            Button {
                Task { await openPDF(c.documentUrl, name: c.title) }
            } label: {
                Label("Ver documento", systemImage: "doc.text").frame(maxWidth: .infinity, alignment: .leading)
            }
            if let finalUrl = c.finalUrl {
                Divider()
                Button {
                    Task { await openPDF(finalUrl, name: c.title + " (firmado)") }
                } label: {
                    Label("PDF firmado y sellado", systemImage: "checkmark.seal").frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .disabled(busy)
        .card()
    }

    private func recipients(_ c: Contract) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            Text("Destinatarios").font(.headline)
            ForEach(c.recipients) { r in
                HStack(alignment: .top, spacing: Theme.Spacing.md) {
                    AvatarView(name: r.name, size: 36)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(r.name).font(.subheadline.weight(.semibold))
                        Text(r.isSigner ? "Firmante · orden \(r.routingOrder)" : "Recibe copia")
                            .font(.caption).foregroundStyle(Theme.Colors.textSecondary)
                        if let signed = r.signedAt {
                            Text("Firmó \(Formatters.dateTime(signed))").font(.caption2).foregroundStyle(Theme.Colors.success)
                        }
                        if let reason = r.declineReason, !reason.isEmpty {
                            Text("Motivo: \(reason)").font(.caption2).foregroundStyle(Theme.Colors.danger)
                        }
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 6) {
                        StatusBadge(text: r.statusLabel, color: recipientColor(r.status))
                        if c.status == "sent" && r.isPending {
                            Button("Reenviar") { Task { await resend(r) } }
                                .font(.caption.weight(.semibold))
                                .disabled(busy)
                        }
                    }
                }
            }
        }
        .card()
    }

    private func timeline(_ events: [ContractEvent]) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            Text("Historial").font(.headline)
            ForEach(Array(events.reversed().enumerated()), id: \.offset) { _, e in
                HStack(alignment: .top, spacing: Theme.Spacing.md) {
                    Circle().fill(Theme.Colors.accent).frame(width: 8, height: 8).padding(.top, 6)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(e.label).font(.subheadline)
                        Text([e.actor, e.device, Formatters.dateTime(e.createdAt)].filter { !$0.isEmpty }.joined(separator: " · "))
                            .font(.caption2).foregroundStyle(Theme.Colors.textSecondary)
                    }
                }
            }
        }
        .card()
    }

    @ViewBuilder
    private func actions(_ c: Contract) -> some View {
        switch c.status {
        case "draft":
            VStack(spacing: Theme.Spacing.sm) {
                if c.hasFields == true {
                    Button("Enviar para firma") { Task { await send() } }
                        .buttonStyle(PrimaryButtonStyle())
                } else {
                    Text("Este borrador no tiene campos de firma. Colócalos en el CRM web y luego envíalo desde aquí.")
                        .font(.footnote).foregroundStyle(Theme.Colors.textSecondary)
                    Link(destination: APIClient.shared.absoluteURL("/contratos/\(c.id)/preparar")) {
                        Text("Preparar en el CRM web").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
                Button("Eliminar borrador", role: .destructive) { Task { await deleteDraft() } }
                    .font(.footnote)
            }
            .disabled(busy)
        case "sent":
            Button("Anular contrato", role: .destructive) { showVoid = true }
                .frame(maxWidth: .infinity)
                .buttonStyle(.bordered)
                .disabled(busy)
        default:
            EmptyView()
        }
    }

    private func recipientColor(_ status: String) -> Color {
        switch status {
        case "completed": return Theme.Colors.success
        case "declined": return Theme.Colors.danger
        case "viewed", "sent": return Theme.Colors.warning
        default: return Theme.Colors.textSecondary
        }
    }

    // MARK: Acciones

    private func load() async {
        do {
            contract = try await APIClient.shared.contract(contractId)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func openPDF(_ path: String, name: String) async {
        busy = true
        defer { busy = false }
        do {
            let data = try await APIClient.shared.download(path)
            let safe = name.replacingOccurrences(of: "/", with: "-")
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(safe).pdf")
            try data.write(to: url, options: .atomic)
            pdf = PDFFile(url: url, title: name)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func resend(_ r: ContractRecipient) async {
        busy = true
        defer { busy = false }
        do {
            try await APIClient.shared.resendContract(contractId, recipient: r.id)
            notice = "Enlace reenviado a \(r.name)."
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func send() async {
        busy = true
        defer { busy = false }
        do {
            let res = try await APIClient.shared.sendContract(contractId)
            contract = res.contract
            notice = res.warning ?? "Contrato enviado."
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func voidContract() async {
        busy = true
        defer { busy = false }
        do {
            contract = try await APIClient.shared.voidContract(contractId, reason: voidReason)
            notice = "Contrato anulado."
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func deleteDraft() async {
        busy = true
        defer { busy = false }
        do {
            try await APIClient.shared.deleteContract(contractId)
            contract = nil
            error = "Borrador eliminado."
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Visor de PDF

struct PDFFile: Identifiable {
    let url: URL
    let title: String
    var id: URL { url }
}

struct PDFViewer: View {
    let file: PDFFile
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        PDFKitView(url: file.url)
            .ignoresSafeArea(edges: .bottom)
            .navigationTitle(file.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cerrar") { dismiss() } }
                ToolbarItem(placement: .primaryAction) { ShareLink(item: file.url) }
            }
    }
}

struct PDFKitView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.document = PDFDocument(url: url)
        return view
    }

    func updateUIView(_ view: PDFView, context: Context) {
        if view.document?.documentURL != url { view.document = PDFDocument(url: url) }
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        ContractDetailView(preview: PreviewData.contracts[0])
    }
}
#endif
