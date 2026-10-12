---
target: Lumina app shell and interactable views, baseline b48c27b
total_score: 28
p0_count: 0
p1_count: 1
timestamp: 2026-10-11T19-34-40Z
slug: frontend-src-app-appshell-tsx
---
# Lumina app quality baseline

Recorded against b48c27b before this pass. User confirmed preserving identity. This is the first formal baseline; prior test/catalogue coverage was not a scored critique.

Anti-patterns verdict: Lumina has a recognizable gold/serif identity, but mechanical alignment and excessive technical detail make several task surfaces feel unfinished. The independent deterministic scan later found ten signals: nine false positives (guarded artwork and CSS triangle markers), one genuine player width transition, removed in this pass. No reliable browser overlay was available through the read-only browser API.

| Heuristic | Score /4 |
|---|---|
| System status | 3 |
| Real-world match | 4 |
| Control and freedom | 3 |
| Consistency | 2 |
| Error prevention | 3 |
| Recognition | 3 |
| Efficiency | 3 |
| Minimalism | 2 |
| Recovery | 3 |
| Help | 2 |

Total: 28/40, Good. Subjective expert assessment, not an accessibility certification.

Strengths: recognizable brand; labeled anchored navigation; keyboard shortcuts and recoverable forms.

Priority issues:
- P1 phone player preference panels can escape the viewport; constrain panels and preserve access (/impeccable adapt).
- P2 settings copy/switches and member navigation crowd phones; stack copy and use a labeled page selector (/impeccable distill).
- P2 header mark/search alignment, reversed dialog order and onboarding heading collision undermine craft; align geometry and focus order (/impeccable polish).
- P2 raw episode filename dominates the content title and an empty rail wastes space; prioritize matched metadata, disclose file details and distinguish empty/error states (/impeccable layout).

Personas: Casey struggles with narrow phone search and clipped player panels; Sam sees dialog visual order disagree with Tab order and no visible password label; Jordan cannot infer the accepted format from the Add a link error.

Minor observations: request option bands, library/channel tab overflow, operator jargon and help coverage still deserve attention. Specialized failures and modal variants are listed as unreviewed in the per-view ledger.

Questions to consider: Which tasks can expose their advanced controls only when needed? Which operator terms need inline explanations?
