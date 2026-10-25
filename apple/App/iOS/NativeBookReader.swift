import SwiftUI
import UIKit
import Observation
import LuminaCore

@MainActor @Observable
private final class BookReaderModel {
    private let session: LuminaSession
    @ObservationIgnored private lazy var api = LuminaBooks(session: session)
    let id: String
    @ObservationIgnored private lazy var window = BookPageWindow(api: api, bookID: id)
    let preparation = NativeBookPreparation()
    var manifest: BookManifest?
    var unit = 0
    var offset = 0.0
    var document: NativeBookDocument?
    var ranges = [NSRange]()
    var images = [Int: UIImage]()
    var error: String?
    var saveError: String?
    var busy = false
    var ready = false
    private var chapter: (index: Int, nodes: [BookNode])?
    private var inlineImages = [String: UIImage]()
    private var pendingSave: (unit: Int, offset: Double, completed: Bool?)?
    private var saving: Task<Void, Never>?
    private var generation = 0
    private var closed = false
    private var pendingAnchor: String?

    init(session: LuminaSession, book: BookItem, fileID: String? = nil) {
        self.session = session; id = fileID ?? book.readingFileID
    }
    var prosePage: Int {
        let location = Int((offset * Double(max(1, document?.text.length ?? 0))).rounded())
        return ranges.lastIndex { $0.location <= location } ?? 0
    }
    func open() async {
        busy = true; defer { busy = false }
        do {
            let result = try await api.manifest(id: id)
            try Task.checkCancellation()
            guard !closed, result.count > 0 else { throw LuminaError.malformedResponse }
            manifest = result; unit = min(result.count - 1, max(0, result.book.progress.unitIndex))
            offset = min(1, max(0, result.book.progress.offset)); error = nil
        } catch is CancellationError { }
        catch { self.error = error.localizedDescription }
    }
    func prepare(size: CGSize, expanded: Bool, fontSize: CGFloat, color: UIColor, curl: Bool) async {
        guard let manifest, !closed else { return }
        generation += 1; let current = generation
        busy = true; ready = false; error = nil
        defer { if generation == current { busy = false } }
        do {
            if manifest.book.format == "epub" {
                if chapter?.index != unit {
                    let index = unit
                    let nodes = try await api.chapter(id: id, index: index)
                    try Task.checkCancellation()
                    guard current == generation else { return }
                    var loadedImages = [String: UIImage]()
                    var cost = 0
                    for member in Set(NativeBookTypesetter.imageMembers(nodes)) {
                        let data = try await api.resource(id: id, member: member)
                        let prepared = try await preparation.image(data, pixels: 1024)
                        try Task.checkCancellation()
                        guard current == generation else { return }
                        cost += prepared.bytesPerRow * prepared.height
                        guard cost <= 24 * 1024 * 1024 else { throw LuminaError.malformedResponse }
                        loadedImages[member] = UIImage(cgImage: prepared)
                    }
                    chapter = (index, nodes); inlineImages = loadedImages
                }
                let (doc, pages) = try await preparation.document(nodes: chapter?.nodes ?? [], fontSize: fontSize, color: color, images: inlineImages, size: size)
                try Task.checkCancellation()
                guard current == generation else { return }
                document = doc; ranges = pages
                if let anchor = pendingAnchor, let location = doc.anchors[anchor] {
                    offset = Double(location) / Double(max(1, doc.text.length))
                }
                pendingAnchor = nil
            } else if curl {
                document = nil; ranges = []; images = [:]
                await window.retain([], next: nil)
            } else {
                document = nil; ranges = []
                let companion = expanded ? BookSpread.companion(current: unit, count: manifest.count, rightToLeft: manifest.readingDirection == "rtl") : nil
                let primary = expanded ? BookSpread.primary(current: unit, count: manifest.count, rightToLeft: manifest.readingDirection == "rtl") : unit
                let visible = [primary, companion].compactMap { $0 }
                images = images.filter { visible.contains($0.key) }
                await window.retain(visible, next: nil)
                var result = [Int: UIImage]()
                for page in visible {
                    let data = try await window.bytes(page)
                    let pixelSize = min(BookPagePreparation.maximumPixelSize, Int(max(size.width, size.height) * 3))
                    let prepared = try await preparation.image(data, pixels: pixelSize)
                    try Task.checkCancellation()
                    guard current == generation else { return }
                    result[page] = UIImage(cgImage: prepared)
                }
                images = result
                let next = unit + (expanded ? 2 : 1)
                await window.retain(visible, next: next < manifest.count ? next : nil)
            }
            ready = true; checkpoint()
        } catch is CancellationError { }
        catch { if current == generation { self.error = error.localizedDescription } }
    }
    func artwork(_ index: Int, size: CGSize) async throws -> UIImage {
        guard !closed else { throw CancellationError() }
        let data = try await api.page(id: id, index: index)
        try Task.checkCancellation()
        let image = try await preparation.image(data, pixels: min(2048, Int(max(size.width, size.height) * 3)))
        try Task.checkCancellation()
        return UIImage(cgImage: image)
    }
    func turn(_ forward: Bool, expanded: Bool) {
        guard let manifest, !busy, ready else { return }
        let delta: Int = forward ? (expanded ? 2 : 1) : -(expanded ? 2 : 1)
        let artworkStart = expanded ? BookSpread.start(current: unit) : unit
        if manifest.book.format == "epub", !ranges.isEmpty {
            var next = prosePage + delta
            if forward, manifest.readingDirection != "rtl", next >= ranges.count, prosePage < ranges.count - 1 { next = ranges.count - 1 }
            if ranges.indices.contains(next) {
                offset = Double(ranges[next].location) / Double(max(1, document?.text.length ?? 0))
                checkpoint(); return
            }
        }
        let nextUnit = unit + (forward ? 1 : -1)
        guard (0..<manifest.count).contains(nextUnit) else { return }
        if manifest.book.format != "epub", expanded, !(0..<manifest.count).contains(artworkStart + delta) { return }
        document = nil; ranges = []; ready = false
        unit = manifest.book.format == "epub" ? nextUnit : min(manifest.count - 1, max(0, artworkStart + delta))
        offset = forward ? 0 : (manifest.book.format == "epub" ? 1 : 0)
    }
    func jump(_ index: Int) {
        guard let manifest, (0..<manifest.count).contains(index) else { return }
        pendingAnchor = nil
        if unit != index { document = nil; ranges = []; ready = false }
        unit = index; offset = 0
        if document != nil { checkpoint() }
    }
    func link(_ url: URL) {
        guard url.scheme == "lumina-book", let chapter = Int(url.lastPathComponent) else { return }
        let anchor = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "anchor" }?.value
        if chapter >= 0, chapter != unit { jump(chapter); pendingAnchor = anchor; return }
        if let anchor, let location = document?.anchors[anchor] {
            offset = Double(location) / Double(max(1, document?.text.length ?? 0)); checkpoint()
        }
    }
    func checkpoint(completed: Bool? = nil) {
        guard manifest != nil, !closed else { return }
        pendingSave = (unit, offset, completed ?? pendingSave?.completed)
        guard saving == nil else { return }
        saving = Task {
            while let next = pendingSave {
                pendingSave = nil
                do { _ = try await api.save(id: id, unit: next.unit, offset: next.offset, completed: next.completed); saveError = nil }
                catch { saveError = "Reading progress could not be saved. Try again." }
            }
            saving = nil
        }
    }
    func flush() async { await saving?.value }
    func stop() async { closed = true; generation += 1; await window.stop(); await flush() }
}

