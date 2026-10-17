from __future__ import annotations

from dataclasses import dataclass
import socket
from collections.abc import Iterable
from typing import Any
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import pytest

from app.services import music_sonos
from app.services.music_sonos import SonosError, SonosService, SonosTrack


# These are mock-only LAN records.  They never leave the fake socket/HTTP
# boundary or appear in the service's public discovery response.
MEMBER_IP = "192.0.2.10"
COORDINATOR_IP = "192.0.2.11"
OTHER_IP = "192.0.2.12"
MEMBER_ID = "RINCON_MEMBER"
COORDINATOR_ID = "RINCON_COORDINATOR"


def _location(address: str, path: str = "/xml/device_description.xml") -> str:
    return f"http://{address}:1400{path}"


def _ssdp(location: str, *, peer: str, usn: str = "uuid:RINCON_MEMBER::urn:schemas-upnp-org:device:ZonePlayer:1") -> tuple[bytes, tuple[str, int]]:
    payload = (
        "HTTP/1.1 200 OK\r\n"
        "CACHE-CONTROL: max-age=1800\r\n"
        f"LOCATION: {location}\r\n"
        "SERVER: Linux UPnP/1.0 Sonos/80.0 (ZPS)\r\n"
        "ST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n"
        f"USN: {usn}\r\n"
        "\r\n"
    ).encode()
    return payload, (peer, 1900)


def _device_description(device_id: str, *, name: str, model: str) -> bytes:
    return f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:ZonePlayer:1</deviceType>
    <friendlyName>{name}</friendlyName>
    <modelName>{model}</modelName>
    <UDN>uuid:{device_id}</UDN>
  </device>
