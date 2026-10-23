# ADR 0030: Cache local hero framing on demand

- Status: Accepted
- Date: 2026-10-19

## Context

One fixed backdrop crop cuts off faces in some compositions and hides low subjects in others. Library and Requests heroes use different image-delivery paths. Analyzing every backdrop during a library scan would waste CPU on artwork nobody views.

## Decision

Analyze requested hero backdrops through a deduplicated bounded background queue. Authorized library and catalog browsing may also nominate up to four upcoming hero backdrops per response for an eight-entry lower-priority warm-up queue. Warm-up uses only local or already-pinned image bytes, yields to interactive analysis and the existing application quiet-time gate, pauses during video transcodes, and spaces work by two seconds. A cache-only miss does not poison the later interactive request. Library requests must pass the normal title visibility check and name its current source; catalog requests accept only the exact approved TMDB backdrop URL shape and use the existing guarded image fetcher. Browsing never waits for inference. Posters, logos and episode cards do not enter this queue.

The small pinned YuNet and NanoDet models ship with their licenses. YuNet supplies face regions; NanoDet supplies generic subject boxes only, with its classification labels discarded. OpenCV runs only in a low-priority, single-thread child with a wall timeout and Linux CPU/address-space limits. It returns validated normalized regions, never executable instructions or modified assets. Its native memory is released when the child exits. The worker stops when idle and is reaped during shutdown.

Cache small JSON results by analysis version and source identity, with bounded memory and disk retention. A source replacement invalidates framing; failed analysis has a retry delay. This cache is disposable, requires no schema migration and is independent of artwork rendition readiness. No full-library backfill is scheduled.

The browser keeps heroes full bleed and fits important regions to the current dimensions, including headroom and an allowance for the text overlay. Wide desktop title heroes gain a bounded responsive height allowance to reduce excessive cropping without a delayed layout change. Uncertain detections retain the existing conservative crop. Preview and sharp image share geometry; inset images and blurred borders are excluded. Browser inference and per-title asset customizations are unnecessary.

## Consequences

The image gains approximately 4.0 MB of model weights plus the pinned OpenCV/NumPy runtime. These libraries are not imported by the serving process. The browser gives the first framing lookup at most 150 milliseconds before revealing the artwork. A pending answer reveals the conservative crop immediately; no inference completion or polling holds up the page. Once revealed, that source keeps its chosen framing for the visit, so late results cannot shift a displayed image. Ready answers are cached for later visits, and viewport resizing still recomputes the crop from the same framing regions. Demand-led warm-up makes ready metadata more likely on a first visit without analyzing the entire library. Unusual compositions can still require a compromise when important subjects cannot all fit the available aspect ratio. The fallback also covers providers without supported local analysis.

## Native clients (2026-10-22)

iOS and tvOS use the same cached analysis through the connected-app endpoint
`GET /Items/{id}/Images/Backdrop/0/Framing?tag=...`. This requires an authenticated
member and the current signed backdrop tag; image tags alone do not authorize analysis.
Inherited backdrops name the parent image item, matching the displayed artwork.
Authorized native title responses nominate the same bounded warm-up queue.
The shared Apple renderer applies the web's static face-first crop, headroom and text
allowance at its actual viewport size. It requests framing alongside image loading
with a 150 ms request timeout, chooses once before revealing that source and keeps
late/pending analysis for future visits through the server cache. Unavailable or
older servers use the conservative upper crop. Portrait posters retain their existing
uncropped foreground presentation; no per-device model or library backfill is added.

Requests heroes and detail headers on both Apple platforms use the same crop
geometry and reveal policy. Their connected-app catalog admits framing through
`GET /Lumina/Requests/catalog/framing?src=...`, reusing the web validator and its
issued-source registry. Native catalog responses already nominate this shared
cache for warm-up. Only backdrop sources request analysis, including backdrops used as fallback tile artwork; posters and logos do not.
iPhone Requests heroes prefer a backdrop when one is available so the same
subject regions can protect the narrow viewport.