@MainActor
struct NativeBookReader: View {
    let session: LuminaSession
    let book: BookItem
    private let covers: BookCoverCache?
    @State private var model: BookReaderModel
    @State private var contents = false
    @State private var controlsVisible = false
    @State private var appearance = false
    @State private var single = false
    @State private var spread = false
    @State private var retry = 0
    @State private var textScale = 1.0
    @State private var artworkSizing: Bool?
    @State private var hasHinge = false
    @State private var pageDraft = 1
    @State private var exitWithoutSaving = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var widthClass
    @Environment(\.layoutDirection) private var layoutDirection
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.colorScheme) private var colorScheme
    @ScaledMetric(relativeTo: .body) private var fontSize = 20.0
    init(session: LuminaSession, book: BookItem, covers: BookCoverCache? = nil) {
        self.session = session; self.book = book; self.covers = covers
        _model = State(initialValue: BookReaderModel(session: session, book: book))
    }
    private var expanded: Bool { widthClass == .regular && !single }
    private var fillArtwork: Bool { artworkSizing ?? hasHinge }
    private var isProse: Bool { model.manifest?.book.format == "epub" }

    var body: some View {
        NavigationStack {
            GeometryReader { geometry in
                let layout = layout(geometry)
                let curl = !isProse && !reduceMotion && (layout.companion == nil || layout.companion!.minY == layout.primary.minY)
                ZStack(alignment: .topLeading) {
                    if curl, let manifest = model.manifest, model.error == nil {
                        let frame = layout.companion.map { $0.union(layout.primary) } ?? layout.primary
                        NativeBookCurlPager(index: model.unit, count: manifest.count,
                            doublePage: layout.companion != nil, rightToLeft: manifest.readingDirection == "rtl",
                            fillDisplay: fillArtwork, pageSize: layout.primary.size,
                            spineInset: layout.companion.map { max(0, frame.width / 2 - $0.width) } ?? 0,
                            load: model.artwork, onSelect: model.jump, onTurn: turnPage,
                            onControls: { controlsVisible.toggle() })
                            .id("\(model.id):\(layout.companion != nil):\(layout.primary.size):\(fillArtwork)")
                            .frame(width: frame.width, height: frame.height)
                            .position(x: frame.midX, y: frame.midY)
                    } else {
                        if let other = layout.companion { page(primary: false).environment(\.layoutDirection, layoutDirection).frame(width: other.width, height: other.height).position(x: other.midX, y: other.midY).accessibilityElement(children: .contain).accessibilityIdentifier("bookCompanionPage") }
                        page(primary: true).environment(\.layoutDirection, layoutDirection).frame(width: layout.primary.width, height: layout.primary.height).position(x: layout.primary.midX, y: layout.primary.midY).accessibilityElement(children: .contain).accessibilityIdentifier("bookPrimaryPage")
                    }
                }
                .environment(\.layoutDirection, .leftToRight)
                .task(id: RenderKey(unit: model.unit, size: layout.primary.size, font: fontSize * textScale, expanded: layout.companion != nil, dark: colorScheme == .dark, loaded: model.manifest != nil, retry: retry, curl: curl)) {
                    spread = layout.companion != nil
                    await model.prepare(size: layout.primary.size, expanded: spread, fontSize: fontSize * textScale,
                                        color: colorScheme == .dark ? .white : .black, curl: curl)
                }
            }
            .ignoresSafeArea(.container, edges: !controlsVisible && !isProse ? .all : [])
            .background(Color(uiColor: .systemBackground))
            .overlay(alignment: .bottomTrailing) {
                if !controlsVisible && !isProse {
                    Group {
                        if #available(iOS 26, *) { showControlsButton.buttonStyle(.glass) }
                        else { showControlsButton.buttonStyle(.bordered) }
                    }.padding(8)
                }
            }
            .toolbarVisibility(controlsVisible ? .visible : .hidden, for: .navigationBar)
            .toolbarVisibility(controlsVisible || isProse ? .visible : .hidden, for: .bottomBar)
            .statusBarHidden(!controlsVisible)
            .persistentSystemOverlays(controlsVisible ? .visible : .hidden)
            .navigationTitle(model.manifest?.book.title ?? "Books")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close reader", systemImage: "xmark") { Task { await model.flush(); if model.saveError == nil { dismiss() } else { exitWithoutSaving = true } } }.accessibilityIdentifier("bookCloseButton")
                }
                ToolbarItemGroup(placement: .primaryAction) {
                    Button("Read full screen", systemImage: "arrow.up.left.and.arrow.down.right") { controlsVisible = false }.accessibilityIdentifier("bookImmersiveButton")
                    Button("Contents", systemImage: "list.bullet") { pageDraft = model.unit + 1; contents = true }.accessibilityIdentifier("bookContentsButton")
                    Button("Reading appearance", systemImage: "textformat.size") { appearance = true }.accessibilityIdentifier("bookAppearanceButton")
                }
                ToolbarItemGroup(placement: .bottomBar) {
                    if controlsVisible {
                        Button("Previous page", systemImage: model.manifest?.readingDirection == "rtl" ? "chevron.right" : "chevron.left") { model.turn(false, expanded: spread) }.disabled(model.busy || !model.ready).accessibilityIdentifier("bookPreviousButton")
                        Text(position).font(.caption).accessibilityIdentifier("bookPosition")
                        Button("Next page", systemImage: model.manifest?.readingDirection == "rtl" ? "chevron.left" : "chevron.right") { model.turn(true, expanded: spread) }.disabled(model.busy || !model.ready).accessibilityIdentifier("bookNextButton")
                    } else if isProse { showControlsButton }
                }
            }
            .sheet(isPresented: $contents) { contentsSheet }
            .sheet(isPresented: $appearance) { appearanceSheet }
            .alert("Reading progress isn’t saved", isPresented: $exitWithoutSaving) {
                Button("Keep reading", role: .cancel) { }
                Button("Close without saving", role: .destructive) { dismiss() }
            } message: {
                Text("Your last saved position will be restored next time.")
            }
            .safeAreaInset(edge: .top) {
                if let saveError = model.saveError {
                    HStack { Text(saveError).font(.caption); Button("Retry") { model.checkpoint() } }.padding().background(.background)
                }
            }
        }
        .background(BookHingeCapability(hasHinge: $hasHinge).frame(width: 0, height: 0))
        .task(id: model.id) { await model.open() }
        .onDisappear { Task { await model.stop() } }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                Task {
                    let lease = UIApplication.shared.beginBackgroundTask(withName: "Reading progress")
                    model.checkpoint(); await model.flush()
                    if lease != .invalid { UIApplication.shared.endBackgroundTask(lease) }
                }
            }
        }
    }
    private var showControlsButton: some View {
        Button { controlsVisible = true } label: {
            Label("Show reading controls", systemImage: "ellipsis").labelStyle(.iconOnly).frame(minWidth: 44, minHeight: 44)
        }
            .accessibilityIdentifier("bookControlsButton")
    }
    private func turnPage(_ forward: Bool) { model.turn(forward, expanded: spread) }
    private var position: String {
        guard let manifest = model.manifest else { return "Opening book…" }
        if manifest.book.format == "epub" { return "Chapter \(model.unit + 1) of \(manifest.count) · \(model.prosePage + 1)/\(max(1, model.ranges.count))" }
        if spread {
            let first = BookSpread.start(current: model.unit) + 1
            return "Pages \(first)–\(min(first + 1, manifest.count)) of \(manifest.count)"
        }
        return "Page \(model.unit + 1) of \(manifest.count)"
    }
    @ViewBuilder private func page(primary: Bool) -> some View {
        if primary, let error = model.error {
            ContentUnavailableView { Label("Unable to load page", systemImage: "book.closed") } description: { Text(error) } actions: {
                Button("Retry") { if model.manifest == nil { Task { await model.open() } } else { retry += 1 } }
            }
        } else if let manifest = model.manifest {
            if manifest.book.format == "epub", let document = model.document {
                let index = primary ? (spread ? BookSpread.primary(current: model.prosePage, count: model.ranges.count, rightToLeft: manifest.readingDirection == "rtl") : model.prosePage) : BookSpread.companion(current: model.prosePage, count: model.ranges.count, rightToLeft: manifest.readingDirection == "rtl")
                if let index, model.ranges.indices.contains(index) { NativeBookTextPage(document: document, range: model.ranges[index], rightToLeft: manifest.readingDirection == "rtl", onLink: model.link, onTurn: turnPage, onControls: { controlsVisible.toggle() }) }
                else if !primary { bookend }
            } else if manifest.book.format != "epub" {
                let index = primary ? (spread ? BookSpread.primary(current: model.unit, count: manifest.count, rightToLeft: manifest.readingDirection == "rtl") : model.unit) : BookSpread.companion(current: model.unit, count: manifest.count, rightToLeft: manifest.readingDirection == "rtl")
                if let index, let image = model.images[index] {
                    NativeBookImagePage(image: image, label: "Page \(index + 1) of \(manifest.count)", pageID: "\(model.id):\(index)", fillDisplay: fillArtwork, rightToLeft: manifest.readingDirection == "rtl", onTurn: turnPage, onControls: { controlsVisible.toggle() })
                } else if index != nil { ProgressView("Loading page…") }
                else { bookend }
            } else if primary { ProgressView("Typesetting chapter…") }
        } else if primary { ProgressView("Opening book…") }
    }
    @ViewBuilder private var bookend: some View {
        if let covers {
            NativeBookCover(id: book.coverURL == nil ? nil : book.id, title: book.title, author: book.author, covers: covers)
                .padding(24)
                .onTapGesture { controlsVisible.toggle() }
        }
    }
    private func layout(_ geometry: GeometryProxy) -> BookReaderLayout {
        if #available(iOS 27.1, *) {
            return BookReaderLayout(size: geometry.size, expanded: expanded,
                                    divisions: geometry.reservedRegions(kind: .division, layoutDirectionBehavior: .fixed).map(reservedFrame),
                                    occlusions: geometry.reservedRegions(kind: .occlusion, layoutDirectionBehavior: .fixed).map(reservedFrame), artwork: model.manifest?.book.format != "epub")
        }
        return BookReaderLayout(size: geometry.size, expanded: expanded, artwork: model.manifest?.book.format != "epub")
    }
    @available(iOS 27.1, *)
    private func reservedFrame(_ region: ReservedRegion) -> CGRect {
        CGRect(x: region.frame.minX - region.margins.leading, y: region.frame.minY - region.margins.top,
               width: region.frame.width + region.margins.leading + region.margins.trailing,
               height: region.frame.height + region.margins.top + region.margins.bottom)
    }
    private var contentsSheet: some View {
        NavigationStack {
            Form {
                if let manifest = model.manifest {
                    if manifest.book.format == "epub" {
                        ForEach(manifest.chapters) { chapter in
                            Button { model.jump(chapter.id); contents = false } label: { HStack { Text(chapter.label); if model.unit == chapter.id { Image(systemName: "checkmark") } } }
                        }
                    } else {
                        TextField("Page number", value: $pageDraft, format: .number).keyboardType(.numberPad)
                        Stepper("Page \(pageDraft)", value: $pageDraft, in: 1...max(1, manifest.count))
                        Button("Go to page") { model.jump(pageDraft - 1); contents = false }.disabled(!(1...max(1, manifest.count)).contains(pageDraft))
                    }
                    if manifest.book.files.count > 1 {
                        Section("Files") {
                            ForEach(manifest.book.files) { file in
                                Button("\(file.format.uppercased()) · \(file.filename)") {
                                    Task {
                                        await model.stop()
                                        model = BookReaderModel(session: session, book: book, fileID: file.id)
                                        contents = false
                                    }
                                }.disabled(!file.available || file.id == model.id)
                            }
                        }
                    }
                    Button("Mark as read", systemImage: "checkmark") { model.checkpoint(completed: true); contents = false }
                }
            }
            .navigationTitle("Contents")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done", systemImage: "checkmark") { contents = false } } }
        }
    }
    private var appearanceSheet: some View {
        NavigationStack {
            Form {
                Toggle("Single page", isOn: $single)
                    .tint(LuminaTheme.brand)
                if model.manifest?.book.format == "epub" {
                    Slider(value: $textScale, in: 1...2, step: 0.1) { Text("Text size") }
                } else {
                    Picker("Page sizing", selection: Binding(get: { fillArtwork }, set: { artworkSizing = $0 })) {
                        Text("Fit entire page").tag(false)
                        Text("Fill display").tag(true)
                    }.accessibilityIdentifier("bookSizingPicker")
                    Text("In Fill display, pan to see every part of the page.").foregroundStyle(.secondary)
                }
                Text("Two-page spreads show consecutive pages together, such as 5–6. Manga keeps its original reading order.").foregroundStyle(.secondary)
            }
            .navigationTitle("Reading appearance")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done", systemImage: "checkmark") { appearance = false }.accessibilityIdentifier("bookAppearanceDoneButton") } }
        }
    }
    private struct RenderKey: Equatable {
        let unit: Int
        let size: CGSize
        let font: Double
        let expanded: Bool
        let dark: Bool
        let loaded: Bool
        let retry: Int
        let curl: Bool
    }
}

