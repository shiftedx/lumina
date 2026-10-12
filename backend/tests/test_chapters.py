from app.services.chapters import MAX_TIMELINE_ITEMS, normalize_chapters


def test_provider_chapters_take_precedence_are_ordered_and_clamped() -> None:
    timeline = normalize_chapters(
        provider_chapters=[
            {"start_time": 60, "end_time": 180, "title": "Main <em>part</em>"},
            {"start_time": 0, "end_time": 72, "title": "Opening"},
            {"start_time": 60, "title": "Duplicate"},
            {"start_time": 150, "title": "Too late"},
        ],
        description="0:00 Inferred\n1:00 chapters\n2:00 must not win",
        duration=120,
    )

    assert timeline["chapters"] == [
        {"start_time": 0.0, "end_time": 60.0, "title": "Opening"},
        {"start_time": 60.0, "end_time": 120.0, "title": "Main <em>part</em>"},
    ]
    assert [token["seconds"] for token in timeline["timestamps"]] == [0.0, 60.0]


def test_description_infers_chapters_only_from_three_ascending_near_start_tokens() -> None:
    timeline = normalize_chapters(
        provider_chapters=None,
        description="0:03 Warm-up\n1:15 The story\n02:40 Closing thoughts",
        duration=240,
    )

    assert timeline["chapters"] == [
        {"start_time": 3.0, "end_time": 75.0, "title": "Warm-up"},
        {"start_time": 75.0, "end_time": 160.0, "title": "The story"},
        {"start_time": 160.0, "end_time": 240.0, "title": "Closing thoughts"},
    ]
    assert timeline["timestamps"] == [
        {"start": 0, "end": 4, "seconds": 3.0, "label": "0:03"},
        {"start": 13, "end": 17, "seconds": 75.0, "label": "1:15"},
        {"start": 28, "end": 33, "seconds": 160.0, "label": "02:40"},
    ]


def test_malformed_duplicate_descending_and_out_of_range_timestamps_remain_non_chapters() -> None:
    for description in (
        "0:00 Start\n1:60 malformed\n2:00 End",
        "0:00 Start\n0:00 Again\n2:00 End",
        "0:00 Start\n2:00 Later\n1:00 Earlier",
        "0:00 Start\n1:00 Middle\n3:00 Outside",
    ):
        timeline = normalize_chapters(provider_chapters=[], description=description, duration=180)
        assert timeline["chapters"] == []

    isolated = normalize_chapters(
        provider_chapters=[],
        description="A useful note begins at 1:23, while 3:00 is beyond this media.",
        duration=180,
    )
    assert isolated["chapters"] == []
    assert isolated["timestamps"] == [{"start": 24, "end": 28, "seconds": 83.0, "label": "1:23"}]


def test_description_timestamps_are_isolated_and_respect_duration_boundaries() -> None:
    timeline = normalize_chapters(
        provider_chapters=[],
        description="At 00:00 begin; then 1:02:03 continue. Do not link http://1:23 or 1:40:00.",
        duration=4_000,
    )

    assert timeline["chapters"] == []
    assert timeline["timestamps"] == [
        {"start": 3, "end": 8, "seconds": 0.0, "label": "00:00"},
        {"start": 21, "end": 28, "seconds": 3723.0, "label": "1:02:03"},
    ]


def test_timeline_metadata_has_strict_work_and_output_bounds() -> None:
    provider = [
        {"start_time": index, "title": "x" * 1_000}
        for index in range(MAX_TIMELINE_ITEMS + 50)
    ]
    provider_timeline = normalize_chapters(
        provider_chapters=provider,
        description=None,
        duration=MAX_TIMELINE_ITEMS + 100,
    )
    assert len(provider_timeline["chapters"]) == MAX_TIMELINE_ITEMS
    assert all(len(chapter["title"]) <= 240 for chapter in provider_timeline["chapters"])

    description = "\n".join(f"0:{index % 60:02d} Marker {index}" for index in range(MAX_TIMELINE_ITEMS + 50))
    description_timeline = normalize_chapters(
        provider_chapters=None,
        description=description,
        duration=10_000,
    )
    assert len(description_timeline["timestamps"]) == MAX_TIMELINE_ITEMS
    assert description_timeline["chapters"] == []
