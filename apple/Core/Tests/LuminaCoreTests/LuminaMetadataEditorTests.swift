import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaMetadataEditorTests: XCTestCase {
    private func editor() -> LuminaMetadataEditor {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MetadataProtocol.self]
        let editor = LuminaMetadataEditor(client: LuminaClient(configuration: config))
        editor.configure(session: LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "test-token",
                                               userID: "owner", username: "Owner", serverName: "Lumina", deviceID: "metadata-test"))
        return editor
    }
    private func item(id: String = "title-1") throws -> MediaItem {
        try JSONDecoder().decode(MediaItem.self, from: JSONSerialization.data(withJSONObject: ["Id": id, "Name": "Old title", "Type": "Movie"]))
    }
    private static var doc: [String: Any] {
        ["title_id": "title-1", "name": "Old title", "type": "movie", "can_identify": true, "tmdb_configured": true, "seasons": [],
         "fields": ["name": ["value": "Old title", "locked": false], "year": ["value": 1999, "locked": false],
                    "overview": ["value": "Keep me", "locked": true], "studios": ["value": ["Studio, Inc."], "locked": false], "provider_ids": ["value": ["Tmdb": "7"], "locked": true]]]
    }
    func testEditSendsOnlyChangedFieldsWithBasesAndNeverOverwritesOnConflict() async throws {
        let editor = editor()
        let state = MetadataReplies(document: Self.doc)
        MetadataProtocol.handler = { request in state.response(request) }
        await editor.reloadPermissions()
        editor.open(try item(), mode: .edit)
        await editor.load()
        XCTAssertEqual(editor.draft["year"], "1999")
        editor.draft["name"] = "Corrected title"
        editor.draft["year"] = "2001"
        editor.draft["studios"] = "Studio, Inc.\nNew Studio"
        state.status = 409
        let conflicted = await editor.save()
        XCTAssertFalse(conflicted)
        XCTAssertEqual(editor.draft["name"], "Corrected title")
        XCTAssertEqual(editor.revision, 0)
        state.status = 200
        let saved = await editor.save()
        XCTAssertTrue(saved)
        let change = try XCTUnwrap(state.body?["edits"] as? [[String: Any]]).first?["changes"] as? [String: [String: Any]]
        XCTAssertEqual(Set(change?.keys.map { $0 } ?? []), ["name", "year", "studios"])
        XCTAssertEqual(change?["name"]?["base"] as? String, "Old title")
        XCTAssertEqual(change?["year"]?["value"] as? Int, 2001)
        XCTAssertEqual(change?["studios"]?["value"] as? [String], ["Studio, Inc.", "New Studio"])
        XCTAssertEqual(editor.revision, 1)
        XCTAssertTrue(state.paths.allSatisfy { $0.hasPrefix("/Lumina/Metadata/") })
    }
    func testMatchSearchConfirmAndRevokedPermissions() async throws {
        let editor = editor()
        let state = MetadataReplies(document: Self.doc)
        MetadataProtocol.handler = { request in state.response(request) }
        await editor.reloadPermissions()
        editor.open(try item(), mode: .match)
        await editor.load()
        XCTAssertEqual(editor.candidates.first?.name, "Correct match")
        let match = try XCTUnwrap(editor.candidates.first)
        let applied = await editor.identify(match)
        XCTAssertTrue(applied)
        XCTAssertEqual(state.body?["tmdb_id"] as? Int, 603)
        state.status = 403
        state.editable = false
        let denied = await editor.identify(match)
        XCTAssertFalse(denied)
        XCTAssertFalse(editor.canIdentify)
        XCTAssertFalse(editor.canEdit)
        editor.configure(session: nil)
        XCTAssertNil(editor.destination)
        XCTAssertNil(editor.document)
    }
    func testPermissionGateAndUnsafeTitleNeverIssueAnEditRequest() async throws {
        let editor = editor()
        let state = MetadataReplies(document: Self.doc)
        MetadataProtocol.handler = { request in state.response(request) }
        editor.open(try item(), mode: .edit)
        XCTAssertNil(editor.destination)
        await editor.reloadPermissions()
        editor.open(try item(id: "../../outside"), mode: .edit)
        await editor.load()
        XCTAssertNotNil(editor.errorMessage)
        XCTAssertEqual(state.paths, ["/Lumina/Metadata/capabilities"])
    }
}

private final class MetadataReplies: @unchecked Sendable {
    private let lock = NSLock()
    private let document: [String: Any]
    private var _status = 200
    private var _editable = true
    private var _body: [String: Any]?
    private var _paths: [String] = []
    var status: Int { get { lock.withLock { _status } } set { lock.withLock { _status = newValue } } }
    var editable: Bool { get { lock.withLock { _editable } } set { lock.withLock { _editable = newValue } } }
    var body: [String: Any]? { lock.withLock { _body } }
    var paths: [String] { lock.withLock { _paths } }
    init(document: [String: Any]) { self.document = document }
    func response(_ request: URLRequest) -> (HTTPURLResponse, Data) {
        lock.withLock { () -> (HTTPURLResponse, Data) in
            _paths.append(request.url!.path)
            let value: Any
            var status = 200
            if request.url!.path.hasSuffix("/capabilities") {
                value = ["can_edit_details": _editable, "can_identify": _editable]
            } else if request.httpMethod == "POST" {
                var data = request.httpBody ?? Data()
                if let stream = request.httpBodyStream {
                    stream.open(); defer { stream.close() }
                    var bytes = [UInt8](repeating: 0, count: 1024)
                    while stream.hasBytesAvailable {
                        let count = stream.read(&bytes, maxLength: bytes.count)
                        if count <= 0 { break }; data.append(contentsOf: bytes.prefix(count))
                    }
                }
                _body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                status = _status
                if status == 200 { value = ["conflicts": [[String: Any]]()] }
                else { value = ["detail": "stale_or_denied"] }
            } else if request.url!.path.hasSuffix("/matches") {
                value = [["tmdb_id": 603, "name": "Correct match", "year": 1999, "overview": "Synthetic match"]]
            } else { value = document }
            return (HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!,
                    try! JSONSerialization.data(withJSONObject: value))
        }
    }
}

private final class MetadataProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) -> (HTTPURLResponse, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let (response, data) = Self.handler?(request) else { return }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