private struct NativeBookImagePage: UIViewRepresentable {
    let image: UIImage
    let label: String
    let pageID: String
    let fillDisplay: Bool
    let rightToLeft: Bool
    let onTurn: (Bool) -> Void
    let onControls: () -> Void
    var nativeCurl = false
    var onZoom: ((Bool) -> Void)?
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> BookZoomScrollView {
        let view = BookZoomScrollView()
        view.minimumZoomScale = 1; view.maximumZoomScale = 4
        view.delegate = context.coordinator
        view.accessibilityIdentifier = "bookArtworkViewport"
        view.contentInsetAdjustmentBehavior = .never
        view.showsHorizontalScrollIndicator = false; view.showsVerticalScrollIndicator = false
        view.imageView.contentMode = .scaleAspectFit
        view.imageView.isAccessibilityElement = true
        view.addSubview(view.imageView)
        let doubleTap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.doubleTap(_:)))
        doubleTap.numberOfTapsRequired = 2
        view.addGestureRecognizer(doubleTap)
        context.coordinator.scroll = view
        context.coordinator.interaction = BookReadingGestures(scrollView: view, accessibilityTarget: view.imageView, doubleTap: doubleTap, nativeCurl: nativeCurl)
        return view
    }
    func updateUIView(_ view: BookZoomScrollView, context: Context) {
        context.coordinator.interaction?.rightToLeft = rightToLeft
        context.coordinator.interaction?.onTurn = onTurn
        context.coordinator.interaction?.onControls = onControls
        context.coordinator.onZoom = onZoom
        view.fillDisplay = fillDisplay
        if context.coordinator.pageID != pageID {
            view.resetPage()
            context.coordinator.pageID = pageID
        }
        view.imageView.image = image
        view.imageView.accessibilityLabel = label
        view.centerArtwork()
    }
    final class Coordinator: NSObject, UIScrollViewDelegate {
        weak var scroll: BookZoomScrollView?
        var pageID: String?
        var interaction: BookReadingGestures?
        var onZoom: ((Bool) -> Void)?
        func viewForZooming(in scrollView: UIScrollView) -> UIView? { scroll?.imageView }
        func scrollViewDidZoom(_ scrollView: UIScrollView) { scroll?.centerArtwork(); onZoom?(scrollView.zoomScale > 1.01) }
        @objc func doubleTap(_ gesture: UITapGestureRecognizer) {
            guard let scroll else { return }
            let animated = !UIAccessibility.isReduceMotionEnabled
            if scroll.zoomScale > 1 { scroll.setZoomScale(1, animated: animated) }
            else {
                let point = gesture.location(in: scroll.imageView)
                let size = CGSize(width: scroll.bounds.width / 2, height: scroll.bounds.height / 2)
                scroll.zoom(to: CGRect(x: point.x - size.width / 2, y: point.y - size.height / 2, width: size.width, height: size.height), animated: animated)
            }
        }
    }
}

