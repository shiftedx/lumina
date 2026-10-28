"""Small synthetic editorial relevance check, complementary to temporal recommendation replay.

Run: mise exec -- backend/.venv/bin/python scripts/perf/reco_relevance.py
No database, private history, model downloads or network. These hand-authored English cases
are acceptance examples, not a calibrated benchmark or evidence of real-user uplift.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.models import MediaTitle
from app.services.reco import SOURCE_POPULAR, Candidate, MemberProfile, SurfaceContext
from app.services.reco.profile import candidate_from_title, remote_tokens
from app.services.reco.ranking import rank
from app.services.reco.remote_matching import remote_anchor_strength

NOW = datetime(2026, 10, 27, 12)

# Explicit editorial judgments: relationship comes from subject, not identical titles or genre alone.
CASES = (
    ("medical", "Surgeons at a hospital treat patients while debating medical ethics and surgical risks.",
     "Doctors at a rural hospital treat patients and confront medical ethics during emergencies.",
     "Teenagers train in magic to defend a kingdom against supernatural monsters."),
    ("legal", "Defense attorneys investigate evidence and defend clients during criminal trials in court.",
     "Attorneys uncover evidence of corruption while representing clients in difficult criminal trials.",
     "Young musicians compete for a recording contract while managing friendships and fame."),
    ("ocean", "Marine biologists study coral reefs and ocean wildlife threatened by pollution.",
     "Researchers protect coral reefs and document marine wildlife affected by ocean pollution.",
     "A chef restores a restaurant and teaches apprentices to bake bread and pastries."),
    ("space", "Astronauts aboard a spacecraft explore distant planets and repair failing navigation systems.",
     "A spacecraft crew repairs navigation systems while surveying distant planets for an expedition.",
     "A royal family struggles over succession and romance in a medieval kingdom."),
)


def candidate(key: str, overview: str, *, animated: bool = False):
    title = MediaTitle(id=key, type="series", key=key, name=key, year=2018,
                       category="anime" if animated else "tv", created_at=NOW, added_at=NOW,
                       metadata_json={"overview": overview, "genres": ["Drama", "Animation"] if animated else ["Drama"],
                                      "community_rating": 9.9 if animated else 7.0})
    return candidate_from_title(title, None)


def profile() -> MemberProfile:
    return MemberProfile(user_id="synthetic", built_at=NOW, generation=0, affinity={}, channel_aliases={},
                            followed=frozenset(), interests=frozenset(), satisfied=(), centroids=(), centroid_mass=(),
                            taste_mix={}, item_fatigue={}, channel_fatigue={}, fatigued_out=frozenset(), fewer={}, spill={},
                            hidden_items=frozenset(), hidden_channels=frozenset(), hidden_titles=frozenset(), excluded=frozenset())


def evaluate() -> dict:
    member = profile()
    results = []
    for key, anchor_text, related_text, unrelated_text in CASES:
        anchor = candidate(f"{key}-anchor", anchor_text)
        related = candidate(f"{key}-related", related_text)
        pool = [related, candidate(f"{key}-unrelated", unrelated_text),
                candidate(f"{key}-animation", unrelated_text, animated=True),
                candidate(f"{key}-missing", ""),
                candidate(f"{key}-generic", "A young woman faces challenges in life and discovers friendship and family.")]
        for surface in ("home_because", "title_similar"):
            ctx = SurfaceContext(surface=surface, k=5, local_date=date(2026, 10, 27), anchor=anchor)
            served = rank(pool, member, ctx, now=NOW)
            keys = {item.candidate.key for item in served}
            results.append({"case": key, "surface": surface, "relevant_retrieved": related.key in keys,
                            "irrelevant_served": len(keys - {related.key}), "served": len(keys)})
            empty = rank(pool[1:], member, ctx, now=NOW)
            results.append({"case": f"{key}-abstain", "surface": surface, "relevant_retrieved": True,
                            "irrelevant_served": len(empty), "served": len(empty)})
    return {"fixture": "synthetic-editorial-v1", "limitations": "Small English lexical acceptance set; not user satisfaction or embedding calibration.",
            "passed": all(r["relevant_retrieved"] and r["irrelevant_served"] == 0 for r in results), "cases": results}


def remote_candidate(key: str, title: str, *, channel: str, category: str = "science-technology", views: int = 1000) -> Candidate:
    channel_key = f"ch:{channel}"
    return Candidate(key=key, target_kind="remote", title=title, channel_key=channel_key, channel_name=channel,
                     tokens=remote_tokens(title, channel_key, (category,)), published_at=NOW,
                     sources=SOURCE_POPULAR, category_keys=(category,), views=views)


def evaluate_remote() -> dict:
    """Judged Up Next pairs; a creator or broad category alone cannot establish topic continuity."""
    cases = (
        ("medical", remote_candidate("medical-anchor", "Cardiac transplant surgery recovery guide", channel="Ward Lab"),
         remote_candidate("medical-related", "Transplant surgeon explains cardiac recovery", channel="Clinical Notes"),
         [remote_candidate("medical-category", "Quantum processor research highlights", channel="Circuit Lab", views=5_000_000),
          remote_candidate("medical-creator", "Weekly channel news and highlights", channel="Ward Lab", views=5_000_000)]),
        ("game", remote_candidate("game-anchor", "Hollow Moon speedrun final boss route", channel="Route Lab", category="gaming"),
         remote_candidate("game-related", "Hollow Moon boss route speedrun tutorial", channel="Speedrun Atlas", category="gaming"),
         [remote_candidate("game-other", "Neon Harbor speedrun final boss route highlights", channel="Speedrun Atlas", category="gaming", views=5_000_000)]),
        ("creator", remote_candidate("creator-anchor", "Vintage bicycle chain restoration tutorial", channel="Bike Shed"),
         remote_candidate("creator-related", "Restoring a vintage bicycle drivetrain chain", channel="Repair School"),
         [remote_candidate("creator-drift", "Weekly community update and highlights", channel="Bike Shed", views=5_000_000)]),
        ("boilerplate", remote_candidate("boilerplate-anchor", "Orion telescope polar alignment tutorial tips", channel="Field Notes", category="technology"),
         remote_candidate("boilerplate-related", "Orion telescope polar alignment guide", channel="Night Sky School", category="technology"),
         [remote_candidate("boilerplate-drift", "Weekly camera tutorial tips upload", channel="Field Notes", category="technology", views=5_000_000)]),
    )
    results = []
    for key, anchor, related, distractors in cases:
        ctx = SurfaceContext(surface="up_next", k=5, local_date=date(2026, 10, 27), anchor=anchor, context_key=anchor.key)
        served = rank([*distractors, related], profile(), ctx, now=NOW)
        keys = [item.candidate.key for item in served]
        results.append({"case": key, "related_evidence": remote_anchor_strength(anchor, related) is not None,
                        "distractor_evidence": any(remote_anchor_strength(anchor, item) is not None for item in distractors),
                        "related_served": related.key in keys, "distractors_served": len(set(keys) & {item.key for item in distractors}),
                        "first": keys[0] if keys else None})
    unknown = remote_candidate("unknown-anchor", "", channel="Route Lab", category="gaming")
    unknown_pool = [remote_candidate("unknown-same-channel", "Weekly game highlights", channel="Route Lab", category="gaming")]
    served = rank(unknown_pool, profile(), SurfaceContext(surface="up_next", k=5, local_date=date(2026, 10, 27),
                                                           anchor=unknown, context_key=unknown.key), now=NOW)
    results.append({"case": "unknown", "related_evidence": True,
                    "distractor_evidence": remote_anchor_strength(unknown, unknown_pool[0]) is not None,
                    "related_served": True, "distractors_served": len(served), "first": served[0].candidate.key if served else None})
    passed = all(row["related_evidence"] and not row["distractor_evidence"] and row["related_served"]
                 and row["distractors_served"] == 0 and (row["case"] == "unknown" or row["first"] == f"{row['case']}-related")
                 for row in results)
    return {"fixture": "synthetic-remote-up-next-v1", "limitations": "Hand-judged English title tokens without named entity understanding, provider retrieval, or historical watch effects.",
            "passed": passed, "cases": results}


if __name__ == "__main__":
    report = {"titles": evaluate(), "remote_up_next": evaluate_remote()}
    print(json.dumps(report, indent=2))
    sys.exit(0 if all(section["passed"] for section in report.values()) else 1)
