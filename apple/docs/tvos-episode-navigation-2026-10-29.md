# TV episode activation and hero return — October 29, 2026

## Report and reproduction

Selecting an episode card updated the preview but did not start playback. The focused native regression pressed Select on episode two and timed out waiting for the player. It previously required returning to the hero's Play button.

A separate regression uses 27 synthetic seasons and five episodes in the last season, matching the horizontal overflow in the reported navigation path. The original implementation left the actual hero origin at −266 points after five Up presses, with focus still in the season controls. The check reads the rendered hero's vertical scroll-coordinate origin through a test-only diagnostic. An earlier accessibility-group frame check was discarded because its bounds represent accessible content rather than the whole artwork.

## Changes

Episode Select now uses the existing playback preparation path for that episode. Focus continues to preview episode metadata; preparation retains its duplicate-start guard, resume behavior, error handling and cancellation. Back retains the activated episode card. VoiceOver describes Select as playing the episode.

The movie/show hero exposes a full-width directional focus section so it can be reached from late seasons on the right. A shared native scroll container restores the top when focus enters a hero or the pinned navigation header. Home, movie/show details, Music browsing/details and Requests browsing use it. Ordinary shelf navigation and the library grid's own restoration remain independent. The transition respects Reduce Motion. No artwork/metadata cache behavior, backend schema or dependency changes are involved.

The long-series fixture finishes probing its generated media before verification; otherwise its extra files exceed the startup probe batch and race the unrelated audio-duration verification.

## Qualification

On the combined source including #220, three focused native methods pass in `integrated.xcresult`: episode-card Select starts episode two with advancing decoded video, server progress and card focus after Back; the existing season/metadata journey switches seasons and resumes the saved episode through Siri Remote Play/Pause after an actual app-process restart; and the 27-season return journey restores the hero origin to 0 and restores Home's full hero. The playback and resume methods took 58.76 and 134.49 seconds; the long-series return method took 70.23 seconds.

The cross-destination method passes in `browse-final.xcresult` (104.77 seconds): the movie opens with its full hero and decoded playback, Music restores its full artwork from the album grid, and Requests restores its heading/top controls from its lower shelves. The movie fixture legitimately has no optional related-title shelf, so that portion checks its initial full hero and decoded playback; below-hero return is exercised on Home, series, Music and Requests. That fourth method initially failed in the integrated bundle because the check incorrectly required that optional shelf; only the three passing methods from that bundle are counted. The corrected cross-destination method was rerun separately. Its Requests check also handles the lazy stack removing its heading while offscreen, then requires the heading to reappear at its original vertical position. The earlier pre-integration run also exceeded an undersized 180-second allowance during a sign-out/sign-in sequence; the retained fresh-process resume contract now restores saved credentials directly. Those diagnostic runs are not counted as passing suites. The broker's existing same-class fixture-isolation contract also passes.

`source-integrated.json` and `source-browse-final.json` record tested source hashes; product code is identical between these runs. The final browse run changes only its test's optional-shelf and lazy-row assumptions. Evidence is retained in ignored `output/tvos-episode-focus/`. These are optimized Release builds on a dedicated second-generation Apple TV 4K 1080p / tvOS 27 Simulator with disposable authenticated synthetic media. No physical Apple TV or new TestFlight upload is claimed. Only focused workflows are selected under the owner's testing instruction.