/// The default is a device capability preference; pane layout still follows the actual viewport.
private struct BookHingeCapability: UIViewRepresentable {
    @Binding var hasHinge: Bool
    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.isUserInteractionEnabled = false
        if #available(iOS 27.1, *) {
            let capability = $hasHinge
            view.addInteraction(UIHingeInteraction { _, update in
                if update.hinge != nil { Task { @MainActor in capability.wrappedValue = true } }
            })
        }
        return view
    }
    func updateUIView(_ uiView: UIView, context: Context) { }
}

private final class BookZoomScrollView: UIScrollView {
    let imageView = UIImageView()
    var fillDisplay = false {
        didSet { if oldValue != fillDisplay { resetPage() } }
    }
    private var previousSize = CGSize.zero
    private var needsPageLayout = true
    private var resetCenter = true
    func resetPage() {
        setZoomScale(1, animated: false)
        resetCenter = true; needsPageLayout = true; setNeedsLayout()
    }
    func centerArtwork() {
        let horizontal = max(0, (bounds.width - contentSize.width) / 2)
        let vertical = max(0, (bounds.height - contentSize.height) / 2)
        contentInset = UIEdgeInsets(top: vertical, left: horizontal, bottom: vertical, right: horizontal)
        let mode = fillDisplay ? "Fill display" : "Fit entire page"
        imageView.accessibilityValue = zoomScale > 1.01 ? "\(mode) · \(Int((zoomScale * 100).rounded()))% zoom" : mode
    }
    override func layoutSubviews() {
        super.layoutSubviews()
        guard needsPageLayout || previousSize != bounds.size else { return }
        let oldSize = previousSize
        let center = resetCenter || contentSize.width <= 0 || contentSize.height <= 0 ? CGPoint(x: 0.5, y: 0.5) :
            CGPoint(x: (contentOffset.x + oldSize.width / 2) / contentSize.width,
                    y: (contentOffset.y + oldSize.height / 2) / contentSize.height)
        previousSize = bounds.size; needsPageLayout = false; resetCenter = false
        let savedScale = zoomScale
        setZoomScale(1, animated: false)
        var size = bounds.size
        if let image = imageView.image, image.size.width > 0, image.size.height > 0 {
            let scale = fillDisplay ? max(size.width / image.size.width, size.height / image.size.height) : min(size.width / image.size.width, size.height / image.size.height)
            size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        }
        imageView.frame = CGRect(origin: .zero, size: size)
        contentSize = size
        setZoomScale(savedScale, animated: false)
        centerArtwork()
        contentOffset = CGPoint(x: min(max(-contentInset.left, contentSize.width - bounds.width + contentInset.right), max(-contentInset.left, center.x * contentSize.width - bounds.width / 2)),
                                y: min(max(-contentInset.top, contentSize.height - bounds.height + contentInset.bottom), max(-contentInset.top, center.y * contentSize.height - bounds.height / 2)))
    }
}

