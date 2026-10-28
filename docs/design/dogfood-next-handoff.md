# Continue small dogfood fixes

Continue the owner's one-issue-at-a-time dogfood workflow. Start with the next reported issue; the previous batch is complete. If no new issue accompanies this handoff, report readiness and wait for it.

## Starting state

- Workspace: the existing `lumina-work-cleanup/lumina` checkout attached to this task.
- Branch prepared for continuation: `codex/dogfood-next`, based on upstream `main` at `b8558b4` (merged [PR #169](https://github.com/shiftedx/lumina/pull/169)). This branch also carries this documentation-only handoff.
- The previous implementation batch is merged; its working tree was clean. Its proof server was stopped. Other worktrees belong to separate tasks.
- Signed iOS **0.1.0 (26)** was uploaded through Xcode Organizer on October 27, 2026; Organizer confirmed “Uploaded to Apple,” build 26. Processing and tester availability were not verified. Build 26 is consumed; preserve its archives, symbols, hashes and logs.

## Work each issue

1. **Verify the base.** Read the current `AGENTS.md`, inspect working-tree changes, and fetch upstream. Account for any edits before updating refs. Start implementation from current upstream code plus the retained handoff; preserve other tasks' worktrees and work.
2. **Define the result.** Use the owner's screenshot/reproduction to identify the affected view, platform and posture. For Duo, closed/folded means the smaller outer screen; open/unfolded means the large inner screen. State the observable acceptance criterion before editing. Inspect connected code/context before asking for information already available.
3. **Make a small fix.** Trace repeated symptoms to their existing shared component. Preserve the app's identity and surrounding behavior. Use bounded subagent packets for independent work, following the live routing policy in `AGENTS.md`; give each packet distinct ownership and require diff/test/evidence results. The parent integrates and reviews the combined result.
4. **Verify the user workflow.** Capture comparable before/after renders for visual changes and exercise the affected action. Extend an existing meaningful public workflow or boundary check. Keep iteration tests focused; consult [verification guidance](../VERIFICATION.md) and satisfy the repository's required gates before a PR. Record every remaining failure accurately; a timeout or familiar test name alone does not establish a baseline failure.
5. **Batch and report.** Commit accepted fixes in reviewable units and leave the working tree accounted for. Tell the owner what changed, what was exercised, and what remains unverified. Keep collecting the small fixes until the owner explicitly requests the next release.

An issue is complete when its acceptance criterion is observable in the running app, its focused regression check passes, and the change preserves the contracts below. A source edit or screenshot alone is insufficient for a behavior fix.

## Preserve these contracts

- **Playback continuity:** one durable playback session/player survives presentation and size/posture changes. A view reconstruction must not restart playback from the server's older saved position. Preserve existing legitimate teardown/track-transition behavior. When touching playback presentation or lifecycle, retain the direct and client-demux rebuild checks for live position, paused state, and Close→fresh Play.
- **Reader:** preserve EPUB/PDF/CBZ/CBR position, appearance, fit/zoom and page navigation. The chapter/page label is intentionally plain text.
- **Artwork and controls:** full-page artwork leads; secondary content follows below. Home has only the minimum logo/nav corner scrim. Detail views omit the navigation wordmark. Keep semantic labels, shared gutters, usable targets and aligned controls.
- **Icons/branding:** use Lumina's bespoke SVG assets. For icon changes, read [the icon guide](../../assets/icons/STYLE_GUIDE.md); for logo/color/button/navigation changes, read [the brand contract](../../apple/docs/brand-assets.md). Use the installed Impeccable skill when auditing or polishing UI.
- **Casting:** preserve ticket/session ownership and explicit return-to-device behavior. Simulator zero-device/network guidance proves picker/resource loading, not physical receiver discovery or remote playback.

## Read only when needed

| Trigger | Reference |
| --- | --- |
| A regression against build 26, or details of the completed view-by-view batch | [Build 26 validation handoff](dogfood-validation-handoff.md) |
| Playback/receiver/reader validation needs existing evidence | Ignored `output/dogfood-build-26/` and `output/visual-polish-2026-10-27/README.md` in this checkout |
| Cast SDK version, linkage, resources or archive packaging changes | [Cast resource provenance](../../apple/Resources/GoogleCast/README.md), archive validation contracts, and the real SDK picker workflow |
| Owner explicitly requests another signed build/upload | [TestFlight workflow](../../apple/docs/testflight.md) and the retained release manifest below |

## Release boundary

The previous authorization to package/upload was fulfilled by build 26. Treat the next release as a separate owner-requested step; verify an unused build number before packaging. Use Xcode for an authorized upload. Browser login to App Store Connect remains outside authorization.

The local source of truth for build 26 is ignored `apple/Distribution/0.1.0-26/ReleaseManifest.json`, alongside the signed IPA/Organizer archive, source snapshot, hashes and `What-to-Test.txt`. Keep superseded/intermediate packages separate from the uploaded package. Preserve needed ignored release artifacts before archiving or removing a worktree.

Known qualification limits are recorded in the build 26 handoff: the full repository gate was not entirely green, and physical fold/decoder behavior, real receivers and some native appearances still need device validation. Compare new failures against that evidence; keep unexecuted checks labeled unverified. The IPA ships native iOS code; web/backend changes follow their server deployment path.
