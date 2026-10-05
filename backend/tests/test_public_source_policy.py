from __future__ import annotations

import socket
import urllib.request

import pytest
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError

from app.models import AutomationRun, DownloadJob, SourceAutomation
from app.services.network_policy import (
    PUBLIC_SOURCE_POLICY_MESSAGE,
    PolicyYoutubeDL,
    PublicSourcePolicy,
    PublicSourcePolicyError,
    SafeRedirectHandler,
    create_public_connection,
)
from support import make_user


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
        del family
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (address, port))
            for address in mapping[host]
        ]

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/media",
        "gopher://example.test/",
        "http://127.0.0.1/admin",
        "http://10.0.0.8/media",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/admin",
        "http://[fec0::1]/internal",
        "http://224.0.0.1/stream",
        "http://192.0.2.1/reserved",
    ],
)
def test_policy_rejects_unsupported_or_non_public_destinations(url: str) -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({"example.test": ["93.184.216.34"]}))
    with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
        policy.validate_url(url)


def test_policy_rejects_encoded_loopback_and_mixed_dns_answers() -> None:
    policy = PublicSourcePolicy(
        resolver=resolver_for(
            {
                "2130706433": ["127.0.0.1"],
                "mixed.example": ["93.184.216.34", "10.0.0.4"],
            }
        )
    )

    for url in ("http://2130706433/", "https://mixed.example/media"):
        with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
            policy.validate_url(url)


def test_redirect_target_is_revalidated_before_following() -> None:
    policy = PublicSourcePolicy(
        resolver=resolver_for({"public.example": ["93.184.216.34"], "internal.example": ["127.0.0.1"]})
    )
    handler = SafeRedirectHandler(policy)
    request = urllib.request.Request("https://public.example/start")

    with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://internal.example/admin")


def test_connection_rechecks_dns_and_rejects_rebinding() -> None:
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    def rebinding_resolver(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
        del host, family
        return [(socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (address, port)) for address in next(answers)]

    policy = PublicSourcePolicy(resolver=rebinding_resolver)
    connected: list[str] = []

    def fake_socket(ip_addr, timeout, source_address):
        del timeout, source_address
        connected.append(ip_addr[4][0])
        return object()

    assert create_public_connection(policy, ("rebind.example", 443), _create_socket_func=fake_socket) is not None
    assert connected == ["93.184.216.34"]
    with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
        create_public_connection(policy, ("rebind.example", 443), _create_socket_func=fake_socket)
    assert connected == ["93.184.216.34"]


def test_policy_error_category_is_stable_and_non_reflective() -> None:
    marker = "http://127.0.0.1/sensitive-marker"
    with pytest.raises(PublicSourcePolicyError) as caught:
        PublicSourcePolicy().validate_url(marker)
    assert str(caught.value) == PUBLIC_SOURCE_POLICY_MESSAGE
    assert marker not in str(caught.value)


def test_policy_ytdlp_transport_blocks_extractor_generated_private_requests() -> None:
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}) as ydl:
        with pytest.raises(RequestError, match="Public source policy"):
            ydl.urlopen(Request("http://127.0.0.1/internal"))


@pytest.mark.parametrize(
    "media",
    [
        {"protocol": "rtmp", "url": "rtmp://93.184.216.34/live"},
        {"protocol": "m3u8", "url": "https://media.example/video.m3u8"},
        {"protocol": "m3u8", "url": "https://media.example/live.m3u8", "is_live": True},
        {
            "is_live": True,
            "requested_formats": [{"protocol": "m3u8", "url": "https://media.example/live.m3u8"}],
        },
        {
            "protocol": "https",
            "url": "https://media.example/clip.mp4",
            "section_start": 1,
        },
        {"protocol": "https", "url": "http://127.0.0.1/private.mp4"},
        # A text playlist saved as "media": yt-dlp's ffmpeg postprocessors would follow its
        # file:// / http:// entries (other members' files, the LAN) before publication refuses it.
        {"protocol": "https", "url": "https://media.example/a.m3u", "ext": "m3u"},
        {"protocol": "https", "url": "https://media.example/a", "ext": "M3U8"},
        {"requested_formats": [
            {"protocol": "https", "url": "https://media.example/v.mp4", "ext": "mp4"},
            {"protocol": "https", "url": "https://media.example/a.sdp", "ext": "sdp"},
        ]},
    ],
)
def test_external_or_private_media_transport_is_rejected_before_download(media: dict) -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({"media.example": ["93.184.216.34"]}))
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=policy) as ydl:
        with pytest.raises(PublicSourcePolicyError):
            ydl.validate_download_transport(media)


