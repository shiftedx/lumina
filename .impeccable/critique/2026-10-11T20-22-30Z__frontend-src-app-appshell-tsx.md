---
target: Lumina app shell and interactable views, including dense playback-menu correction
total_score: 31
p0_count: 0
p1_count: 0
timestamp: 2026-10-11T20-22-30Z
slug: frontend-src-app-appshell-tsx
---
# Lumina app quality audit

This report consolidates the formal app-wide design review completed on 2026-10-11. Lumina’s identity is confirmed and should be preserved: paper, ink, and gold; serif media hierarchy; restrained standard controls; and flows that keep household capabilities available. The work focused on verified geometry, clarity, accessibility, status, and recovery defects.

Every score is a **subjective design judgment**, not an objective certification, telemetry result, or measured task-completion rate. The machine-readable [JSON](app-quality-audit.json) and [CSV](app-quality-audit.csv) contain all 140 reviewed rows, all ten Nielsen dimensions, before scores where evidenced, current or after scores, evidence references, findings, and row limitations.

## Holistic assessment

The whole-app manual judgment moved from **28/40 to 31/40**. This is one holistic review, explicitly **not** the mean of the per-view register, an objective certification, or measured task completion. Consistency, recognition, and minimalist presentation improved; Help remains 2/4.

| Nielsen dimension | Before | Current |
| --- | ---: | ---: |
| Visibility of system status | 3 | 3 |
| Match between system and real world | 4 | 4 |
| User control and freedom | 3 | 3 |
| Consistency and standards | 2 | 3 |
| Error prevention | 3 | 3 |
| Recognition rather than recall | 3 | 4 |
| Flexibility and efficiency | 3 | 3 |
| Aesthetic and minimalist design | 2 | 3 |
| Error recognition, diagnosis, and recovery | 3 | 3 |
| Help and documentation | 2 | 2 |

## Coverage

| Surface | Reviewed rows | Mean current score | Range |
| --- | ---: | ---: | ---: |
| Root | 31 | 31.9/40 | 24–35 |
| Settings | 53 | 33.5/40 | 30–36 |
| Requests | 13 | 31.1/40 | 28–36 |
| Media | 43 | 32.9/40 | 29–36 |

The register contains **140** deduplicated reviewed rows. **50** have an evidenced per-view before score and **16** have paired before/after arrays. Settings also has an aggregate 28/40 pre-fix baseline, but its 53 per-view before scores were never recorded and remain null.

Three overlapping Collections rows from the Root critique were removed from the register. The richer Media entries for Collections list, Collection detail, and Smart collection builder are canonical. The Root-only empty collection detail remains.

## Evidenced score changes

Only rows with an independently recorded before and after score appear here. Unchanged Root and Requests rows retain their formal baseline as the current score and are marked “carried forward” in JSON/CSV.

| Surface | View | Before | After | Δ |
| --- | --- | ---: | ---: | ---: |
| Media | Metadata editor — People | 24 | 31 | +7 |
| Media | Watch — fully populated Playback menu | 24 | 30 | +6 |
| Media | Watch — matched episode with Up next failure | 18 | 36 | +18 |
| Media | Watch — matched episode with empty season | 23 | 35 | +12 |
| Media | Watch — matched episode with populated Up next | 27 | 34 | +7 |
| Media | Watch — standard local player, metadata, and tools | 32 | 33 | +1 |
| Requests | manage | 26 | 29 | +3 |
| Requests | mine | 30 | 31 | +1 |
| Root | auth-picker-password | 28 | 31 | +3 |
| Root | confirmation-dialog | 32 | 33 | +1 |
| Root | onboarding-channels | 30 | 33 | +3 |
| Root | onboarding-error | 31 | 33 | +2 |
| Root | onboarding-interests | 31 | 33 | +2 |
| Root | palette-link-invalid | 27 | 31 | +4 |
| Root | shell-header | 29 | 31 | +2 |

## Manual review and detector output

The Nielsen ratings come from manual screenshot, source, and focused interaction review. Root and Requests Assessment A were recorded before detector review; Media Assessment A was explicitly detector-isolated. Detector output did not set or alter any score.

The app-wide detector produced **10 signals**. **Nine were false positives**: guarded dynamic images, source comments containing image markup, and triangular watch-marker borders. **One was confirmed**: a width transition on the player volume control. That transition was removed while hover and keyboard focus access stayed covered by regression tests.

## Product findings

