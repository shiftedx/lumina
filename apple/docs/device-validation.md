# Physical Apple TV / HDR OLED acceptance checklist

Record the commit, Apple TV model, tvOS version, TV model/firmware, HDMI path (direct or receiver/soundbar), network type, source format, and server version. Use media you are authorized to play. Keep tokens, server addresses, usernames, and personal library details out of shared reports.

## Setup

- [ ] Enable Lumina's **Apps like Infuse** API in the web UI; copy its displayed origin.
- [ ] Pair Apple TV in Xcode **Window → Devices and Simulators**. Enable Developer Mode if requested. Select the tvOS scheme, your development team, and the paired Apple TV; Run.
- [ ] Alternatively, install the assigned beta through TestFlight on iPhone/iPad and Apple TV; record its marketing version and build number. This path does not require registering the TV for development. See [TestFlight distribution](testflight.md).
- [ ] Use your existing, supported HTTPS or LAN HTTP configuration. Sign in with a household member account (or an app password for two-step verification).
- [ ] Confirm the connected app appears in Lumina **Settings → Connected apps**.
- [ ] On Apple TV, enable **Settings → Video and Audio → Match Content → Match Dynamic Range / Match Frame Rate** for the matching tests. Note the base output mode and TV processing settings.

## Library and Siri Remote

- [ ] Browse posters and open a title; title, year/episode text, and overview match Lumina.
- [ ] Search, clear search, refresh, and reach all actions with the remote.
- [ ] Focus is visibly distinct. Return from details to a sensible card. Repeated Back/Menu returns through the navigation hierarchy without trapping focus.
- [ ] Long titles and a large library remain legible at normal viewing distance. Empty and unavailable libraries show an actionable state.

## Core playback and shared progress

- [ ] Play a known H.264/AAC MP4 for at least 20 seconds. Confirm moving video and audible sound, rather than only a displayed time counter.
- [ ] Use remote Play/Pause, seek forward/backward, and open native track controls. Check audio/video synchronization after seeking.
- [ ] Pause; refresh the web UI and verify the watch position within a few seconds. Exit playback; verify the final position and Continue Watching.
- [ ] Resume on iPhone/iPad, then exit and resume on Apple TV. Verify both directions use the last saved position without jumping to zero.
- [ ] Finish a short video. Verify completed state and no stale Continue Watching entry in Lumina.
- [ ] Test **Convert to compatible video**, including an MKV source. Confirm video, sound, seeking, and resume work; stop and confirm the server releases the conversion session.
- [ ] Background/foreground the app during playback. It should pause and retain progress; resume explicitly.
- [ ] Terminate and relaunch the app. The Keychain session restores without entering the password again.

## HDR OLED and audio qualification

These checks measure delivered output. A source label or “Direct” badge alone is not evidence of HDR or Atmos.

- [ ] HEVC SDR MP4: image, levels, color, sync, and seek are correct.
- [ ] HDR10 MP4: the TV's signal information reports HDR, highlights/shadows look correct, and output returns to the appropriate mode after exiting.
- [ ] Dolby Vision: record the source's DV profile/container and the actual TV signal indication. Treat unsupported profiles as a recorded limitation, not proof of general DV support.
- [ ] 23.976/24/25/50/59.94/60 fps sources as available: record the TV/receiver's actual refresh rate and any black-screen transition or persistent judder.
- [ ] AAC stereo, AC-3 and E-AC-3 surround: confirm receiver channel output. For Atmos, record the source encoding and receiver's actual Atmos indication.
- [ ] TrueHD/DTS-HD/image subtitles: record whether playback converts or fails. These formats are not promised natively by this proof; do not mark Infuse parity based on these builds.
- [ ] Force compatible playback on an HDR source: record the resulting output and any tone-mapping/color issue. HDR preservation through conversion is not promised.

## Extended audio acceptance (2026-10-20; physical results pending)

- [ ] Create/edit/delete saved and smart playlists; check resolved tracks, mixes, and a different member's shared read-only playlist.
- [ ] Restore a terminated app's queue and position paused. Change the same member's queue on another device; verify revision conflict choices preserve the selected queue and isolate other members.
- [ ] Save an album, artwork and available lyrics; disconnect the network with the session retained, play local audio, seek and advance tracks. Exercise cancel/retry/remove/clear and budget failures without silent eviction. Sign out and verify owner cleanup. On Apple TV, verify recovery after cache purge; cache storage is not permanent.
- [ ] Check lyric source and timed-line seeking. Compare manual, album and track gain with peak limiting. Verify Off/3/6/12-second crossfade and gapless transitions audibly on eligible buffered tracks; verify the source-loudness streaming label and disabled effects for oversized/unsupported buffering. The buffered path uses bounded current/next-track 48 kHz stereo PCM; do not label it bit-perfect or qualify other hardware formats by inference.
- [ ] Set a sleep deadline, background/relaunch, and verify expiry pauses without automatic restart. Check system interruption and headphone removal.
- [ ] With authorized Sonos hardware, acknowledge handoff before local playback pauses; verify status, play/pause/seek/next/previous, debounced volume, failed acknowledgement/retry, and acknowledged return. Local effects/repeat/sleep must be unavailable on remote output. Record actual speaker compatibility and network behavior.

## Failure paths and access

- [ ] Wrong password/app password yields a useful error without exposing credentials.
- [ ] Disable/re-enable network during playback; the UI remains dismissible and explains playback/progress failure. Reconnect, reopen the title, and check the last server-saved position.
- [ ] Revoke the app token in Lumina; further library/playback operations fail. Sign out and sign in again.
- [ ] Sign out while connected. Relaunch and verify the app stays signed out.
- [ ] Test a restricted member: only that member's visible library appears and plays.

## iPhone, iPad, and Duo

- [ ] Repeat connect, browse, direct/HLS playback, pause/seek/exit, cross-device resume, and sign-out.
- [ ] On iPhone/iPad, verify video audio plays with Silent Mode enabled and stops when leaving playback. Test a headphone disconnect and a system interruption; the app must remain controllable.
- [ ] Test portrait/landscape, iPad split-view resizing, and supported Duo layout changes. Navigation, text, and controls remain reachable inside safe areas.
- [ ] Check large Dynamic Type, VoiceOver labels, light/dark appearance, and local-network permission denial/recovery.

Report each failure with the action, expected/actual result, format/container details, and a redacted screenshot. Do not include media filenames, private hosts, credentials, or complete playback URLs in public reports.
