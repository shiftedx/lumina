import pytest
from pydantic import ValidationError

from app.models import DownloadJob, SourceAutomation
from app.schemas import OutputProfile
from app.services.output_policy import OutputPolicy
from support import make_user


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_path", "/tmp/outside"),
        ("base_path", "C:/outside"),
        ("base_path", "../outside"),
        ("base_path", "%(title)100000s"),
        ("subdir", "safe/../../outside"),
        ("subdir", "windows\\escape"),
        ("subdir", "%(uploader)s"),
        ("template", "../%(title)s.%(ext)s"),
        ("template", "nested/%(title)s.%(ext)s"),
        ("template", "D:outside"),
        ("template", "%(filepath)s"),
        ("template", "%(title)999999999s.%(ext)s"),
    ],
)
def test_output_profile_rejects_paths_and_templates_that_can_escape(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        OutputProfile(**{field: value})


def test_output_policy_resolves_a_user_profile_inside_the_library(tmp_path) -> None:
    library_root = tmp_path / "library"
    resolved = OutputPolicy(library_root, "user-a").resolve(
        OutputProfile(
            base_path="curated",
            subdir="summer",
            organize_by="uploader",
            template="%(title)s [%(id)s].%(ext)s",
        )
    )
    assert resolved == (
        library_root
        / "user-a"
        / "curated"
        / "by-uploader"
        / "summer"
        / "%(title)s [%(id)s].%(ext)s"
    )


@pytest.mark.parametrize("user_id", [".", "..", "/tmp/user", "C:outside", "nested/user", "nested\\user"])
def test_output_policy_rejects_invalid_owner_identifiers(tmp_path, user_id: str) -> None:
    with pytest.raises(ValueError, match="owner identifier"):
        OutputPolicy(tmp_path / "library", user_id)


@pytest.mark.parametrize(
    ("organize_by", "expected_directory"),
    [("uploader", "by-uploader"), ("playlist", "by-playlist")],
)
def test_metadata_cannot_select_an_output_directory_symlink(tmp_path, organize_by: str, expected_directory: str) -> None:
    library_root = tmp_path / "library"
    user_root = library_root / "user-a"
    outside = tmp_path / "outside"
    user_root.mkdir(parents=True)
    outside.mkdir()
    (user_root / "attacker").symlink_to(outside, target_is_directory=True)

    resolved = OutputPolicy(library_root, "user-a").resolve(OutputProfile(organize_by=organize_by))

    assert resolved.parent == user_root / expected_directory
    assert "%(" not in str(resolved.parent)
    assert resolved.parent != user_root / "attacker"


def test_output_policy_blocks_symlink_escape_and_isolates_household_users(tmp_path) -> None:
    library_root = tmp_path / "library"
    user_root = library_root / "user-a"
    outside = tmp_path / "outside"
    user_root.mkdir(parents=True)
    outside.mkdir()
    (user_root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="inside the user's Library"):
        OutputPolicy(library_root, "user-a").resolve(OutputProfile(base_path="escape"))

    first = OutputPolicy(library_root, "user-a").resolve(OutputProfile(template="same.%(ext)s"))
    second = OutputPolicy(library_root, "user-b").resolve(OutputProfile(template="same.%(ext)s"))
    assert first != second
    assert first.parent == library_root / "user-a"
    assert second.parent == library_root / "user-b"


def test_output_policy_rejects_user_root_symlink_to_another_household_user(tmp_path) -> None:
    library_root = tmp_path / "library"
    other_user_root = library_root / "user-b"
    other_user_root.mkdir(parents=True)
    (library_root / "user-a").symlink_to(other_user_root, target_is_directory=True)

    with pytest.raises(ValueError, match="Library owner"):
        OutputPolicy(library_root, "user-a")


def test_output_policy_revalidates_constructed_profiles_at_the_service_boundary(tmp_path) -> None:
    profile = OutputProfile.model_construct(
        base_path=None,
        subdir=None,
        template=str(tmp_path / "outside"),
        organize_by="downloads",
    )

    with pytest.raises(ValueError):
        OutputPolicy(tmp_path / "library", "user-a").resolve(profile)


def test_job_and_automation_requests_reject_hostile_output_paths_without_persisting(db_factory, api_client) -> None:
    session_factory = db_factory
    user = make_user("user-1", username="viewer", display_name="Viewer")
    with session_factory.begin() as session:
        session.add(user)

    hostile_path = "/outside-library-marker"
    client = api_client(user=user, base_url="http://localhost")
    job_response = client.post(
        "/api/jobs",
        json={"source_url": "https://example.com/video", "output_profile": {"base_path": hostile_path}},
    )
    automation_response = client.post(
        "/api/automations",
        json={
            "label": "Unsafe output",
            "source_url": "https://example.com/channel",
            "output_profile": {"base_path": hostile_path},
        },
    )

    assert job_response.status_code == 422
    assert automation_response.status_code == 422
    assert hostile_path not in job_response.text
    assert hostile_path not in automation_response.text
    with session_factory() as session:
        assert session.query(DownloadJob).count() == 0
        assert session.query(SourceAutomation).count() == 0
