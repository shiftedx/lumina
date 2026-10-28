import LuminaCore
import SwiftUI

struct NativeMediaAbout: View {
    let overview: MediaOverview
    @State private var showsFullSynopsis = false
    @ScaledMetric(relativeTo: .body) private var paragraphSpacing = 12.0

    init(overview: MediaOverview) {
        self.overview = overview
    }

    var body: some View {
        if !overview.isEmpty {
            VStack(alignment: .leading, spacing: paragraphSpacing) {
                synopsis
                if let source = overview.source {
                    Text("Source: \(source)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("mediaAboutSource")
                }
                if !overview.notes.isEmpty {
                    DisclosureGroup {
                        VStack(alignment: .leading, spacing: paragraphSpacing) {
                            ForEach(overview.notes) { note in
                                HStack(alignment: .firstTextBaseline, spacing: 8) {
                                    Text("•").accessibilityHidden(true)
                                    Text(note.text)
                                }
                            }
                        }
                        .padding(.vertical, paragraphSpacing)
                    } label: {
                        Text("Notes")
                            .font(.body.weight(.semibold))
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .contentShape(.rect)
                            .accessibilityIdentifier("mediaAboutNotes")
                    }
                }
            }
            .font(.body)
            .frame(maxWidth: 600, alignment: .leading)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("mediaAbout")
            .onChange(of: overview) { _, _ in showsFullSynopsis = false }
        }
    }

    @ViewBuilder
    private var synopsis: some View {
        if isLongSynopsis, !showsFullSynopsis {
            Text(synopsisText)
                .lineLimit(6)
            Button { showsFullSynopsis = true } label: {
                Text("Show more")
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(.rect)
            }
            .accessibilityIdentifier("mediaAboutShowMore")
        } else {
            ForEach(overview.synopsis) { paragraph in
                Text(paragraph.text)
            }
            if isLongSynopsis {
                Button { showsFullSynopsis = false } label: {
                    Text("Show less")
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .contentShape(.rect)
                }
                .accessibilityIdentifier("mediaAboutShowLess")
            }
        }
    }

    private var synopsisText: String {
        overview.synopsis.map(\.text).joined(separator: "\n\n")
    }

    private var isLongSynopsis: Bool { synopsisText.count > 500 }
}