/// UIKit owns the paper mesh, interactive fold and cancellation; checkpoints follow completed turns only.
private struct NativeBookCurlPager: UIViewControllerRepresentable {
    let index: Int
    let count: Int
    let doublePage: Bool
    let rightToLeft: Bool
    let fillDisplay: Bool
    let pageSize: CGSize
    let spineInset: CGFloat
    let load: (Int, CGSize) async throws -> UIImage
    let onSelect: (Int) -> Void
    let onTurn: (Bool) -> Void
    let onControls: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIViewController(context: Context) -> UIPageViewController {
        let spine: UIPageViewController.SpineLocation = doublePage ? .mid : rightToLeft ? .max : .min
        let controller = UIPageViewController(transitionStyle: .pageCurl, navigationOrientation: .horizontal,
            options: [.spineLocation: spine.rawValue])
        controller.isDoubleSided = doublePage
        controller.view.backgroundColor = .systemBackground
        controller.view.accessibilityIdentifier = "bookCurlPager"
        controller.dataSource = context.coordinator
        controller.delegate = context.coordinator
        context.coordinator.controller = controller
        context.coordinator.show(index, animated: false)
        return controller
    }
    func updateUIViewController(_ controller: UIPageViewController, context: Context) {
        context.coordinator.parent = self
        context.coordinator.show(index, animated: true)
    }
    static func dismantleUIViewController(_ controller: UIPageViewController, coordinator: Coordinator) {
        controller.dataSource = nil; controller.delegate = nil
        coordinator.leaves.values.forEach { $0.cancel() }
        coordinator.leaves.removeAll()
    }

