# Personal and server settings: approved design

Status: approved by the owner on 2026-11-01; production integration pending. This design is included in [the authoritative web implementation plan](web-native-implementation-plan.md) and its Settings work packet. The existing filename is retained for review links; this is no longer an optional proposal.

## Intent

Personal settings shape one member’s experience. Server management changes household access, resources and integrations. Keep these spaces separate while sharing controls, typography and navigation behavior.

Settings uses a quiet task-oriented layout rather than media billboards: compact sans-serif headings, aligned setting rows, a searchable section rail and meaningful inline previews. Artwork appears in the caption preview because it helps judge the setting. Server overview prioritizes health and actionable issues over decorative metrics.

## Review entry points

Run the existing development prototype on port 4337 with `mockup=native&variant=A`:

- `screen=settings`: My settings → Profile & security.
- `screen=settings&setting=playback`: Playback, including caption sizing preview.
- `screen=manage`: Manage server → Overview & activity.
- `screen=manage&setting=library`: Library, storage and playback optimization.
- `screen=manage&setting=connections`: Sonarr/Radarr, metadata/imports and email.

Both spaces are also reachable from the Account menu and main navigation footer. `setting` chooses an initial review section; it is not a replacement production route model.

Local comparison gallery: `http://127.0.0.1:4338/settings.html`, when the existing ignored review server/artifacts are available. The committed implementations are `SettingsFrame.tsx`, `PersonalSettingsMock.tsx`, `AdminSettingsMock.tsx` and their scoped styles under `frontend/src/prototypes/native-web/`.

## Accepted structure

| My settings | Representative content |
| --- | --- |
| Profile & security | Display name, two-step verification, password actions. |
| Playback | Episode autoplay, quality ceiling, loudness, caption preview/size. |
| Display | System, Light, Dark, OLED. |
| Discovery & streaming | Optional provider visibility, with YouTube retained. |
| Downloads | Member download defaults. |
| Privacy & apps | Personal data, search history, Jellyfin/Plex watch-history import, connected apps and optional YouTube session. |

| Manage server | Representative content |
| --- | --- |
| Overview & activity | Health, attention queue, active streams and background work. |
| Household | Members, access details and invitations. |
| Library & storage | Folders, scans, editing permission, playback/conversion limits and advanced upkeep. |
| Connections | Sonarr/Radarr, metadata, history sources and SMTP. |
| AI features | Feature availability, local models and optional assistant connection. |
| Maintenance | Backups, update checks, tasks and redacted diagnostics. |

These groups follow the existing `features/settings/navigation.ts` categories. Representative controls are not an exhaustive registry implementation. Preserve every existing registry row, provider and capability when integrating, including personal recommendation controls, allowances, request notification policies, household migration, request rules, settings not shown here, and their existing destinations.

## Interaction contract

- One settings search entry. The prototype filters sections by labels/keywords; production must retain its full searchable registry, row matches and focused deep-link targets.
- Desktop uses a persistent settings rail inside the on-demand global shell. Phone uses a section picker; search reveals matching sections. Global media tabs are hidden within settings to leave room for forms; the menu and Account remain accessible.
- Heading identifies the selected section, with explicit personal/household scope. Approximately 28px desktop/25px phone; section titles 17px, control labels 14px, supporting text 13px. Shared media billboard typography does not become form typography.
- Autosaved preferences say Autosaves. Explicit forms say Manual save; a sticky Save/Discard bar appears only while dirty. Switching sections with unsaved edits offers Keep editing or Discard changes.
- Connection testing is separate from saving. Secrets use blank password replacement fields and a saved-state indication; saving the demo clears typed secret fields. Real integration retains write-only secret handling and field-specific payload semantics.
- Advanced tuning stays in disclosures. Household-wide effect remains visible; personal theme/playback choices do not masquerade as server policy.

## Production preservation gates

Reuse `SettingsSurface`, `SettingsSearch`, the registry/section providers, `SectionForm`, `unsavedChanges.ts`, current `settingsHost` bindings and actual `/settings` versus `/manage` routes. Adapt presentation without replacing their permission or save contracts with one global mock draft.

The prototype uses a synthetic owner persona and temporary local values; it does not establish authorization. Production members see only personal spaces; owner-only direct URLs retain the existing explanation/guard. Personal settings remain available to owners as their own profile, not a disguised server form.

The prototype’s section-navigation confirmation covers only its local form. Production must preserve unsaved guards across category, search, member detail, global navigation and route changes. Existing immediate actions and per-section explicit-save behavior retain their current semantics. Keep key-merging, validation failures, restart-required impact and cancellation behavior.

Some mock buttons provide local status previews rather than full setup, security, import or diagnostic workflows. Production retains those existing real flows. The UI audit established actual control groups, not a new backend contract; reader preferences remain contextual to Books rather than an invented global Reading category.

## Evidence and integration

The review checks exercised all twelve sections at 1440, 390 and 320px without document overflow or API calls. They cover profile save/discard, unsaved navigation, playback toggles, scoped settings search/shortcut, server draft save/discard and Account entry to both spaces. Section review links and desktop/phone captures are in ignored `output/native-prototype-review/` artifacts. Frontend typecheck passed.

Integrate these accepted settings choices with the rest of the web UI through the authoritative plan’s Settings packet. The implementation orchestrator owns shared shell/search/style contracts; the Settings worker owns registry presentation and section integration with focused `navigation`, `unsavedChanges`, settings registry, requests and resource regressions. Keep actual server/account mutations out of visual-review fixtures.