</root>""".encode()


def _topology_xml(*, coordinator_id: str = COORDINATOR_ID, include_coordinator: bool = True) -> str:
    coordinator_member = (
        f'<ZoneGroupMember UUID="{coordinator_id}" Location="{_location(COORDINATOR_IP)}" ZoneName="Living" />'
        if include_coordinator
        else ""
    )
    return (
        "<ZoneGroupState><ZoneGroups>"
        f'<ZoneGroup Coordinator="{coordinator_id}">'
        f"{coordinator_member}"
        f'<ZoneGroupMember UUID="{MEMBER_ID}" Location="{_location(MEMBER_IP)}" ZoneName="Kitchen" />'
        "</ZoneGroup>"
        "</ZoneGroups><VanishedDevices /></ZoneGroupState>"
    )


def _soap_response(action: str, values: dict[str, str] | None = None, *, namespace: str = music_sonos.AV_TRANSPORT_NS) -> bytes:
    body = "".join(f"<{name}>{escape(value)}</{name}>" for name, value in (values or {}).items())
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body>"
        f'<u:{action}Response xmlns:u="{namespace}">{body}</u:{action}Response>'
        "</s:Body></s:Envelope>"
    ).encode()


def _soap_fault(code: str, description: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body><s:Fault>"
        "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring>"
        '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{code}</errorCode><errorDescription>{description}</errorDescription>"
        "</UPnPError></detail>"
        "</s:Fault></s:Body></s:Envelope>"
    ).encode()


@dataclass
class FakeResponse:
    status: int
    body: bytes
    headers: dict[str, str] | None = None

    def getheader(self, name: str) -> str | None:
        headers = self.headers or {}
        return headers.get(name) or headers.get(name.lower())

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]


class FakeSocket:
    def __init__(self, packets: Iterable[tuple[bytes, tuple[str, int]]]) -> None:
        self.packets = list(packets)
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.timeouts: list[float] = []
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def sendto(self, payload: bytes, target: tuple[str, int]) -> None:
        self.sent.append((payload, target))

    def recvfrom(self, _amount: int) -> tuple[bytes, tuple[str, int]]:
        if self.packets:
            return self.packets.pop(0)
        raise socket.timeout()

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, address: str, response: FakeResponse, requests: list[dict[str, Any]]) -> None:
        self.address = address
        self.response = response
        self.requests = requests
        self.closed = False

    def request(self, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        self.requests.append({"address": self.address, "method": method, "path": path, "body": body, "headers": headers or {}})

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def _service(
    packets: Iterable[tuple[bytes, tuple[str, int]]],
    responses: Iterable[FakeResponse],
    *,
    clock: Any = None,
) -> tuple[SonosService, FakeSocket, list[dict[str, Any]]]:
    fake_socket = FakeSocket(packets)
    response_queue = list(responses)
    requests: list[dict[str, Any]] = []

    def socket_factory(*_args: Any) -> FakeSocket:
        return fake_socket

    def connection_factory(address: str, _port: int, *, timeout: float) -> FakeConnection:
        del timeout
        if not response_queue:
            raise AssertionError("unexpected HTTP request in deterministic test")
        return FakeConnection(address, response_queue.pop(0), requests)

    kwargs: dict[str, Any] = {
        "socket_factory": socket_factory,
        "connection_factory": connection_factory,
    }
    if clock is not None:
        kwargs["clock"] = clock
    return SonosService(**kwargs), fake_socket, requests


def _discover_responses(
    *,
    packets: Iterable[tuple[bytes, tuple[str, int]]] | None = None,
    device_id: str = MEMBER_ID,
    device_ip: str = MEMBER_IP,
    topology: str | None = None,
    device_name: str = "Kitchen",
    device_model: str = "Sonos One",
) -> tuple[list[tuple[bytes, tuple[str, int]]], list[FakeResponse]]:
    packet_list = list(packets or [_ssdp(_location(device_ip), peer=device_ip)])
    topology_xml = topology if topology is not None else _topology_xml(coordinator_id=device_id, include_coordinator=False)
    responses = [
        FakeResponse(200, _device_description(device_id, name=device_name, model=device_model)),
        FakeResponse(200, _soap_response("GetZoneGroupState", {"ZoneGroupState": topology_xml}, namespace=music_sonos.ZONE_GROUP_TOPOLOGY_NS)),
    ]
    return packet_list, responses


def _action_name(body: bytes) -> str:
    root = ET.fromstring(body)
    action = next(element for element in root.iter() if element.tag.rsplit("}", 1)[-1] not in {"Envelope", "Body"})
    return action.tag.rsplit("}", 1)[-1]


def _argument(body: bytes, name: str) -> str:
    root = ET.fromstring(body)
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            return element.text or ""
    raise AssertionError(f"missing SOAP argument {name}")


def _argument_names(body: bytes) -> list[str]:
    root = ET.fromstring(body)
    action = next(element for element in root.iter() if element.tag.rsplit("}", 1)[-1] not in {"Envelope", "Body"})
    return [element.tag.rsplit("}", 1)[-1] for element in action]


def test_discovery_uses_bounded_ssdp_and_deduplicates_verified_devices() -> None:
    packets, responses = _discover_responses()
    packets.append(packets[0])
    service, fake_socket, requests = _service(packets, responses)

    result = service.discover()

    assert result == [{"id": MEMBER_ID, "name": "Kitchen", "model": "Sonos One", "coordinator_id": MEMBER_ID}]
    assert len(requests) == 2
    sent_payload, target = fake_socket.sent[0]
    assert target == (music_sonos.SSDP_MULTICAST, music_sonos.SSDP_PORT)
    assert b"MX: 1" in sent_payload
    assert f"ST: {music_sonos.SSDP_ST}".encode() in sent_payload
    assert fake_socket.closed
    assert MEMBER_IP not in repr(result)


def test_discovery_rejects_spoofed_locations_and_http_redirects() -> None:
    packets = [
        _ssdp(_location(OTHER_IP), peer=MEMBER_IP),
        _ssdp("http://sonos.example:1400/xml/device_description.xml", peer=MEMBER_IP),
        _ssdp("http://8.8.8.8:1400/xml/device_description.xml", peer="8.8.8.8"),
        _ssdp("http://127.0.0.1:1400/xml/device_description.xml", peer="127.0.0.1"),
        _ssdp("http://169.254.1.2:1400/xml/device_description.xml", peer="169.254.1.2"),
        _ssdp(f"https://{MEMBER_IP}:1400/xml/device_description.xml", peer=MEMBER_IP),
        _ssdp(_location(MEMBER_IP), peer=MEMBER_IP),
    ]
    service, _fake_socket, requests = _service(
        packets,
        [FakeResponse(302, b"", {"Location": "http://public.example/redirect"})],
    )

    assert service.discover() == []
    assert len(requests) == 1
    assert requests[0]["address"] == MEMBER_IP
    assert requests[0]["method"] == "GET"
    assert requests[0]["path"] == "/xml/device_description.xml"


def test_xml_doctype_entities_and_oversized_payloads_are_rejected() -> None:
    packets = [_ssdp(_location(MEMBER_IP), peer=MEMBER_IP)]
    malicious = b'<!DOCTYPE root [<!ENTITY x "expanded">]><root><device><UDN>&x;</UDN></device></root>'
    service, _fake_socket, _requests = _service(packets, [FakeResponse(200, malicious)])

    assert service.discover() == []
    with pytest.raises(SonosError, match="size limit"):
        music_sonos._bounded_xml(b"<root>" + (b"x" * (music_sonos.MAX_DEVICE_XML_BYTES + 1)), limit=music_sonos.MAX_DEVICE_XML_BYTES, context="test")


def test_didl_metadata_escapes_track_fields_and_uri() -> None:
    track = SonosTrack(
        url="https://media.example.test/song.mp3?sig=a&part=1",
        title="A <great> & honest song",
        artist="A & B",
        album="R&D",
        artwork_url="https://media.example.test/art.jpg?x=1&y=2",
        duration_seconds=65,
    )

    metadata = music_sonos._didl_metadata(track, 3)
    root = ET.fromstring(metadata)
    assert "&lt;great&gt;" in metadata
    assert "&amp;part=1" in metadata
    assert root.find(f".//{{{music_sonos.DC_NS}}}title").text == "A <great> & honest song"
    assert root.find(f".//{{{music_sonos.UPNP_NS}}}artist").text == "A & B"
    resource = root.find(f".//{{{music_sonos.DIDL_NS}}}res")
    assert resource is not None
    assert resource.text == "https://media.example.test/song.mp3?sig=a&part=1"
    assert resource.attrib["duration"] == "0:01:05"


def test_play_queue_targets_group_coordinator_and_preserves_action_order() -> None:
    packets, responses = _discover_responses(
        device_id=MEMBER_ID,
        device_ip=MEMBER_IP,
        topology=_topology_xml(),
    )
    responses.extend(
        [
            FakeResponse(200, _soap_response("RemoveAllTracksFromQueue")),
            FakeResponse(200, _soap_response("AddURIToQueue", {"FirstTrackNumberEnqueued": "1"})),
            FakeResponse(200, _soap_response("AddURIToQueue", {"FirstTrackNumberEnqueued": "2"})),
            FakeResponse(200, _soap_response("SetAVTransportURI")),
            FakeResponse(200, _soap_response("Play")),
        ]
    )
    service, _fake_socket, requests = _service(packets, responses)
    discovered = service.discover()
    assert discovered[0]["coordinator_id"] == COORDINATOR_ID

    result = service.play_queue(
        MEMBER_ID,
        [
            SonosTrack("https://media.example.test/one.mp3", "One & only"),
            SonosTrack("https://media.example.test/two.mp3", "Two"),
        ],
    )

    queue_requests = requests[2:]
    assert [request["method"] for request in queue_requests] == ["POST"] * 5
    assert [_action_name(request["body"]) for request in queue_requests] == [
        "RemoveAllTracksFromQueue",
        "AddURIToQueue",
        "AddURIToQueue",
        "SetAVTransportURI",
        "Play",
    ]
    assert {request["address"] for request in queue_requests} == {COORDINATOR_IP}
    assert "One &amp; only" in _argument(queue_requests[1]["body"], "EnqueuedURIMetaData")
    assert _argument(queue_requests[3]["body"], "CurrentURI") == f"x-rincon-queue:{COORDINATOR_ID}#0"
    assert result == {
        "ok": True,
        "speaker_id": MEMBER_ID,
        "coordinator_id": COORDINATOR_ID,
        "queued": 2,
        "queue_uri": f"x-rincon-queue:{COORDINATOR_ID}#0",
    }


def test_control_supports_transport_volume_and_seek_with_bounds() -> None:
    packets, responses = _discover_responses(topology=_topology_xml())
    actions = [
        ("pause", None, "Pause"),
        ("play", None, "Play"),
        ("stop", None, "Stop"),
        ("next", None, "Next"),
        ("previous", None, "Previous"),
        ("volume", 33, "SetVolume"),
        ("seek", 3661, "Seek"),
    ]
    responses.extend(FakeResponse(200, _soap_response(action)) for _name, _value, action in actions)
    service, _fake_socket, requests = _service(packets, responses)
    service.discover()

    for name, value, _expected_action in actions:
        result = service.control(MEMBER_ID, name, value)
        assert result["ok"] is True

    control_requests = requests[2:]
    assert [_action_name(request["body"]) for request in control_requests] == [action for _name, _value, action in actions]
    assert [_argument_names(request["body"]) for request in control_requests[:5]] == [
        ["InstanceID"],
        ["InstanceID", "Speed"],
        ["InstanceID"],
        ["InstanceID"],
        ["InstanceID"],
    ]
    assert _argument(control_requests[-2]["body"], "DesiredVolume") == "33"
    assert _argument(control_requests[-1]["body"], "Target") == "1:01:01"
    assert _argument(control_requests[-1]["body"], "Unit") == "REL_TIME"

    for invalid_action, invalid_value in (("louder", None), ("volume", -1), ("volume", 101), ("seek", -1), ("play", 1)):
        with pytest.raises(SonosError):
            service.control(MEMBER_ID, invalid_action, invalid_value)
    with pytest.raises(SonosError, match="at most 100"):
        service.play_queue(MEMBER_ID, [SonosTrack("https://media.example.test/a.mp3", "A")] * 101)


def test_control_requires_a_recent_discovery_and_never_accepts_an_address() -> None:
    now = [0.0]
    packets, responses = _discover_responses()
    service, _fake_socket, _requests = _service(packets, responses, clock=lambda: now[0])

    with pytest.raises(SonosError, match="stale"):
        service.control(MEMBER_ID, "play")
    service.discover()
    with pytest.raises(SonosError, match="returned by recent discovery"):
        service.control(MEMBER_IP, "play")
    now[0] = music_sonos.DISCOVERY_TTL_SECONDS
    with pytest.raises(SonosError, match="stale"):
        service.control(MEMBER_ID, "play")


def test_successful_activity_slides_freshness_but_failed_calls_do_not() -> None:
    now = [0.0]
    packets, responses = _discover_responses()
    responses.extend(
        [
            FakeResponse(200, _soap_response("GetTransportInfo", {"CurrentTransportState": "PAUSED_PLAYBACK"})),
            FakeResponse(200, _soap_response("GetVolume", {"CurrentVolume": "20"}, namespace=music_sonos.RENDERING_CONTROL_NS)),
            FakeResponse(200, _soap_response("GetPositionInfo")),
            FakeResponse(200, _soap_response("Pause")),
            FakeResponse(500, _soap_fault("501", "Action failed")),
        ]
    )
    service, _fake_socket, _requests = _service(packets, responses, clock=lambda: now[0])
    service.discover()

    now[0] = music_sonos.DISCOVERY_TTL_SECONDS - 1
    service.status(MEMBER_ID)
    now[0] += music_sonos.DISCOVERY_TTL_SECONDS - 1
    service.control(MEMBER_ID, "pause")
    now[0] += music_sonos.DISCOVERY_TTL_SECONDS - 1
    with pytest.raises(SonosError, match="SOAP fault 501"):
        service.control(MEMBER_ID, "pause")
    now[0] += 1
    with pytest.raises(SonosError, match="stale"):
        service.control(MEMBER_ID, "pause")


def test_soap_fault_is_raised_with_code_and_description() -> None:
    packets, responses = _discover_responses()
    responses.append(FakeResponse(500, _soap_fault("711", "Illegal seek target")))
    service, _fake_socket, _requests = _service(packets, responses)
    service.discover()

    with pytest.raises(SonosError, match=r"SOAP fault 711: Illegal seek target"):
        service.control(MEMBER_ID, "seek", 12)


def test_status_reports_group_transport_volume_track_and_position() -> None:
    packets, responses = _discover_responses(topology=_topology_xml())
    track_metadata = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="-1"><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Now</dc:title>'
        "<upnp:artist>Artist</upnp:artist><upnp:album>Album</upnp:album>"
        "<res>https://media.example.test/now.mp3</res></item></DIDL-Lite>"
    )
    responses.extend(
        [
            FakeResponse(200, _soap_response("GetTransportInfo", {"CurrentTransportState": "PLAYING"})),
            FakeResponse(200, _soap_response("GetVolume", {"CurrentVolume": "27"}, namespace=music_sonos.RENDERING_CONTROL_NS)),
            FakeResponse(
                200,
                _soap_response(
                    "GetPositionInfo",
                    {
                        "Track": "2",
                        "TrackDuration": "0:03:05",
                        "RelTime": "0:01:02",
                        "TrackURI": "https://media.example.test/now.mp3",
                        "TrackMetaData": track_metadata,
                    },
                ),
            ),
        ]
    )
    service, _fake_socket, _requests = _service(packets, responses)
    service.discover()

    status = service.status(MEMBER_ID)

    assert status["coordinator_id"] == COORDINATOR_ID
    assert status["transport_state"] == "PLAYING"
    assert status["playing"] is True
    assert status["volume"] == 27
    assert status["position_seconds"] == 62
    assert status["track"] == {
        "url": "https://media.example.test/now.mp3",
        "title": "Now",
        "artist": "Artist",
        "album": "Album",
        "artwork_url": "",
        "duration_seconds": 185,
        "position_seconds": 62,
        "track_number": 2,
    }
    assert {entry["id"] for entry in status["group"]} == {MEMBER_ID, COORDINATOR_ID}


@pytest.mark.parametrize(
    "url",
    [
        "ftp://media.example.test/song.mp3",
        "https://user:password@media.example.test/song.mp3",
        "https://media.example.test/song.mp3#fragment",
        "https://media.example.test/song.mp3\nX-Injected: yes",
    ],
)
def test_play_queue_rejects_unsupported_credentialed_or_control_character_urls(url: str) -> None:
    packets, responses = _discover_responses()
    service, _fake_socket, requests = _service(packets, responses)
    service.discover()

    with pytest.raises(SonosError):
        service.play_queue(MEMBER_ID, [SonosTrack(url, "Track")])
    assert len(requests) == 2
