# Home image performance audit — 2026-10-18

## Scope and baseline

Home hero and shelf thumbnails: visible readiness, image admission, offscreen requests/decode, browser cache behavior, rendition selection and server delivery. Work uses the isolated `codex/home-image-performance` branch, starting from deployed revision `8abbba95dd9e33b9d683454df3e66f731f16d147`. Books and manga improvements remain in place.

The real-stack Home harness uses synthetic medium-library data, three cold/warm trials and 4× CPU throttling. Before scrolling, every baseline trial had 45 decoded sharp images at three seconds, with only five visible. Cold pre-scroll transfer median was 2,808,725 bytes. Warm transfer was zero, but encoded image bodies still totaled 2,567,956 bytes. Cold queue maxima were 526–592 ms; response-end to reveal maxima were 124–144 ms. Image requests stayed within four concurrent requests on the HTTP/1 fixture. No failed images or duplicate network requests occurred.

Authenticated live observation independently found 126 decoded sharp elements with six visible on settled, unscrolled Home. This settled observation occurred minutes after navigation and is not a first-three-seconds measurement. Resource entries starting in the first five seconds included five original title images totaling 6,731,958 encoded bytes, with the largest 2,144,639 bytes. Those requests were browser-cache hits: these are encoded body sizes, not network transfer.

Saved measurements contain generic aggregates and route classes only. Private media identifiers, paths, hostnames and credentials are excluded. Local evidence is ignored under `output/home-performance/` and `output/home-image-performance/`.

## Changes

Home shelf cards retain their DOM, focus stops, captions, menus and low-cost previews. Sharp artwork becomes eligible only when the shelf is within one viewport below the visible area and the card is in the horizontal window, with two cards of overscan on either side. Keyboard focus enables the focused card immediately. Once loaded, the sharp image remains shown when the window moves. Abandoned unfinished images explicitly cancel their browser requests; stale load/decode callbacks cannot restore them. Cancellation tolerates development Strict Mode ref replay. The shared protocol-aware request/decode limits are unchanged; images continue to hold their loader slot through decode.

Episode stills offer 400, 800 and 1,200-pixel rendition candidates. Supported rendition-backed stills no longer advertise the original source as a responsive candidate. The encoder keeps its existing no-upscale behavior: a smaller source stays at its original dimensions. Still-image width descriptors reflect those actual dimensions and duplicate width candidates are omitted. Unsupported/original-only artwork retains its existing delivery behavior. Existing ready cache rows repair missing sizes on demand through the existing two generation slots and bounded preparation queue. A busy repair returns a retryable 503 instead of an original body under the rendition URL. Failed repairs preserve the valid ready metadata and old thumbnail rather than downgrading to original delivery. Existing 400-pixel cache hits and source identities remain valid; no startup-wide regeneration is added. No schema, runtime dependency or global cache/concurrency increase is introduced.

A valid synthetic 500 × 750 high-entropy PNG of 1,500,933 bytes produced a DPR2-selected JPEG rendition of 620,610 bytes (59% fewer). The larger requested widths both retained the 500 × 750 source dimensions. This uses the local JPEG fallback; production WebP behavior requires live verification.

## Paired real-stack observations

Three-run medians, synthetic medium library, LAN profile and 4× CPU throttling. The three-second snapshots occur before scrolling.

| Measurement | Before | After |
| --- | ---: | ---: |
| Decoded sharp images | 45 | 13 |
| Visible decoded images | 5 | 5 |
| Offscreen decoded images | 40 | 8 |
| Cold transferred image bytes | 2,808,725 | 1,673,872 |

The final profile admits at most four concurrent image requests over HTTP/1, with zero image HTTP failures, overlapping duplicate requests or duplicate image-body transfers across all six phases. DPR2 selected eight 800-pixel Primary renditions and zero original Primary images. Far-card main and revealed secondary controls retain focus across rerenders and retain decoded art.

The after-only all-visible artwork readiness observation was 720 ms cold and 499 ms warm. The old harness did not capture an equivalent all-visible clock, and host load differed between runs; queue and readiness timings are local observations, not a paired production latency guarantee. The final qualification adds far-card and secondary-control focus actions, so whole-workflow timing summaries are also excluded from the paired comparison. The decoded-count and transferred-byte differences are the primary paired resource evidence. Baseline first-screen partial-reveal timestamps are not reported as all-visible readiness.

## Verification

Backend suite: 5,146 passed, 11 skipped. Frontend suite: 2,589 passed in 296 files. App/E2E typechecks, production build, style/size and license checks pass. The final frontend was rebuilt and typechecked during the unchanged backend run; source hashes retained with the local gate evidence establish which inputs each stage checked.

The real-backend browser suite passes with 52 passed and five existing skips (9.9 minutes). The mocked browser suite passes with 623 passed and two existing skips (6.6 minutes). The full `make check` run first completed the backend, unit and build stages; a measurement-only correction distinguished browser-cache revisits from duplicate transfers, then the entire real-backend and mocked browser gate was replayed using the unchanged passing source/build inputs. Local hashes record that provenance. Post-deployment observations are recorded on the release pull request.

The delayed-response horizontal-scroll regression retains actual pixel decoding and focused-card readiness. Before cancellation, actual browser concurrency reached eight with no obsolete requests canceled; afterward it stays at four over HTTP/1 and cancels obsolete work. The vertical near→far contract drops all pending/queued work, retains loaded sharp images, and allows one focused far card to load immediately. Loader/decode caps are unchanged.

## Reproduce

```bash
mise exec -- make frontend-build
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 sh -c 'cd frontend && npx playwright test -c playwright.realstack.config.ts home-images.perf.spec.ts --project=perf'
mise exec -- make check
```
