import AVFoundation
import SwiftUI
import VisionKit
import LuminaCore

@MainActor
struct TVPairingEntryView: View {
    let onSelect: (TVApprovalRequest) -> Void
    let onHandoff: (TVPairingHandoff) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var code = ""
    @State private var scanning = false
    @State private var scannerMessage: String?
    @FocusState private var codeFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("Connect a TV") {
                    Text("Scan the code shown on your TV, or enter its eight-character code.")
                        .font(.subheadline).foregroundStyle(.secondary)
                    TextField("TV code", text: $code)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .focused($codeFocused)
                        .submitLabel(.go)
                        .onSubmit(submitManualCode)
                        .accessibilityIdentifier("tvPairingCodeField")
                    Button("Look up TV") { submitManualCode() }
                        .disabled(code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .accessibilityIdentifier("tvPairingLookupButton")
                    Button(scanning ? "Scanning…" : "Scan TV code") { requestScanner() }
                        .disabled(scanning)
                    if scanning {
                        MobilePairScanner(onCode: acceptScannedCode, onUnavailable: {
                            scanning = false
                            scannerMessage = "The camera is unavailable. Enter the code instead."
                        })
                        .frame(height: 280)
                        .clipShape(.rect(cornerRadius: 16))
                    }
                    if let scannerMessage { Text(scannerMessage).foregroundStyle(.red) }
                }
            }
            .scrollContentBackground(.hidden)
            .background(LuminaTheme.paper)
            .foregroundStyle(LuminaTheme.ink)
            .navigationTitle("Connect a TV")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
        .tint(LuminaTheme.ink)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func submitManualCode() {
        let value = code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !value.isEmpty else { return }
        codeFocused = false
        onSelect(TVApprovalRequest(code: value, expectedServerID: nil))
    }

    private func acceptScannedCode(_ raw: String) {
        scanning = false
        if let url = URL(string: raw), let handoff = try? TVPairingHandoff(link: url) {
            onHandoff(handoff)
        } else if let url = URL(string: raw), let link = try? TVPairingApprovalLink(deepLink: url) {
            onSelect(TVApprovalRequest(code: link.code, expectedServerID: link.serverID))
        } else if let link = try? TVPairingApprovalLink(scannedCode: raw) {
            onSelect(TVApprovalRequest(code: link.code, expectedServerID: link.serverID))
        } else {
            scannerMessage = "This is not a Lumina TV pairing code."
        }
    }

    private func requestScanner() {
        Task {
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized:
                scanning = true
            case .notDetermined:
                scanning = await AVCaptureDevice.requestAccess(for: .video)
                if !scanning { scannerMessage = "Camera access is required to scan a TV code." }
            default:
                scannerMessage = "Camera access is unavailable. Enter the code instead."
            }
        }
    }
}

@MainActor
struct TVPairingApprovalView: View {
    let store: LuminaStore
    let request: TVApprovalRequest
    @Environment(\.dismiss) private var dismiss
    @State private var inspection: TVPairingInspection?
    @State private var inspectionOwner: String?
    @State private var state: State = .loading

