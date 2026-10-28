# Recommendation methods for a sparse, self-hosted media library

Research date: 2026-10-27. This recommends an approach for Lumina's household-scale
catalogue and per-member data. It is not a claim that an offline metric proves a
member will enjoy a programme.

## Decision

Keep Lumina's local, explainable content-based ranker as the serving system. Its
near-term improvement should be a **two-stage hybrid**:

1. retrieve a broad but eligible set from the member's library using hard media
   type, visibility, watched/suppressed, and availability constraints (and explicit language preferences where applicable);
2. score it from several independently meaningful affinities (metadata links,
   genre/subgenre, people, synopsis/title embedding, recency and quality), then
   rerank the top candidates for coverage and diversity; and
3. attach the reason to the winning *contribution* (for example, "Medical drama
   with a similar tone"), never merely to a loose shared type such as "TV".

The key product constraint is meaningful anchor relevance before ranking. Prefer
compatible format for an anchored shelf, allowing a strong, explainable thematic
bridge across formats. A general anime preference alone cannot justify placing
an unrelated anime under a medical-drama anchor. Use a separate discovery shelf
for broader profile matches; respect explicit format preferences without
inferring a permanent ban from one watch.

This improves cold-start behavior and explanation fidelity without training on a data
set that a single household cannot supply. It also builds on the direction Lumina
has already accepted: ADR 0015 deliberately keeps ranking local and
inspectable, forbids training on household data for now, uses fixed embeddings as
features, and requires a concrete one-line reason. The current ranker already
has cosine/token similarity, taste centroids, calibration and a diversity pass;
the highest-leverage work is therefore candidate eligibility, richer title facts,
and outcome measurement—not a new recommendation framework.

## Practical serving design

### 1. Build a media taxonomy that can veto bad matches

Store normalized, auditable facets for each title: `format` (live action, anime,
animation, documentary), `kind` (movie/series/episode), scripted/unscripted,
genres and subgenres, content rating, original language, country/region, networks,
cast/director/creator, collection/franchise, and a short metadata-derived text
document. Preserve provenance and tolerate unknowns; unknown format is not proof
that a title matches an anchor.

For an anchored shelf, use ordered gates:

| Gate | Example effect for a live-action medical drama seed |
| --- | --- |
| Visibility, availability, unstarted and feedback | Required for every candidate. |
| Kind/format | Prefer compatible series/format; crossings require meaningful anchor evidence. |
| Direct relation | A shared collection, creator, cast or declared franchise may override a soft genre mismatch, but not privacy/availability. |
| Attribute match | Score drama, workplace/medical setting, tone and language separately; do not let one generic `TV` label dominate. |
| Semantic match | Use synopsis/title embedding as one bounded feature, after the gates. |

When a member purposely watches anime, the profile gets an anime affinity. That
can enable a separate "Because you enjoy anime" shelf or an explicitly labelled
exploration slot; it must not relabel an anime result as caused by a medical
drama watch.

### 2. Use complementary retrieval rather than one global score

Produce candidates from separate, bounded retrievers and retain provenance:

- direct entity links: franchise, creator, director, cast, network;
- structured metadata: high-overlap genres/subgenres, type and language;
- semantic text: cosine nearest neighbours over normalized title + synopsis +
  selected tags, only inside the eligible cohort;
- profile retrieval: nearest matches to the member's satisfied-title centroids;
- discovery: quality-filtered, unseen titles from underrepresented but compatible
  facets.

Merge the union, score it, then use a diversity-aware reranker. This makes each
retriever debuggable and prevents a weak semantic match from becoming the only
route to a shelf.

Use an absolute relevance gate before reranking, calibrated separately for the
available feature sets. A percentile alone admits weak matches when the whole
pool is weak. Cohort-relative scores can order qualified candidates; a second
percentile gate is an optional experiment, not an established requirement.
Handle repetition through diversity and fatigue rather than treating an
absolute threshold as sufficient for list quality.

For the lexical branch, use a transparent TF-IDF or BM25 fielded query over
normalized title, synopsis, genres, people, collection and public metadata tags. It is a
valuable complement to embeddings (see [scikit-learn text feature extraction](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction)): exact terms such as *medical drama*, a
network, creator or franchise stay intelligible and it remains available during
embedding backfill. The `implicit` project documents item-item cosine, TF-IDF
and BM25 implementations for implicit-feedback data
([source](https://github.com/benfred/implicit/blob/main/README.md)); Lumina can
implement the smaller content-index version without adopting its collaborative
model.

Retain the existing DPP baseline. Maximal marginal relevance (MMR) is a simpler alternative to evaluate, not an extra ranking stage: greedily
select the candidate with the best trade-off between relevance and similarity to
items already selected. Its original formulation is a diversity-based reranking
method [Goldstein & Carbonell, 1998](https://aclanthology.org/X98-1025/), and a
current recommendation text describes MMR as balancing item utility against
diversity in the already-recommended set
([McAuley, *Personalized Machine Learning*, §10.3](https://cseweb.ucsd.edu/~jmcauley/pml/pml_book.pdf)).
For Lumina, measure redundancy with the same facet/embedding representation and
cap repeated franchise, format, primary genre and creator. Do not optimize raw
diversity at the cost of showing unrelated material.

### 3. Make reasons faithful and provide corrective controls

Return a structured explanation payload alongside each item:

`anchor`, `dominant_feature`, `matched_facets`, `format_gate`, `retriever`, and
`exploration`. The UI can then say "Similar to your medical-drama anchor: another
live-action medical drama" only if `format_gate` passed and the medical/drama
facets contributed materially. Otherwise it should identify the actual reason
or use a non-causal label such as "Try something different." Log the shown reason
with the impression so a replay can audit it.

Offer small, high-signal corrections at the point of failure: "not interested",
"less like this", and "don't mix anime with TV" / a profile format preference.
These should update filters and negative affinity immediately, rather than wait
for an inferred dislike.

## Open-source options

| Tool/method | Fit now | Cold start and explanations | Cost / operational fit | Recommendation |
| --- | --- | --- | --- | --- |
| Lumina's own metadata + fixed embeddings + existing DPP | Excellent | Content works for a new title/member; every score component can be named | Existing on-device embedding and SQLite background flow; no new runtime | **Use now.** Tighten hard gates and improve title metadata. |
| TF-IDF/BM25 / item-item cosine | Good baseline and metadata fallback | New items work from text/facets; shared terms make reasons direct | `scikit-learn`-level CPU work; easy to version and replay | Add as a baseline/retriever if the embedding is absent or stale. |
| [LightFM](https://github.com/lyst/lightfm) | Conditional, later | Hybrid matrix factorization accepts user/item features and is designed to generalize via metadata to new users/items; its ranking losses target top-k results | Apache-2.0 repository license; native extension/OpenMP considerations; requires enough clean interaction events | Consider only after a sizable per-member interaction corpus and offline win. Its latent factors weaken reason fidelity. |
| [implicit](https://benfred.github.io/implicit/) ALS/BPR/item-item | Poor for a single household; useful if there is an explicit, consented multi-member/cohort signal | Collaborative filtering cannot score a new item/user without interactions; ALS has an item-contribution `explain` API | MIT; fast CPU implementations and optional GPU/ANN support | Do not add for the current per-member, isolated policy. Reassess only with an opt-in shared signal and sufficient density. |
| [LensKit for Python](https://github.com/lenskit/lkpy) | Good as an experiment/evaluation harness, not as Lumina's serving dependency | Supports training/running/evaluating recommender experiments | Its current Python toolkit is intentionally research/experimentation focused; adds package/tooling surface | Use in a separate offline spike if bespoke evaluation becomes costly. |
| [RecBole](https://github.com/RUCAIBox/RecBole) | Research benchmark only | Broad model coverage, including sequential and knowledge-based models | MIT but PyTorch-based; substantial dependency/model/training surface for a small household | Do not ship it in Lumina's Docker image. Use only to compare a later offline experiment. |

Primary-source notes: LightFM documents hybrid representations as sums of
feature embeddings and its README identifies BPR/WARP ranking support
([docs](https://making.lyst.com/lightfm/docs/), [source](https://github.com/lyst/lightfm)).
Implicit documents ALS, BPR, logistic MF and item-item cosine/TF-IDF/BM25, plus
multithreaded CPU training and optional CUDA/ANN
([docs](https://benfred.github.io/implicit/)); it also exposes explanatory
item contributions for ALS
([API](https://benfred.github.io/implicit/api/models/cpu/als.html)). RecBole
documents 94 algorithms, standard evaluation protocols, PyTorch requirements and
MIT licensing ([docs](https://recbole.io/docs/),
[repository](https://github.com/RUCAIBox/RecBole)). LensKit calls itself a
toolkit for training, running and evaluating recommender experiments
([repository](https://github.com/lenskit/lkpy)).

## Evaluation before changing serving behaviour

Treat each complete or near-complete watch, save, follow, skip, suppression and
open as an event with its impression/surface/position/reason attribution. Keep a
fixed, time-ordered replay: train/profile at time *t*, predict the next eligible
event after *t*, and never use later watches as features. This avoids a random
split leaking future taste into the past.

Report more than a single accuracy number:

| Goal | Offline measure | Online guardrail |
| --- | --- | --- |
| Relevant discovery | Recall@K, NDCG@K and completion/save/follow rate after an attributable impression | Completion/watch-depth rate, segmented by shelf. |
| Avoid bad crossovers | Format mismatch rate and false-causal-reason rate | "Not interested" or format-preference correction rate. |
| Help a large library get discovered | Catalogue coverage, long-tail share and unique titles served | Number of distinct titles started per active member. |
| Keep a shelf usable | Intra-list similarity, duplicate franchise/creator rate, repeated-impression rate | Opens after repeat exposure versus after a fresh recommendation. |
| Preserve member control | Suppression and visibility contract tests | Zero reappearances after a hard suppression. |

Compare a candidate change against the current policy with deterministic replay,
then ship it behind a reversible flag. For real traffic, assign a stable member
to one policy for a bounded period; do not interleave two policies on the same
shelf and claim the clicks are independent. Preserve ADR 0015's privacy and
reproducibility requirements: no household history leaves the installation, and
each served item can be reconstructed from recorded inputs and policy version.

## Delivery sequence

1. Add/normalize title facets and an explicit format taxonomy; backfill without
   inferring a positive anime affinity from a generic TV watch.
2. Add the anchored-shelf eligibility gate and faithful structured reasons.
   Cover the reported regression: watching a live-action medical drama must not
   produce unrelated animation; a supported thematic crossover belongs only where its explanation is truthful.
3. Add retriever provenance and candidate diagnostics, then tune the bounded
   semantic, facet, and diversity weights through replay.
4. Instrument outcome and correction metrics; use them to decide whether the
   content policy has plateaued.
5. Only then run an offline LightFM/item-item experiment behind the same gates,
   with an agreed minimum-data threshold and no new online dependency until it
   beats the content baseline on relevance *and* mismatch/reason guardrails.

## Remote Up Next and autoplay follow-up (2026-10-28)

Library-title matching does not automatically improve remote videos. Up Next needs
its own current-video admission rule, and autoplay needs stricter continuity than a
browse shelf. Category overlap or creator identity alone is insufficient: two gaming
highlights or two uploads from a variety creator can be unrelated. Keep the first
playable result focused on the current subject or a verified next part, apply
member feedback after admission, and constrain exploration to related candidates.
A bounded session history prevents cycling even before persistent progress catches
up. Empty results should stop automatic playback rather than trigger a general
Popular fallback.

This follows the separation of candidate generation and ranking described in
[Google's YouTube recommendation paper](https://research.google/pubs/deep-neural-networks-for-youtube-recommendations/).
YouTube describes watch history and satisfaction feedback among its recommendation
signals ([official explanation](https://support.google.com/youtube/answer/16089387?hl=en-GB));
its published next-video system also treats ranking as a
[multi-objective problem](https://research.google/pubs/recommending-what-video-to-watch-next-a-multitask-ranking-system/).
These sources support the architecture and objectives, not Lumina's particular
thresholds. Lumina has neither YouTube's cross-user co-watch data nor its trained
models. Local lexical/embedding continuity is a practical improvement, not parity.

Candidate coverage remains a separate limitation: the current local pool,
background nominations, Popular snapshot and cached channel listings determine what
can be ranked. Strict matching can reveal a sparse pool. Broader provider-specific
retrieval should be evaluated separately within the existing background request
budgets, without blocking playback on a provider search or exporting private history.
