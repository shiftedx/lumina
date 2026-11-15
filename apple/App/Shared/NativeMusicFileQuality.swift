import LuminaCore
import SwiftUI

/// Quiet source-file metadata shared by collection pages on both Apple platforms.
struct NativeAlbumFileQuality: View {
    let quality: MusicAlbumFileQuality

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            NativeAlbumFileQualityCaption(quality: quality)
                .accessibilityIdentifier("musicAlbumSourceQuality")
            NativeAudioDisclosure("File details", identifier: "musicFileDetails") {
                VStack(alignment: .leading, spacing: 12) {
                    LabeledContent("Format", value: quality.codecSummary)
                    LabeledContent("Bit depth", value: quality.bitDepthSummary)
                    LabeledContent("Sample rate", value: quality.sampleRateSummary)
                    LabeledContent("Average bitrate", value: quality.bitrateSummary)
                    if !quality.formatsComplete {
                        Text("Some file metadata is unavailable. Formats and resolutions cover known tracks.")
                    }
                    Text("Album bitrate is averaged by track duration and shown only when every visible track has a bitrate and duration. These are source-file facts; playback output may differ.")
                        .foregroundStyle(.secondary)
                }
                .font(detailsFont)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct NativeAlbumFileQualityCaption: View {
    let quality: MusicAlbumFileQuality
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(quality.formatSummary)
            Text(quality.bitrateSummary).foregroundStyle(.secondary).monospacedDigit()
        }
        .font(metadataFont)
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Source files")
        .accessibilityValue(quality.accessibilitySummary)
    }
}

struct NativeTrackFileQuality: View {
    let quality: MusicFileQuality
    var body: some View {
        Text(quality.summary)
            .font(metadataFont)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

private var metadataFont: Font {
    #if os(tvOS)
    TVTypography.metadata
    #else
    .caption
    #endif
}

private var detailsFont: Font {
    #if os(tvOS)
    TVTypography.metadata
    #else
    .callout
    #endif
}