    @MainActor final class Coordinator: NSObject, UIPageViewControllerDataSource, UIPageViewControllerDelegate {
        var parent: NativeBookCurlPager
        weak var controller: UIPageViewController?
        var leaves = [Int: BookCurlLeafController]()
        private var turning = false
        private var selected: Int?
        private var step: Int { parent.rightToLeft ? -1 : 1 }
        init(_ parent: NativeBookCurlPager) { self.parent = parent }

        private func leaf(_ index: Int) -> BookCurlLeafController {
            if let leaf = leaves[index] { return leaf }
            let leaf = BookCurlLeafController(index: index, count: parent.count, size: parent.pageSize,
                fillDisplay: parent.fillDisplay, rightToLeft: parent.rightToLeft,
                spineInset: parent.spineInset, physicalRight: index % 2 == (parent.rightToLeft ? 0 : 1), load: parent.load,
                onTurn: { [weak self] in self?.parent.onTurn($0) },
                onControls: { [weak self] in self?.parent.onControls() },
                onZoom: { [weak self] zoomed in self?.controller?.gestureRecognizers.forEach { $0.isEnabled = !zoomed } })
            leaves[index] = leaf
            return leaf
        }
        private func normalized(_ index: Int) -> Int { parent.doublePage ? BookSpread.start(current: index) : index }
        private func position(_ index: Int) -> String {
            parent.doublePage ? "Pages \(index + 1)–\(min(index + 2, parent.count)) of \(parent.count)" : "Page \(index + 1) of \(parent.count)"
        }
        private func pages(_ index: Int) -> [UIViewController] {
            if parent.doublePage {
                let leftIndex = BookSpread.companion(current: index, count: parent.count, rightToLeft: parent.rightToLeft) ?? parent.count
                let rightIndex = BookSpread.primary(current: index, count: parent.count, rightToLeft: parent.rightToLeft) ?? parent.count
                let left = leaf(leftIndex); left.physicalRight = false
                let right = leaf(rightIndex); right.physicalRight = true
                return [left, right]
            }
            let page = leaf(index); page.physicalRight = true
            return [page]
        }
        func show(_ requested: Int, animated: Bool) {
            let index = normalized(requested)
            guard !turning, selected != index, let controller else { return }
            let previous = selected
            let direction: UIPageViewController.NavigationDirection = (index > (previous ?? index)) != parent.rightToLeft ? .forward : .reverse
            selected = index; turning = true
            controller.view.accessibilityValue = position(index)
            controller.setViewControllers(pages(index), direction: direction,
                animated: animated && previous != nil && !UIAccessibility.isReduceMotionEnabled) { [weak self] _ in
                    guard let self else { return }
                    self.turning = false
                    self.retainNeighbors()
                    if self.normalized(self.parent.index) != self.selected { self.show(self.parent.index, animated: false) }
                }
        }
        private func retainNeighbors() {
            guard let selected else { return }
            let stride = parent.doublePage ? 2 : 1
            var retained = Set<Int>()
            for primary in [selected - stride, selected, selected + stride] where (0..<parent.count).contains(primary) {
                retained.insert(primary)
                if parent.doublePage { retained.insert(primary + 1) }
            }
            for key in Array(leaves.keys) where !retained.contains(key) { leaves.removeValue(forKey: key)?.cancel() }
            // Stage only adjacent leaves, never a whole book. Preparation remains on the reader's worker lane.
            for index in retained { _ = leaf(index) }
            for leaf in controller?.viewControllers ?? [] {
                guard let leaf = leaf as? BookCurlLeafController else { continue }
                leaf.view.accessibilityIdentifier = leaf === controller?.viewControllers?.last ? "bookPrimaryPage" : "bookCompanionPage"
            }
        }
        func pageViewController(_ pageViewController: UIPageViewController, viewControllerBefore viewController: UIViewController) -> UIViewController? {
            adjacent(to: viewController, delta: -step)
        }
        func pageViewController(_ pageViewController: UIPageViewController, viewControllerAfter viewController: UIViewController) -> UIViewController? {
            adjacent(to: viewController, delta: step)
        }
        private func adjacent(to controller: UIViewController, delta: Int) -> UIViewController? {
            guard let page = controller as? BookCurlLeafController else { return nil }
            let index = page.index + delta
            if parent.doublePage, let visible = self.controller?.viewControllers,
               controller === (delta == step ? visible.last : visible.first) {
                let second = index + delta
                // A center-spine turn must supply two faces, even at a blank bookend.
                guard (-1...parent.count).contains(second),
                      (0..<parent.count).contains(index) || (0..<parent.count).contains(second) else { return nil }
            }
            guard (0..<parent.count).contains(index) || (parent.doublePage && (index == -1 || index == parent.count)) else { return nil }
            return leaf(index)
        }
        func pageViewController(_ pageViewController: UIPageViewController, willTransitionTo pendingViewControllers: [UIViewController]) {
            turning = true
        }
        func pageViewController(_ pageViewController: UIPageViewController, didFinishAnimating finished: Bool,
                                previousViewControllers: [UIViewController], transitionCompleted completed: Bool) {
            turning = false
            if completed, let current = pageViewController.viewControllers?.compactMap({ $0 as? BookCurlLeafController })
                .last(where: { (0..<parent.count).contains($0.index) }) {
                let index = normalized(current.index)
                selected = index
                pageViewController.view.accessibilityValue = position(index)
                parent.onSelect(index)
            }
            retainNeighbors()
        }
    }
}

