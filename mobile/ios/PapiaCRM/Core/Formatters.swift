import Foundation

enum Formatters {

    // MARK: Fechas
    // El CRM guarda las fechas en UTC como "2026-10-09 18:30:00" (SQLite).

    private static let sqlite: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()

    private static let sqliteMinutes: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f
    }()

    private static let dayOnly: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private static let iso = ISO8601DateFormatter()

    static func date(_ raw: String?) -> Date? {
        guard let input = raw, !input.isEmpty else { return nil }
        var raw = input.replacingOccurrences(of: "T", with: " ")
        if let dot = raw.firstIndex(of: ".") { raw = String(raw[..<dot]) }
        if raw.count > 19, raw.contains("+") || raw.hasSuffix("Z") {
            return iso.date(from: raw.replacingOccurrences(of: " ", with: "T"))
        }
        return sqlite.date(from: String(raw.prefix(19)))
            ?? sqliteMinutes.date(from: String(raw.prefix(16)))
            ?? dayOnly.date(from: String(raw.prefix(10)))
    }

    /// "10:42", "Ayer", "lun", "12/09/26" — como la lista de WhatsApp.
    static func chatListTime(_ raw: String?) -> String {
        guard let d = date(raw) else { return "" }
        let cal = Calendar.current
        if cal.isDateInToday(d) { return d.formatted(date: .omitted, time: .shortened) }
        if cal.isDateInYesterday(d) { return "Ayer" }
        if let days = cal.dateComponents([.day], from: d, to: .now).day, days < 7 {
            return d.formatted(.dateTime.weekday(.abbreviated))
        }
        return d.formatted(date: .numeric, time: .omitted)
    }

    /// Hora actual en el formato del CRM (UTC), para mensajes que aún se están enviando.
    static func nowSQLite() -> String { sqlite.string(from: .now) }

    /// Recordatorios: el CRM los guarda en hora local ("2026-10-10 10:00:00").
    static func reminder(_ raw: String?) -> String {
        guard let raw, !raw.isEmpty else { return "" }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        f.dateFormat = raw.count >= 16 ? "yyyy-MM-dd HH:mm" : "yyyy-MM-dd"
        let text = String(raw.replacingOccurrences(of: "T", with: " ").prefix(raw.count >= 16 ? 16 : 10))
        guard let d = f.date(from: text) else { return raw }
        return raw.count >= 16 ? d.formatted(date: .abbreviated, time: .shortened) : d.formatted(date: .abbreviated, time: .omitted)
    }

    static func time(_ raw: String?) -> String {
        guard let d = date(raw) else { return "" }
        return d.formatted(date: .omitted, time: .shortened)
    }

    static func dayHeader(_ raw: String?) -> String {
        guard let d = date(raw) else { return "" }
        let cal = Calendar.current
        if cal.isDateInToday(d) { return "Hoy" }
        if cal.isDateInYesterday(d) { return "Ayer" }
        return d.formatted(.dateTime.weekday(.wide).day().month(.wide))
    }

    static func dateTime(_ raw: String?) -> String {
        guard let d = date(raw) else { return raw ?? "" }
        return d.formatted(date: .abbreviated, time: .shortened)
    }

    static func relative(_ raw: String?) -> String {
        guard let d = date(raw) else { return "" }
        return d.formatted(.relative(presentation: .named))
    }

    // MARK: Dinero

    static func money(_ value: Double) -> String {
        value.formatted(.currency(code: "USD").precision(.fractionLength(value.rounded() == value ? 0 : 2)))
    }

    // MARK: Teléfonos

    static func phone(_ raw: String) -> String {
        let digits = raw.filter(\.isNumber)
        if digits.count == 11, digits.hasPrefix("1") {
            let d = Array(digits)
            return "+1 (\(String(d[1...3]))) \(String(d[4...6]))-\(String(d[7...10]))"
        }
        return raw
    }

    static func e164(_ raw: String) -> String {
        let digits = raw.filter(\.isNumber)
        if digits.count == 10 { return "+1" + digits }
        return digits.isEmpty ? "" : "+" + digits
    }
}
