# Native Apple App Store readiness — 2026-10-17

## Scope and evidence boundary

This is a policy/readiness review for the proposed paid, native Swift app on
iPhone, iPad, the planned iPhone Duo target, and tvOS.  Its launch scope is a
household's authorised local video library over LAN or remote HTTPS, with
browse, search, playback, profiles, progress, and AI discovery.  Administration
stays on the web.  It excludes YouTube/Twitch/Kick acquisition, live chat, and
third-party-media downloading.  It is not an approval guarantee.

There is no native Xcode project, `Info.plist`, entitlement file, privacy
manifest, StoreKit configuration, or Apple SDK binary in the current tree.  So
this report identifies the implementation and submission gates; it does not
claim those client artefacts are presently wrong.  The deployed server is
AGPL-3.0-or-later ([`LICENSE`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/LICENSE:1),
[`backend/pyproject.toml`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/pyproject.toml:8)).
That does not by itself prevent a paid, separately licensed native client.

Primary policy sources: [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/),
[App Privacy in App Store Connect](https://developer.apple.com/help/app-store-connect/manage-app-information/manage-app-privacy/),
[offering account deletion](https://developer.apple.com/support/offering-account-deletion-in-your-app/),
[local-network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy),
[insecure-network connections / ATS](https://developer.apple.com/documentation/Security/preventing-insecure-network-connections),
[media-playback configuration](https://developer.apple.com/documentation/avfoundation/configuring-your-app-for-media-playback),
[IPv6 support](https://developer.apple.com/support/ipv6/),
[launch-screen preparation](https://developer.apple.com/documentation/technotes/tn3208-preparing-your-apps-launch-screen-to-meet-app-store-requirements),
and [App Store Connect release notes](https://developer.apple.com/help/app-store-connect/release-notes/).

## 1. Executive summary

- **Allowed launch model.** A paid native player may browse and stream a
  household's authorised local library.  Apple does not treat that as a
  prohibited third-party acquisition service.  Rights to the files remain the
  household's responsibility under guideline 5.2.
- **P0 review gate:** guideline 2.1 requires an active, functional backend and
  a review account, or prior approval for a fully functional demo alternative.
  A self-hosted server that a reviewer cannot reach is not sufficient.
- **P1 transport gate:** remote use should be HTTPS. The existing opt-in LAN
  HTTP mode needs a narrow ATS/local-network design. Prefer scoped exceptions;
  a broader exception needs an accurate technical justification for review.
- **P1 scope gate:** the server still contains public-source/acquisition
  features (including `yt-dlp` dependency); the native target, review account,
  screenshots, and metadata must not expose or advertise them.  This avoids a
  5.2.2/5.2.3 question, rather than asserting that all YouTube access is banned.
- **P1 privacy gate:** publish an accurate App Privacy answer and policy before
  submission.  If a person’s prompts, transcripts, or media-derived data is
  sent to a third-party AI provider, guideline 5.1.2(i) requires explicit
  permission before that sharing.
- **Fast wins:** prepare a review-access server/account and test catalogue;
  lock the native route allow-list to the launch scope; write permission and
  data-flow copy from the actual client build; and place a paid-app/server
  requirement disclosure in the product page and review notes.

## 2. Risk register

| Priority | Area | Finding / evidence | Why review might reject | Recommendation | Effort | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | Review access | No native app is checked in; Lumina normally starts loopback-only and remote access needs a TLS proxy ([ADR 0001](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/docs/adr/0001-loopback-first-deployment.md:10)). | Reviewer cannot exercise the paid app (2.1). | Supply a reachable HTTPS review server, seeded authorised test media, test member, exact steps, and a reset path. | 1–3 d | High |
| P1 | Content rights | Server dependency describes itself as `yt-dlp` powered ([`pyproject.toml`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/pyproject.toml:8)); native scope excludes it. | A client that facilitates unlicensed download/conversion of third-party services violates 5.2.3; service terms/authorisation may be requested under 5.2.2. | Enforce and document a native-only local-library contract; preflight routes, deep links, copy, screenshots, and review fixture. | 2–5 d | High |
| P1 | Privacy / AI | Admin may configure any syntactically valid HTTP(S) AI/ASR endpoint ([`admin_ai.py`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/app/routers/admin_ai.py:49), [`local_ai.py`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/app/services/local_ai.py:49)). | Personal data sent to an external AI endpoint without explicit consent violates 5.1.2(i). | Ship local/self-hosted AI only for launch, or add just-in-time informed consent and policy/label disclosure before external sharing. | 1–3 d | High |
| P1 | LAN / ATS | LAN HTTP is deliberately allowed and carries passwords/tokens/media in plaintext ([ADR 0001](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/docs/adr/0001-loopback-first-deployment.md:34)). | Broad ATS exception or unexplained local-network prompt reduces reviewability/security. | HTTPS by default; request local-network permission only after “Connect to server”; narrowly justify any local HTTP exception. | 2–4 d | High |
| P1 | Privacy disclosures | Browser sends anonymous loading samples to `/api/metrics/client` ([`client_metrics.py`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/app/routers/client_metrics.py:1)); native collection is unknown. | Inaccurate App Privacy answers/policy breach 5.1.1. | Inventory the actual native build and server destinations; publish policy URL (and tvOS policy text in App Store Connect) before submit. | 1–2 d | High |
| P2 | Accounts | Existing server is household account sign-in; connected-app credentials are minted only from a browser session ([`connected_apps.py`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/app/routers/connected_apps.py:26)). | Missing in-app deletion only becomes a 5.1.1(v) issue if the app supports account creation, including a web creation link/guest account. | Keep launch sign-in to pre-provisioned household accounts, or add in-app deletion and server deletion workflow if creation is introduced. No Sign in with Apple requirement for this own-account-only design. | 0–5 d | High |
| P2 | Paid distribution | Decision is paid upfront; no IAP/subscription scope. | Unclear price versus required server/paid feature unlock may mislead; inappropriate external payment path would trigger 3.1. | Use one universal-purchase record for iOS/iPad/tvOS if developer-account setup supports it; state “requires a compatible Lumina household server” plainly. Do not add feature unlocks without revisiting IAP. | 1–2 d | Medium |
| P2 | Native value / quality | No native UI exists yet. | A web wrapper or incomplete client fails 4.2/2.1. | Use native browse, profile, player, captions/audio selection and usable empty/offline/error states; test device playback. | Client work | High |
| P2 | Decoder licensing | No candidate playback engine or binary has been selected in this tree. | Copyleft/proprietary codec terms may constrain distribution. | Gate the selected engine, linked codecs, and distribution method on exact license/build review. AGPL server status is not a blanket bar; GPL/LGPL is not automatically unshippable. | 1–3 d | Medium |
| P2 | Networking | ADR LAN mode accepts only private IPv4 literals ([ADR 0001](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/docs/adr/0001-loopback-first-deployment.md:34)). | A client that assumes IPv4 can fail Apple’s IPv6-only network review expectation (2.5.5). | Make client URL handling family-agnostic and test DNS64/NAT64. This is not a mandate to redesign the current private-IPv4 server mode. | 1–2 d | High |

## 3. Detailed findings

### Privacy & data handling

Guideline 5.1.1 requires an in-app and App Store Connect privacy policy that
states collected data, uses, third-party sharing, retention/deletion and how to
revoke consent.  App Privacy declarations must describe the shipping native
binary, not inferred browser behaviour.  Lumina currently has per-member
sessions, profiles/progress and browser loading metrics; therefore prepare the
data inventory from the final app, including server-hosted account and playback
data.  No native analytics or tracking SDK can be found yet, so none is claimed
as a present finding.

`local_ai.py` calls one admin-configured endpoint without redirects/proxy and
does not log request/response content ([`local_ai.py`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/app/services/local_ai.py:1)).  That supports a local-AI launch.  It does not prove every configured origin is owned by the household: URL validation admits HTTP or HTTPS hosts.  If launch guarantees local/self-hosted inference, say so accurately.  If a member’s personal data goes to a third-party AI service, obtain explicit affirmative permission before the request, and disclose the sharing.  ATT is only relevant if the future native app tracks users across apps/sites; no such claim can be made now.

The native contract should return a non-secret processing mode for each
AI-discovery capability: `household-local`, `external`, or `unknown`. For
`external`, withhold personal-data AI requests until that member has seen a
purpose/data/recipient disclosure and affirmatively consents. For `unknown`,
withhold those requests until the server can identify the processing destination
and required disclosure; consent to an unidentified recipient is insufficient.
Normal browse and playback must continue. Do not expose a private endpoint or
key merely to make this disclosure.

### Permissions & entitlements

Apple requires `NSLocalNetworkUsageDescription` when a native app directly or
indirectly connects to the local network; request it from a user action such as
Connect/Add Server, not at launch.  If Bonjour is used, declare only the actual
service types.  The local-network privacy prompt applies on iOS/iPadOS, not
tvOS.  Remote HTTPS is the release default.  For an opt-in private LAN HTTP
server, narrowly document an ATS local-network exception in the native project;
do not use `NSAllowsArbitraryLoads` as a shortcut.

For background audio, AirPlay or Picture in Picture, configure only the
necessary media-playback background mode and activate the audio session during
actual playback.  This is a capability check for the future native target, not
evidence that a permission is currently missing.

### Monetization

Launch is a paid-upfront app, with no subscription, in-app purchase, or paid
feature unlock.  State price, paid-app scope, and the compatible household
server requirement accurately in App Store metadata and reviewer notes.  The
App Review IAP rules must be revisited if a later release sells digital
features/access inside the app.  The reader-app external-link exception is not
needed for this release.  Complete the paid-app agreement, tax/banking and
pricing metadata in App Store Connect; those account-side facts are not
verifiable from this repository. One purchase can cover iOS/iPadOS and tvOS
using Apple's [universal purchase setup](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-platforms).
See also the [paid-app workflow](https://developer.apple.com/help/app-store-connect/get-started/app-store-connect-workflow).

### Account & authentication

Lumina supplies its own household accounts and password/session flows (for
example, password change at [`main.py`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/app/main.py:1658)).  Guideline 4.8 does not require Sign in with Apple for an app that uses its own account system rather than third-party/social login.  Account deletion is required in-app when the app supports account creation, including creation via a web link or guest account; it is not automatically required for a sign-in-only, administrator-provisioned household member app.  Re-evaluate before exposing any creation path.

Use an app-specific bearer/device credential or a secure native login/pairing
flow, not cookie-session copying.  Existing app credentials are browser-minted,
one-time shown and revocable ([`connected_apps.py`](/Users/kmcdowell/Documents/Codex/2026-10-10/i-w/work/lumina/backend/app/routers/connected_apps.py:26)); that is useful server groundwork, while the native UX and secure storage remain to be implemented.

### Content, UGC, and external links

| Scenario | Apple allows it when | Launch decision |
| --- | --- | --- |
| Stream a household’s own/licensed local library | The user has rights; the app does not facilitate infringement. | **In scope.** Say this in metadata/review notes and seed only authorised test files. |
| Link to/use a third-party site or service | Its terms permit the use and the developer can provide authorisation if asked (5.2.2). | **Out of scope** for native launch. |
| Save, convert, or download from Apple Music, YouTube, SoundCloud, Vimeo, etc. | Explicit authorisation; otherwise 5.2.3 forbids facilitating it. Streaming can also violate service terms. | **Out of scope.** No blanket claim that YouTube itself is banned; native must not offer this workflow. |
| User-generated live chat/community | Moderation, report/block and UGC controls where applicable. | **Out of scope.** |

The distinction matters because the server has broader capabilities, while the
native app is a deliberately narrower paid player. Treat native route exposure
as a release acceptance item, not a marketing promise alone. The same scope must
apply during review and production; do not enable excluded functionality after
review or present a different product to reviewers.

### Technical stability & performance

Guideline 2.1 requires a final, functional version with working URLs and
backend; 2.5.5 expects IPv6 compatibility.  Use URLSession/AVFoundation with
ordinary hostname/IPv6 support and test a DNS64/NAT64 environment.  Apple does
not require an immediate public-server IPv6 migration if the client works
through DNS64/NAT64.  Test subtitle/audio fallback, HLS/range authentication,
HDR/Atmos device capability fallback, interruption recovery, remote-network
loss, and server conversion errors against review media.  Those are quality
acceptance checks, not a representation that every source codec is supported.

### UX & reviewability

Guideline 4.2 requires sustained utility beyond a repackaged web site; a native
player with native playback, profiles, progress and server discovery can meet
that bar if it is complete.  The reviewer must be able to connect without
owning a home server.  Explain the paid app’s relationship to the free,
self-hosted server, show an actionable empty/server-unavailable state, and do
not ship an unconnected first screen with no review path.

## 4. Reviewer experience checklist

- [ ] A reviewer can install, sign in, and use a reachable HTTPS test server
  with supplied credentials; the account has authorised seed media and no
  external-service acquisition routes.
- [ ] First-run explains local versus remote server connection and asks for
  local-network access only after a user selects that action.
- [ ] Remote HTTPS works with no broad ATS exception; LAN HTTP, if retained,
  has its narrow exception and clear trusted-home-network copy.
- [ ] Browse, search, profiles, progress, AI discovery and playback complete
  with captions, audio selection, HDR/Atmos fallback and conversion failure
  states understandable to a reviewer.
- [ ] iPhone, iPad, planned Duo target and tvOS target have the declared
  adaptive UI; background media/AirPlay/PiP configurations are tested only
  where enabled.
- [ ] All required tvOS flows work with the Siri Remote, including sign-in,
  search and playback; phone-assisted entry is optional. See guideline 2.4.3
  and Apple's [Remote guidance](https://developer.apple.com/design/human-interface-guidelines/remotes).
- [ ] The current iOS SDK launch-screen requirement is met by the native target
  (for example, the Xcode SwiftUI default), and required future Duo assets are
  scheduled before Apple’s stated April 2027 deadline; no client artefact is
  available here to verify either item.
- [ ] Product page clearly states paid-upfront price and compatible Lumina
  server requirement; support and privacy-policy links work.
- [ ] App Privacy answers match the final binary/server destinations; external
  AI sharing, if enabled, has explicit permission before data leaves the home
  system.
- [ ] Empty library, expired/revoked token, offline server, unsupported media,
  and account sign-out remain useful and do not strand the user.

## 5. Suggested reviewer notes

> Lumina is a paid native household-media player for a user's own or otherwise
> authorised local library. It does not search, acquire, download, convert, or
> stream content from YouTube, Twitch, Kick, Apple Music, or other third-party
> services. It requires a compatible self-hosted Lumina server.
>
> Review access: server URL: **[HTTPS REVIEW URL]**. Account: **[REVIEW MEMBER
> NAME]**; password: **[DEDICATED PASSWORD VALID THROUGHOUT REVIEW]**. The account has seeded,
> authorised test video. Steps: sign in, open **[Library]**, play **[Test Title]**,
> choose captions/audio under **[Player control]**, then verify progress in
> **[Profile]**.
>
> The app requests local-network access only when the reviewer chooses **[Add
> local server]**. Remote review needs no local-network permission. **[If AI is
> local only: AI discovery uses the supplied self-hosted endpoint and sends no
> member data to a third-party AI provider.] [If external AI is enabled: the app
> presents explicit consent before sharing the described data.]**
>
> Purchase model: one paid-upfront app; no in-app purchases, subscriptions, or
> external purchase flow. Support: **[SUPPORT URL]**. Privacy policy:
> **[PRIVACY URL]**.

Use no production household credentials in these notes.  Replace every bracketed
placeholder shortly before submission.

## 6. Next pass: concrete alignment backlog

1. **Submission access (P0):** provision resettable HTTPS review infrastructure,
   least-privilege test member, authorised sample assets, support route and a
   documented reviewer script.
2. **Native scope boundary (P1):** define a client route/deep-link allow-list for
   library, search, profile, progress, AI discovery and playback; verify there
   is no acquisition/download/live-chat affordance or public-source copy.
3. **Privacy package (P1):** enumerate native-to-server and server-to-AI data
   flows; draft the policy, App Privacy answers, tvOS policy text and external-AI
   consent condition.  Add the non-secret `household-local`/`external`/`unknown`
   processing-mode response and pause personal-data AI requests until the
   external recipient is identified and the member consents. Unknown processing
   remains disabled until it can be disclosed. Recheck if analytics/SDKs are added.
4. **Native network package (P1):** implement/test foreground local-network
   prompting, actual Bonjour declarations if any, HTTPS remote connection,
   narrowly scoped LAN HTTP ATS handling, and IPv6/DNS64 client behaviour.
5. **Paid-app package (P2):** configure universal purchase if desired, paid-app
   legal/financial metadata, product-page server requirement, and no IAP path.
6. **Playback/legal gates (P2):** qualify real device/output-chain playback and
   review the exact selected engine/codec binary licences before distribution.
7. **Account decision (P2):** retain provisioned-member sign-in only, or add
   creation/deletion flows together before exposing creation anywhere.

No implementation or runtime validation was performed for this research note.
