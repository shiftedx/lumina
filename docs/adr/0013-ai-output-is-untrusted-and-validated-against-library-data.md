# ADR 0013: AI output is untrusted and validated against library data

- Status: Accepted
- Date: 2026-09-25

## Context

The media-vault pass uses a language model and speech recognition for match tie-breaking, subtitle generation and translation, recaps, semantic search and a smart-collection builder. Filenames, subtitles and transcripts are attacker-influenced text, and a model can hallucinate titles, ids or timings.

## Decision

- AI runs only through the admin-configured, OpenAI-compatible endpoints (`local_ai`, `local_asr`), under the shared inference slot. No feature calls a third-party AI service of its own. Amended 2026-09-26 (ADR 0014): on-device models are reached through the same client code, as a loopback endpoint chosen by model_endpoints.
- Input text is passed as delimited, untrusted DATA. Replies must be JSON and are validated against real library data before use. A match must be one of the offered TMDB candidate ids. Translated cues must return exactly the requested cue ids. Recap points must cite real input cues. Smart-collection rules must pass the same validator as a hand-built rule. Anything else is discarded. A model never invents or selects titles directly; Lumina evaluates rules and ranks candidates itself.
- Every AI feature has a documented no-AI form: manual Identify, the deterministic encoder and lexical search, overview-based recap fallback, and the manual rule builder. Unavailable actions are hidden or disabled with a reason. An admin can also switch individual AI features off directly; the disabled set is stored as a JSON list of feature keys in the `ai_features_disabled` settings column.

## Consequences

- A prompt injection in a filename can at worst pick a wrong but real candidate, which Identify fixes.
- AI availability never gates core playback or library features.