def test_preview_job_automation_and_retry_share_the_stable_policy_error(monkeypatch, db_factory, api_client) -> None:
    session_factory = db_factory
    user = make_user("user-1", username="viewer", display_name="Viewer")
    with session_factory.begin() as session:
        session.add(user)
        session.add(
            DownloadJob(
                id="legacy-private-job",
                user_id=user.id,
                source_url="http://127.0.0.1/legacy-marker",
                status="failed",
                format_selection={},
                output_profile={},
            )
        )
        session.add(
            SourceAutomation(
                id="legacy-private-automation",
                user_id=user.id,
                label="Legacy private automation",
                source_url="http://127.0.0.1/legacy-automation-marker",
                source_type="generic_url",
                cron_expression="*/30 * * * *",
                active=True,
                auto_download=True,
                format_selection={},
                output_profile={},
                rules={},
                duplicate_policy="skip_same_source",
            )
        )

    monkeypatch.setattr("app.main.resolve_request_user_snapshot", lambda request, credentials=None: user)
    monkeypatch.setattr("app.main.SessionLocal", session_factory)  # the streaming gate reads the member's access
    client = api_client(user=user, base_url="http://localhost")
    hostile_url = "http://127.0.0.1/sensitive-marker"
    rejected_responses = [
        client.post("/api/preview", json={"source_url": hostile_url}),
        client.post("/api/jobs", json={"source_url": hostile_url}),
        client.post("/api/automations", json={"label": "Blocked", "source_url": hostile_url}),
        client.post("/api/jobs/legacy-private-job/retry"),
    ]
    for response in rejected_responses:
        assert response.status_code == 400
        assert response.json() == {"detail": PUBLIC_SOURCE_POLICY_MESSAGE}
        assert "sensitive-marker" not in response.text
        assert "legacy-marker" not in response.text
        assert "legacy-automation-marker" not in response.text

    automation_response = client.post("/api/automations/legacy-private-automation/run")
    assert automation_response.status_code == 200
    assert automation_response.json()["status"] == "failed"
    assert automation_response.json()["error"] == PUBLIC_SOURCE_POLICY_MESSAGE
    assert "legacy-automation-marker" not in automation_response.text

    with session_factory() as session:
        assert session.query(DownloadJob).count() == 1
        assert session.query(SourceAutomation).count() == 1
        assert session.get(DownloadJob, "legacy-private-job").status == "failed"
        assert session.query(AutomationRun).one().status == "failed"


def test_a_playlist_rewrite_resolves_each_host_once_but_still_checks_every_url() -> None:
    calls: list[str] = []
    base = resolver_for({"cdn.example": ["93.184.216.34"]})

    def counting(host, port, family, kind):  # noqa: ANN001
        calls.append(host)
        return base(host, port, family, kind)

    policy = PublicSourcePolicy(resolver=counting)
    resolved: set[tuple[str, int]] = set()
    for n in range(450):
        policy.validate_url(f"https://cdn.example/seg/{n}.ts", resolved)
    assert calls == ["cdn.example"]
    with pytest.raises(PublicSourcePolicyError):
        policy.validate_url("https://user:pw@cdn.example/seg/1.ts", resolved)  # per-URL checks still run
    policy.validate_url("https://cdn.example/seg/1.ts")  # without the set every call resolves
    assert calls == ["cdn.example", "cdn.example"]


@pytest.mark.parametrize("url", ["http://public.example:22/", "https://public.example:25/x", "http://93.184.216.34:6379/", "http://public.example:8096/"])
def test_policy_admits_only_web_ports(url: str) -> None:
    """A member-supplied source cannot turn Lumina into a port scanner or a client for SSH, SMTP, Redis, or a
    non-web service published on the household's own public address."""
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    with pytest.raises(PublicSourcePolicyError):
        policy.validate_url(url)
    with pytest.raises(PublicSourcePolicyError):  # every connection, including redirects and manifest children
        create_public_connection(policy, ("public.example", int(url.rsplit(":", 1)[1].split("/")[0])))


def test_policy_admits_explicit_default_ports() -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    for url in ("http://public.example:80/a", "https://public.example:443/a", "https://public.example/a"):
        assert policy.validate_url(url) == url


def test_an_admins_extra_ports_admit_public_hosts_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import network_policy

    monkeypatch.setattr(network_policy, "_extra_ports", frozenset())
    network_policy.set_extra_ports([8000, 22, "8080", True])  # hand-edited JSON: only the valid, safe int survives
    assert network_policy._extra_ports == {8000}
    policy = PublicSourcePolicy(resolver=resolver_for({"radio.example": ["93.184.216.34"], "lan.example": ["192.168.1.5"]}))
    assert policy.validate_url("http://radio.example:8000/stream") == "http://radio.example:8000/stream"
    for url in ("http://lan.example:8000/stream", "http://10.0.0.8:8000/stream", "http://radio.example:8080/", "http://radio.example:22/"):
        with pytest.raises(PublicSourcePolicyError):
            policy.validate_url(url)
