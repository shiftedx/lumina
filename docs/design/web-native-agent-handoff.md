# Handoff: integrate the approved Lumina web UI

Approved reference synchronized from `5d4881a`; settings prototype `f5486e4482d81de13b549eb3bc0e5415d1f8295c`. Integration results, ownership and focused verification are recorded in [the integration status](web-native-integration-status.md), [settings registry coverage](web-settings-capability-matrix.md) and [the design review](web-native-design-review.md). This settings extension is local and undeployed; the prior media review deployment remains separate. The approved reference instructions follow.

You are the implementation orchestrator. Use a team of subagents to integrate the approved native-inspired design into the real web app, retaining existing capabilities and server-backed state. This includes the approved My settings and Manage server rework alongside all media surfaces. The next task is production code integration and review, not another round of mockups.

## Read first

1. Read the current `AGENTS.md`, `PRODUCT.md`, `apple/docs/brand-assets.md`, and the owner testing instruction in `docs/design/dogfood-next-handoff.md`.
2. Read [the implementation plan](web-native-implementation-plan.md). It is the authoritative scope, ownership sequence, capability map and acceptance criteria. In particular, read **Capability gates to settle first** before assigning backend work.
3. Read [the accepted personal/admin settings design](web-settings-design-proposal.md). It is approved scope, not optional follow-up. Preserve the complete production registry and its permission/save contracts; the mockups show representative controls.
4. Compare the pinned A prototype with current production components. Use Impeccable for the frontend work and the applicable architecture/implementation skills. Preserve the user's approved choices rather than replacing them with a new visual direction.

Done: you can name the production implementation base, the pinned prototype, the shared-file owner, every work packet and the two known backend/data gaps below.

## Pinned review reference

- Design branch: `codex/web-native-mockups`.
- Prototype snapshot: `f5486e4482d81de13b549eb3bc0e5415d1f8295c`; original application base: `ae2d56e7`.
- Committed source: `frontend/src/prototypes/native-web/`; A is `VariantNative.tsx` with its media-specific views. B/C are earlier comparisons.
- The current plan and handoff updates follow that prototype snapshot on the same design branch.
- Current local review worktree: `/Users/kmcdowell/.codex/visualizations/2026/11/01/01a194dd-a76e-7d33-9453-86cda62d2abd/lumina-web-native-prototype`.
- Interactive preview, while running: `http://127.0.0.1:4337/?mockup=native&variant=A&screen=music`. Other `screen` values: `home`, `movies`, `shows`, `books`, `detail`, `requests`, `streaming`, `settings`, `manage`. For settings sections, initial review links accept `setting=playback`, `library`, `connections`, etc.; production retains its existing settings routes.
- Settings comparison gallery: `http://127.0.0.1:4338/settings.html`, when running.
- Comparison gallery/captures: local `output/native-prototype-review/`, optionally served at port 4338. Those generated review files/scripts are ignored artifacts, not a portable implementation dependency. Regenerate captures from the committed prototype if absent.
- To run the prototype elsewhere, use the repository's locked bootstrap workflow, then `mise exec -- npm --prefix frontend run dev -- --host 127.0.0.1 --port 4337` in the design worktree. Keep the `mockup=native` query.

Use a fresh implementation branch/worktree from the current intended application base. Inspect existing work and branch divergence before choosing that base. Use the pinned prototype as reference; preserve its branch and review artifacts. Integrate production code through the real components rather than merging the entire design branch: the development gate, fictional media, fixture progress, DOM-event glue and review controls are not shipping application behavior.

## Two known production gaps

- **Recently played albums:** added albums already have a real title query, but played albums need a bounded per-member album aggregation over existing track history. The plan names the existing endpoints that are not substitutes and the proposed seam. Do not use release year, acquisition order, recommendations or highlights as listening recency.
- **Manga/comic chapters:** existing series/volume/page/RTL support is real; EPUB chapters do not establish a CBZ/CBR chapter model. Resolve the source and representation of actual chapter boundaries before implementing the requested chapter flow. Record any necessary additive backend contract. Keep this gap visible until implemented or a specific product decision is obtained.

