import SwiftUI
import LuminaCore

@MainActor
struct NativeAccountAllowancesView: View {
    let settings: LuminaUserSettings
    let query: String
    @State private var resources = SettingsValue.null
    @State private var jobs: [SettingsValue] = []
    @State private var error: String?
    @State private var cancelling: String?

    private let fields = [
        ("remote_stream_bps", "Remote stream ceiling"), ("remote_member_bps", "Remote bandwidth"),
        ("playback_sessions", "Playback sessions"), ("video_encodes", "Video conversions"),
        ("download_running", "Running downloads"), ("download_queued", "Queued downloads"),
        ("ai_running", "Running AI jobs"), ("ai_queued", "Queued AI jobs"),
    ]
    var body: some View {
        Group {
            if query.split(whereSeparator: \.isWhitespace).allSatisfy({ "your allowances resources bandwidth playback ai jobs cancel downloads".localizedCaseInsensitiveContains(String($0)) }) {
                Section {
                    if resources == .null && error == nil { ProgressView("Loading your allowances…") }
                    ForEach(fields, id: \.0) { key, title in
                        if let value = resources["effective"]?[key]?.numberValue {
                            LabeledContent(title, value: value == 0 ? "Unlimited" : key.hasSuffix("_bps") ? (value / 1_000_000).formatted(.number.precision(.fractionLength(1))) + " Mbps" : String(Int(value)))
                        }
                    }
                    Text("Household caps still apply when an allowance is unlimited.")
                        .font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier(resources == .null ? "accountAllowancesLoading" : "accountAllowancesLoaded")
                    ForEach(jobs, id: \.resourceJobID) { job in
                        HStack {
                            VStack(alignment: .leading) {
                                Text((job["kind"]?.stringValue ?? "Job").capitalized)
                                Text(job["state"]?.stringValue ?? "").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Cancel", role: .destructive) { Task { await cancel(job) } }
                                .buttonStyle(.borderless).disabled(cancelling != nil)
                        }
                    }
                    if jobs.isEmpty && resources != .null {
                        Text("No active AI jobs.")
                            .foregroundStyle(.secondary)
                            .accessibilityIdentifier("accountAllowancesNoActiveJobs")
                    }
                    if let error {
                        Text(error).foregroundStyle(.red).accessibilityIdentifier("accountAllowancesError")
                        Button("Retry allowances") { Task { await load() } }
                    }
                } header: {
                    Text("Your allowances").accessibilityIdentifier("accountAllowancesSection")
                }
            }
        }
        .task(id: settings.hasLoaded) { if settings.hasLoaded { await load() } }
    }
    private func load() async {
        do {
            resources = try await settings.action("resources")
            jobs = try await settings.action("resource-jobs").arrayValue ?? []
            error = nil
        } catch { self.error = error.localizedDescription }
    }
    private func cancel(_ job: SettingsValue) async {
        cancelling = job.resourceJobID
        defer { cancelling = nil }
        do {
            _ = try await settings.action("resource-jobs/\(job["kind"]?.stringValue ?? "")/\(job.resourceJobID)/cancel", method: "POST")
            await load()
        } catch { self.error = error.localizedDescription }
    }
}
private extension SettingsValue { var resourceJobID: String { self["id"]?.stringValue ?? "" } }