    private enum State: Equatable {
        case loading, ready, approved, denied, needsSignIn, unavailable(String)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if state != .ready {
                        Text(statusText)
                            .font(.caption)
                            .foregroundStyle(LuminaTheme.muted)
                            .accessibilityIdentifier("tvPairingApprovalStatus")
                    }
                    switch state {
                    case .loading:
                        ProgressView("Checking TV code…")
                    case .needsSignIn:
                        ContentUnavailableView("Sign in required", systemImage: "person.crop.circle.badge.exclamationmark",
                                               description: Text("Sign in to this Lumina server, then open this TV request again."))
                    case .unavailable(let message):
                        ContentUnavailableView("TV request unavailable", systemImage: "exclamationmark.triangle",
                                               description: Text(message))
                        Button("Try again") { Task { await inspect() } }
                            .buttonStyle(LuminaQuietActionStyle())
                    case .approved:
                        ContentUnavailableView("TV approved", systemImage: "checkmark.circle",
                                               description: Text("Your TV will finish signing in automatically."))
                    case .denied:
                        ContentUnavailableView("TV request denied", systemImage: "xmark.circle",
                                               description: Text("The TV was not connected."))
                    case .ready:
                        approvalDetails
                    }
                }
                .padding(24)
                .frame(maxWidth: 600, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(LuminaTheme.paper)
            .foregroundStyle(LuminaTheme.ink)
            .navigationTitle("Approve TV")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(state == .approved || state == .denied ? "Done" : "Cancel") { dismiss() }
                        .accessibilityIdentifier("tvPairingDoneButton")
                }
            }
            .accessibilityIdentifier("tvPairingApprovalScreen")
            .accessibilityValue(statusText)
        }
        .tint(LuminaTheme.ink)
        .task(id: "\(request.id)|\(sessionOwner ?? "signed-out")") { await inspect() }
    }

    @ViewBuilder private var approvalDetails: some View {
        if let inspection, let session = store.session, inspectionOwner == sessionOwner {
            VStack(alignment: .leading, spacing: 10) {
                Text(inspection.deviceName).font(.title2.weight(.semibold))
                LabeledContent("TV code") {
                    Text(inspection.code).monospaced().fontWeight(.semibold).foregroundStyle(LuminaTheme.ink)
                }
                LabeledContent("Member") { Text(session.username).foregroundStyle(LuminaTheme.ink) }
                LabeledContent("Server") { Text(session.serverName).foregroundStyle(LuminaTheme.ink) }
            }
            .font(.body)
            Text("Only approve a TV you can see. This does not sign this phone out or share its session.")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack(spacing: 12) {
                Button("Deny", role: .destructive) { Task { await deny() } }
                    .buttonStyle(LuminaQuietActionStyle())
                    .accessibilityIdentifier("tvPairingDenyButton")
                Button("Approve") { Task { await approve() } }
                    .buttonStyle(LuminaQuietActionStyle(primary: true))
                    .accessibilityIdentifier("tvPairingApproveButton")
            }
        } else {
            ProgressView("Checking TV code…")
        }
    }

    private var statusText: String {
        switch state {
        case .loading: "Checking"
        case .ready: "Ready"
        case .approved: "Approved"
        case .denied: "Denied"
        case .needsSignIn: "Sign in required"
        case .unavailable: "Unavailable"
        }
    }

    private func inspect() async {
        guard let owner = sessionOwner else { state = .needsSignIn; return }
        state = .loading
        inspection = nil
        inspectionOwner = nil
        do {
            let result = try await store.inspectTVPairing(code: request.code)
            guard owner == sessionOwner else { return }
            guard request.expectedServerID == nil || request.expectedServerID == result.serverID else {
                state = .unavailable("This TV code belongs to a different server.")
                return
            }
            inspection = result
            inspectionOwner = owner
            state = result.status == .pending ? .ready : terminalState(for: result.status)
        } catch is CancellationError { }
        catch { if owner == sessionOwner { state = .unavailable(message(for: error)) } }
    }

    private func approve() async {
        guard let owner = sessionOwner, inspection != nil, inspectionOwner == owner else { return }
        state = .loading
        do {
            try await store.approveTVPairing(code: request.code)
            guard owner == sessionOwner else { return }
            state = .approved
        } catch is CancellationError { }
        catch { if owner == sessionOwner { state = .unavailable(message(for: error)) } }
    }

    private func deny() async {
        guard let owner = sessionOwner, inspection != nil, inspectionOwner == owner else { return }
        state = .loading
        do {
            try await store.denyTVPairing(code: request.code)
            guard owner == sessionOwner else { return }
            state = .denied
        } catch is CancellationError { }
        catch { if owner == sessionOwner { state = .unavailable(message(for: error)) } }
    }

    private var sessionOwner: String? {
        guard let session = store.session else { return nil }
        return "\(session.serverURL.absoluteString)|\(session.userID)|\(session.accessToken)"
    }

    private func terminalState(for status: TVPairingStatus) -> State {
        status == .denied ? .denied : .unavailable(status == .expired ? "This TV code expired. Start a new request on the TV." : "This TV code is no longer available.")
    }

    private func message(for error: Error) -> String {
        guard let error = error as? LuminaError else { return "Could not contact this Lumina server. Try again." }
        switch error {
        case .http(404), .http(410): return "This TV code expired or is unavailable. Start a new request on the TV."
        case .http(401), .http(403), .notSignedIn: return "Your sign-in changed. Sign in again before approving a TV."
        default: return "Could not check this TV request. Try again."
        }
    }
}
