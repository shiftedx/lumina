# TV episode activation and hero return — October 29, 2026

## Report and reproduction

Selecting an episode card updated the preview but did not start playback. The focused native regression pressed Select on episode two and timed out waiting for the player. It previously required returning to the hero's Play button.

A separate regression uses 27 synthetic seasons and five episodes in the last season, matching the horizontal overflow in the reported navigation path. The original implementation left the actual hero origin at −266 points after five Up presses, with focus still in the season controls. The check reads the rendered hero's vertical scroll-coordinate origin through a test-only diagnostic. An earlier accessibility-group frame check was discarded because its bounds represent accessible content rather than the whole artwork.

## Changes

Episode Select now uses the existing playback preparation path for that episode. Focus continues to preview episode metadata; preparation retains its duplicate-start guard, resume behavior, error handling and cancellation. Back retains the activated episode card. VoiceOver describes Select as playing the episode.

The movie/show hero exposes a full-width directional focus section so it can be reached from late seasons on the right. A shared native scroll container restores the top when focus enters a hero or the pinned navigation header. Home, movie/show details, Music browsing/details and Requests browsing use it. Ordinary shelf navigation and the library grid's own restoration remain independent. The transition respects Reduce Motion. No artwork/metadata cache behavior, backend schema or dependency changes are involved.

The long-series fixture finishes probing its generated media before verification; otherwise its extra files exceed the startup probe batch and race the unrelated audio-duration verification.

## Qualification

Focused native results pending. Evidence is retained in ignored `output/tvos-episode-focus/`. These are optimized Release builds on a dedicated second-generation Apple TV 4K 1080p / tvOS 27 Simulator with disposable authenticated synthetic media. No physical Apple TV or new TestFlight upload is claimed. Only focused workflows are selected under the owner's testing instruction.