- The Requests phone location and status-filter problems were resolved without removing any status or media controls. My Requests moved 30→31; Manage moved 26→29.
- The People editor phone row was the clearest presentation failure. Full names and roles now stack above type and actions, moving 24→31 while retaining every action and keyboard reordering.
- Matched-episode Watch states now establish identity before playback, reclaim space for an empty season, and expose retry without replacing the player. The calibrated raw ratings are 27→34, 23→35, and 18→36. All five phone tool labels now wrap fully at 390px; the generic Watch rating moved 32→33 through minimalism only. That generic 33/40 score excludes the fully populated multi-track Playback menu scored separately below.
- Settings is strong across its current 53-view capture set. Long diagnostics and schedule workflows remain scan-heavy on phones; per-view pre-fix scores do not exist.
- A user-reported fully populated Playback menu exposed a distinct P1 state missed by the earlier generic Watch fixture. Its paired Assessment A moved 24→30 after every Quality, Version, Audio, Subtitles, Generate, Sync, Translate, Caption style, and Sound control was retained in readable groups with 44px targets. Before/after full-menu, action, track, Caption style, and Sound slices were inspected at 1280px and 390px in light and dark themes; isolated baseline 5ae failed 4/4 and current passed 4/4. The small phone player still requires scrolling, and the later inset follow-up receives no score increase.
- Remaining cross-surface P2 work centers on stable overflow cues for Library and Channel tab rows, the Artwork auto-save/footer contradiction, and the weak hierarchy among title actions.

## Watch completion

The matched-episode header-geometry calibration and final phone tab wrap are included. All five labels are fully visible at 390px. The raw matched-state ratings remain unchanged, while the generic Watch row changes only Aesthetic and minimalist design from 2→3 (32→33). The producing packet passed 17 focused Playwright checks plus timestamp/source unit coverage.

## Evidence boundaries

- Most evidence uses deterministic synthetic captures at 1440×900 and 390×844 in light and dark themes. No actual household media, private hosts, credentials, or private names appear in this report.
- Reviewed rows do not imply every state was captured. Specialized loading, empty, failure, permission, network, browser, decoder, subtitle, and fullscreen combinations remain outside the evidence where listed.
- A controllable live browser was unavailable for parts of the independent review. Those rows cite synthetic fixture, source, and regression evidence rather than production observation.
- Artwork placeholder quality was excluded from scoring.

## Known coverage gaps

- Expired invitation/reset-link visual variants
- Session-expiry dialog
- Collection rename/delete failure dialogs
- Other specialized modal variants listed in the inventory
- empty, loading, failure, and open action-menu variants for newly captured Shows, Anime, YouTube Videos, and Recordings lenses
- empty, restricted, slow-loading, unreachable, follow-error, and channel-settings modal states for Live, Shorts, Playlists, and In your library
- validation failure, save conflict, destructive confirmation, provider-search result, and unsaved-navigation dialog states
- hardware-decoder failures, native-HLS behavior, real subtitle tracks, and browser fullscreen variations
- Uncaptured loading and error combinations are unreviewed

## Source assessments

- `/tmp/lumina-root-critique.json` and `/tmp/lumina-root-after-scores.json`
- `/tmp/lumina-settings-critique.json`
- `/tmp/lumina-requests-critique.json` and `/tmp/lumina-requests-after-scores.json`
- `output/app-quality/media-critique.json`
- `output/app-quality/watch-quality-assessment.json`
- `/tmp/lumina-detector-dispositions.json`

Temporary assessment paths identify the inputs used during this audit. Durable per-view results and evidence references are copied into the companion JSON and CSV.

## Retained visual examples

These committed screenshots use synthetic media and show the same fixture before and after. The expanded capture sets remain local build artifacts.

| State | Baseline | Result |
| --- | --- | --- |
| Empty season, desktop | [Before](evidence/watch-empty-1440-before.png) | [After](evidence/watch-empty-1440-after.png) |
| Following episodes, phone | [Before](evidence/watch-populated-390-before.png) | [After](evidence/watch-populated-390-after.png) |

## Dense playback-menu correction

The first Watch review missed the full multi-track and advanced-actions state. Its previous generic score does not apply to that state. Manual screenshot review rates the dense menu 24→30/40, with all ten after dimensions at 3/4. The corrected menu retains Quality, Version, Audio, Subtitles, Generate, Sync, Translate, Caption Style, and Sound controls. Icon geometry no longer leaks into text actions; helper copy occupies a separate row. The phone panel still requires scrolling within the player. Baseline regression failed all four width/theme cases; corrected cases pass. The narrow PlaybackMenu detector returned zero signals. The prior app-wide detector remains ten signals: nine false positives and one resolved width transition. No browser overlay is claimed because evaluation is read-only. The holistic app score remains 31/40.

Follow-up light-theme screenshot review also caught low-contrast Caption Style options. The final panel supplies overlay ink tokens in both page themes, and the dense browser regression checks each segmented option reaches 4.5:1 contrast. No score increase is assigned for this additional correction.