The production music view preference should use the existing authenticated-member `ui_prefs` path, not the prototype's synthetic browser key. Preserve explicit deep-linked route/filter state before applying the remembered default.

## Launch the team

Follow the current `AGENTS.md` routing policy and available model/tool capabilities. Maximum concurrency is orchestrator plus three workers. The orchestrator owns global routing, shell, tokens, search, shared API/types, preference plumbing and the integration record.

1. Complete the plan's baseline/capability inventory and shared-shell vertical slice. Record ownership and the current route-to-test map in `docs/design/web-native-integration-status.md`.
2. Launch the three media packets from the plan: Video/Home, Music, Books. Keep their file ownership disjoint. The Books worker owns the chapter backend packet after its prerequisite contract is settled; shared schemas/models, migrations/types and rollback documentation stay with the orchestrator, as bounded in the plan.
3. Integrate and review that wave. Reuse the three workers for Requests, Streaming/watch, and Settings. The Settings worker owns `features/settings/` and affected settings contracts; global shell/routes/tokens, workspace and API changes stay with you. Require a complete registry-row coverage map and the approved personal/owner layouts. Once one implementation packet finishes, reuse that slot for the independent read-only cross-surface review; never exceed three workers beside you.
4. Complete the plan's verification matrix and return the integrated revision, captures, test evidence and remaining limits. Keep deployment, marketing-site edits and native releases outside this task.

Use this packet for every worker:

```text
Task: <one bounded result from the implementation plan>
Base and dependencies: <implementation revision; shared contracts already available>
Owned files: <explicit paths and test files; shared-file requests go to orchestrator>
Retained behavior: <existing workflows, permissions and relevant ADR/test pointers>
Done evidence: <files/diff, exact commands and results, browser captures, remaining gaps>
Coordination: Others are editing this worktree. Preserve their work and adapt to it.
Escalation: Report a missing contract or failed refined retry with evidence; do not guess API/data behavior.
```

A worker report closes a packet only when the orchestrator verifies its required public flows. A clean typecheck or attractive screenshot alone does not close a production playback/reader/authorization packet.

## Latest prototype qualification

The final Music mock uses Recently played/Recently added fixture shelves, an All albums disclosure and a visible Albums/Artists switch. Only that browsing choice persists, under `lumina:prototype:music-browse-tab:v1:demo-alex`; all other demo state remains temporary. Browser checks at 1440, 390 and 320px verified correct album entry, catalog access, choice across route unmount/reload, preservation through artist→album navigation, malformed storage fallback and usable behavior with storage blocked. Typecheck passed.

Earlier captured flows cover navigation auto-hide/keyboard return; show catalog→series→season→episode; artist→album→tracks; Books details→volume/chapter→reader with saved resume; Requests and provider-qualified Streaming. These qualify the prototype interaction intent, not real server playback or reader contracts. Requalify the production integration against actual data, permissions and existing tests using the plan.

Settings were approved on 2026-11-01. Prototype checks covered all twelve personal/admin sections at 1440, 390 and 320px: scoped search/shortcut, profile save/discard, unsaved section navigation, server drafts, responsive controls and Account entry to both spaces. No account/server actions were executed. Production qualification must additionally exercise member/owner permissions, complete registry coverage, real persistence, connection tests, validation failures, write-only secrets and global unsaved-route guards.

## Completion report

Return a reviewable implementation, not a proposal to start: revision/branch, completed capability matrix, meaningful changes, exact validation, desktop/phone captures, and remaining decisions or unrun gates. Apply the current owner instruction for focused testing; a full repository/Core/native UI suite requires the owner's direct request. Explain any release/PR gate that remains unrun. Preserve feature gaps as explicit unfinished work rather than silently removing the approved flow.
