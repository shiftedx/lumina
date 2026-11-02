# Approved settings capability matrix

Reference: approval `5d4881a`, prototype `f5486e4482d81de13b549eb3bc0e5415d1f8295c`. All 105 production registry rows have one destination across 52 stable subpages. `features/settings/navigation.test.ts` checks this against the actual registry; browser registry walks also cover the 27 member-detail rows. This table documents the real control providers, not prototype representative controls.

Personal URLs affect the authenticated member. Manage URLs and member-detail administration remain owner-only; legacy `/settings/<server-section>` and `/admin/...` links remain supported. Each row below retains its original ID, provider and validation/payload contracts. A subpage is an in-page destination within its category, not a replacement public router.

Manual save keeps a local draft, guards navigation, retains failure input, and offers dirty-only Save/Discard. Autosaves retain their actual immediate/blur semantics and error handling; they are not converted into a synthetic form. Explicit actions (authentication, tests, imports, exports, token operations, backup operations, destructive confirmations) keep their real staged or immediate workflows. A connection test does not save its draft. Write-only keys/passwords remain blank replacement fields with configured-state indicators and field-specific replace/remove semantics.

| Registry row | Space / category | Existing destination | Save / action behavior |
| --- | --- | --- | --- |
| `account.profile` | My settings / profile | `/settings/account?page=profile` → Profile | Manual save + guarded draft |
| `account.resources` | My settings / profile | `/settings/account?page=allowances` → Your allowances | Read-only |
| `account.password` | My settings / profile | `/settings/account?page=security` → Security | Explicit action/workflow |
| `account.two-factor` | My settings / profile | `/settings/account?page=security` → Security | Explicit action/workflow |
| `account.request-notifications` | My settings / profile | `/settings/account?page=notifications` → Notifications | Manual save + guarded draft |
| `playback.autoplay` | My settings / playback | `/settings/playback?page=watching` → Watching | Autosaves |
| `playback.max-quality` | My settings / playback | `/settings/playback?page=watching` → Watching | Autosaves |
| `playback.loudness` | My settings / playback | `/settings/playback?page=watching` → Watching | Autosaves |
| `playback.stream-cache` | My settings / playback | `/settings/playback?page=watching` → Watching | Autosaves |
| `playback.captions` | My settings / playback | `/settings/playback?page=captions` → Subtitles | Autosaves |
| `playback.auto-skip` | My settings / playback | `/settings/playback?page=skipping` → Skipping & language | Autosaves |
| `playback.mute` | My settings / playback | `/settings/playback?page=skipping` → Skipping & language | Autosaves |
| `playback.mute-words` | My settings / playback | `/settings/playback?page=skipping` → Skipping & language | Manual save + guarded draft |
| `appearance.theme` | My settings / display | `/settings/appearance?page=display` → Display | Autosaves |
| `discovery.interests` | My settings / discovery | `/settings/discovery?page=interests` → Interests | Manual save + guarded draft |
| `discovery.hidden` | My settings / discovery | `/settings/discovery?page=hidden` → Hidden recommendations | Explicit action/workflow |
| `discovery.history` | My settings / discovery | `/settings/discovery?page=recommendation-history` → History | Explicit action/workflow |
| `streaming.providers` | My settings / discovery | `/settings/streaming?page=providers` → Providers | Autosaves |
| `downloads.defaults` | My settings / downloads | `/settings/downloads?page=downloads` → Downloads | Autosaves |
| `privacy.export` | My settings / privacy | `/settings/privacy?page=data` → Your data | Explicit action/workflow |
| `privacy.search-history` | My settings / privacy | `/settings/privacy?page=data` → Your data | Explicit action/workflow |
| `privacy.jellyfin-history` | My settings / privacy | `/settings/privacy?page=watch-history` → Import watch history | Explicit action/workflow |
| `privacy.plex-history` | My settings / privacy | `/settings/privacy?page=watch-history` → Import watch history | Explicit action/workflow |
| `playback.listening-export` | My settings / playback | `/settings/playback?page=listening-export` → Listening history export | Explicit action/workflow |
| `apps.connected` | My settings / privacy | `/settings/apps?page=apps` → Connected apps | Explicit action/workflow |
| `apps.youtube` | My settings / privacy | `/settings/apps?page=youtube` → YouTube | Explicit action/workflow |
| `about.credits` | My settings / privacy | `/settings/about?page=about` → About Lumina | Read-only |
| `overview.status` | Manage server / overview | `/manage/overview?page=dashboard` → Dashboard | Read-only |
| `activity.now` | Manage server / overview | `/manage/activity?page=playing` → Now playing | Read-only + operational actions |
| `activity.server` | Manage server / overview | `/manage/activity?page=playing` → Now playing | Read-only |
| `activity.background` | Manage server / overview | `/manage/activity?page=playing` → Now playing | Read-only |
| `activity.history` | Manage server / overview | `/manage/activity?page=playback-history` → Playback history | Read-only |
| `members.list` | Manage server / household | `/manage/members?page=members` → Members | Member list/actions; member access edits use guarded manual save |
| `members.resources` | Manage server / household | `/manage/members?page=resources` → Resources | Manual save + guarded draft |
| `members.invitations` | Manage server / household | `/manage/members?page=invitations` → Invitations | Explicit action/workflow |
| `requests.enabled` | Manage server / household | `/manage/requests?page=request-rules` → Request rules | Autosaves |
| `requests.policies` | Manage server / household | `/manage/requests?page=request-rules` → Request rules | Manual save + guarded draft |
| `requests.member-policies` | Manage server / household | `/manage/requests?page=request-rules` → Request rules | Manual policy dialog + explicit reset |
| `members.owner-two-factor` | Manage server / household | `/manage/members?page=household-security` → Security | Autosaves |
| `members.jellyfin` | Manage server / household | `/manage/members?page=member-import` → Import members | Explicit action/workflow |
| `members.plex` | Manage server / household | `/manage/members?page=member-import` → Import members | Explicit action/workflow |
| `library.storage` | Manage server / library | `/manage/library?page=folders` → Folders | Guarded manual rules; explicit add/probe/import/disable/remove actions |
| `library.imports` | Manage server / library | `/manage/library?page=imports` → Imports | Explicit action/workflow |
| `library.automation` | Manage server / library | `/manage/library?page=automation` → Automation | Autosaves policy; explicit scan action |
| `library.retention` | Manage server / library | `/manage/library?page=automation` → Automation | Manual save + guarded draft |
| `library.anime` | Manage server / library | `/manage/library?page=organization` → Organization | Manual save + guarded draft |
| `library.editing` | Manage server / library | `/manage/library?page=organization` → Organization | Autosaves |
| `library.artwork` | Manage server / library | `/manage/library?page=organization` → Organization | Explicit action/workflow |
| `media.unmatched` | Manage server / library | `/manage/media?page=matching` → Fix matches | Explicit action/workflow |
| `media.tmdb-key` | Manage server / connections | `/manage/media?page=metadata` → Metadata | Manual save + guarded draft |
| `media.omdb-key` | Manage server / connections | `/manage/media?page=metadata` → Metadata | Manual save + guarded draft |
| `media.language` | Manage server / connections | `/manage/media?page=metadata` → Metadata | Manual save + guarded draft |
| `media.introdb` | Manage server / connections | `/manage/media?page=metadata` → Metadata | Manual save + guarded draft |
| `media.refresh-all` | Manage server / connections | `/manage/media?page=metadata` → Metadata | Explicit action/workflow |
| `media.jellyfin` | Manage server / connections | `/manage/media?page=players` → Media apps | Manual save + guarded draft |
| `media.native-streaming` | Manage server / connections | `/manage/media?page=players` → Media apps | Manual save + guarded draft |
| `media.jellyfin-address` | Manage server / connections | `/manage/media?page=players` → Media apps | Read-only |
| `media.autoplay-presence` | Manage server / connections | `/manage/media?page=players` → Media apps | Manual save + guarded draft |
| `members.public-address` | Manage server / connections | `/manage/members?page=addresses` → Addresses | Manual save + guarded draft |
| `members.local-address` | Manage server / connections | `/manage/members?page=addresses` → Addresses | Manual save + guarded draft |
| `requests.sonarr` | Manage server / connections | `/manage/requests?page=sonarr` → Sonarr | Manual save + guarded draft |
| `requests.anime-language` | Manage server / connections | `/manage/requests?page=sonarr` → Sonarr | Manual save + guarded draft |
| `requests.radarr` | Manage server / connections | `/manage/requests?page=radarr` → Radarr | Manual save + guarded draft |
| `requests.integrations` | Manage server / connections | `/manage/requests?page=integrations` → Integrations | Explicit action/workflow |
| `requests.smtp` | Manage server / connections | `/manage/requests?page=email` → Email | Manual save + guarded draft |
| `requests.smtp-test` | Manage server / connections | `/manage/requests?page=email` → Email | Explicit action/workflow |
| `media.jellyfin-import` | Manage server / connections | `/manage/media?page=jellyfin` → History imports | Manual save + guarded draft |
| `media.plex-import` | Manage server / connections | `/manage/media?page=jellyfin` → History imports | Manual save + guarded draft |
| `ai.semantic-search` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.subtitles-from-speech` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.sync` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.translate` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.recap` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.episode-summaries` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.key-scenes` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.smart-collection-builder` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.match-tie-breaker` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.mute-strong-language` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.personal-recommendations` | Manage server / ai | `/manage/ai?page=features` → Features | Manual save + guarded draft |
| `ai.search-model` | Manage server / ai | `/manage/ai?page=models` → Local models | Manual save + guarded draft |
| `ai.speech-model` | Manage server / ai | `/manage/ai?page=models` → Local models | Manual save + guarded draft |
| `ai.model-threads` | Manage server / ai | `/manage/ai?page=models` → Local models | Manual save + guarded draft |
| `ai.assistant-address` | Manage server / ai | `/manage/ai?page=assistant` → Assistant server | Manual save + guarded draft |
| `ai.assistant-model` | Manage server / ai | `/manage/ai?page=assistant` → Assistant server | Manual save + guarded draft |
| `ai.api-key` | Manage server / ai | `/manage/ai?page=assistant` → Assistant server | Manual save + guarded draft |
| `ai.assistant-test` | Manage server / ai | `/manage/ai?page=assistant` → Assistant server | Explicit action/workflow |
| `ai.max-concurrency` | Manage server / ai | `/manage/ai?page=assistant` → Assistant server | Manual save + guarded draft |
| `ai.context-window` | Manage server / ai | `/manage/ai?page=assistant` → Assistant server | Manual save + guarded draft |
| `ai.embedding-model` | Manage server / ai | `/manage/ai?page=assistant` → Assistant server | Manual save + guarded draft |
| `ai.speech-address` | Manage server / ai | `/manage/ai?page=speech` → Speech server | Manual save + guarded draft |
| `ai.speech-server-model` | Manage server / ai | `/manage/ai?page=speech` → Speech server | Manual save + guarded draft |
| `ai.status` | Manage server / ai | `/manage/ai?page=ai-activity` → Activity | Read-only |
| `backups.list` | Manage server / maintenance | `/manage/backups?page=backups` → Backups | Explicit action/workflow |
| `backups.schedule` | Manage server / maintenance | `/manage/backups?page=backups` → Backups | Manual save + guarded draft |
| `tasks.list` | Manage server / maintenance | `/manage/tasks?page=tasks` → Tasks | Explicit action/workflow |
| `diagnostics.updates` | Manage server / maintenance | `/manage/diagnostics?page=updates` → Updates | Autosaves check/notification preferences; explicit check action |
| `diagnostics.report` | Manage server / maintenance | `/manage/diagnostics?page=diagnostics` → Diagnostics | Read-only |
| `transcoding.hwaccel` | Manage server / library | `/manage/transcoding?page=transcoding` → Video conversion | Manual save + guarded draft |
| `transcoding.max-sessions` | Manage server / library | `/manage/transcoding?page=transcoding` → Video conversion | Manual save + guarded draft |
| `transcoding.cache` | Manage server / library | `/manage/transcoding?page=transcoding` → Video conversion | Manual save + guarded draft |
| `transcoding.diagnostics` | Manage server / library | `/manage/transcoding?page=transcoding` → Video conversion | Explicit action/workflow |
| `overview.concurrency` | Manage server / library | `/manage/overview?page=download-limits` → Download limits | Manual save + guarded draft |
| `overview.per-member` | Manage server / library | `/manage/overview?page=download-limits` → Download limits | Manual save + guarded draft |
| `overview.min-free` | Manage server / library | `/manage/overview?page=download-limits` → Download limits | Manual save + guarded draft |
| `overview.source-ports` | Manage server / library | `/manage/overview?page=download-limits` → Download limits | Manual save + guarded draft |

Member details retain all 27 existing `members.*` registry rows through `/manage/members/:id` (and legacy `/settings/members/:id`), across identity, libraries/ratings, streaming, schedule/screen time, resources, follows and activity. Access drafts remain guarded; follow/unfollow, bonus-time, activation and reset/security operations retain their explicit action contracts. Full registry search finds rows in advanced disclosures and focuses their actual fields; members cannot search or mount owner rows.

Validation and desktop/mobile evidence are recorded in [the integration status](web-native-integration-status.md) and [the design review](web-native-design-review.md). Provider credentials and real external account operations are not exercised by visual fixtures.