@MainActor
private final class BookCurlLeafController: UIHostingController<AnyView> {
    let index: Int
    var physicalRight: Bool {
        didSet { if oldValue != physicalRight, let render { rootView = render(physicalRight) } }
    }
    private var render: ((Bool) -> AnyView)?
    private var loading: Task<Void, Never>?
    init(index: Int, count: Int, size: CGSize, fillDisplay: Bool, rightToLeft: Bool,
         spineInset: CGFloat, physicalRight: Bool, load: @escaping (Int, CGSize) async throws -> UIImage, onTurn: @escaping (Bool) -> Void,
         onControls: @escaping () -> Void, onZoom: @escaping (Bool) -> Void) {
        self.index = index; self.physicalRight = physicalRight
        super.init(rootView: AnyView(ProgressView("Loading page…").frame(maxWidth: .infinity, maxHeight: .infinity)))
        view.backgroundColor = .systemBackground
        guard (0..<count).contains(index) else {
            rootView = AnyView(Color(uiColor: .systemBackground)); return
        }
        let start: (BookCurlLeafController) -> Void = { controller in
            controller.loading = Task { [weak controller] in
                do {
                    let image = try await load(index, size)
                    try Task.checkCancellation()
                    let render: (Bool) -> AnyView = { right in AnyView(NativeBookImagePage(image: image, label: "Page \(index + 1) of \(count)",
                        pageID: "\(index)", fillDisplay: fillDisplay, rightToLeft: rightToLeft,
                        onTurn: onTurn, onControls: onControls, nativeCurl: true, onZoom: onZoom)
                        .padding(right ? .leading : .trailing, spineInset)
                        .environment(\.layoutDirection, .leftToRight)
                        .ignoresSafeArea()) }
                    controller?.render = render
                    if let controller { controller.rootView = render(controller.physicalRight) }
                } catch is CancellationError { }
                catch {
                    controller?.rootView = AnyView(ContentUnavailableView {
                        Label("Unable to load page", systemImage: "book.closed")
                    } description: { Text(error.localizedDescription) } actions: {
                        Button("Retry") { [weak controller] in controller?.reload?() }
                    })
                }
            }
        }
        reload = { [weak self] in if let self { start(self) } }
        start(self)
    }
    private var reload: (() -> Void)?
    func cancel() { loading?.cancel(); loading = nil; reload = nil }
    deinit { loading?.cancel() }
    @available(*, unavailable)
    required dynamic init?(coder aDecoder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}
