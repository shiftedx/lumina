"""Bounded local Sonos discovery and UPnP control.

The service deliberately keeps the network boundary small.  SSDP responses are
used only to identify a numeric LAN peer, and every subsequent HTTP request is
made to that peer on port 1400 without redirect handling.  Callers must first
run :meth:`SonosService.discover`; control methods only use the resulting
short-lived in-memory records.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import http.client
import ipaddress
import math
import re
import socket
import time
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET


SONOS_PORT = 1400
SSDP_PORT = 1900
SSDP_MULTICAST = "239.255.255.250"
SSDP_ST = "urn:schemas-upnp-org:device:ZonePlayer:1"
DISCOVERY_TIMEOUT_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 2.0
DISCOVERY_TTL_SECONDS = 120.0
MAX_DISCOVERY_RESULTS = 32
MAX_QUEUE_TRACKS = 100
MAX_SSDP_BYTES = 16 * 1024
MAX_DEVICE_XML_BYTES = 64 * 1024
MAX_SOAP_XML_BYTES = 256 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_TEXT_LENGTH = 512
MAX_URL_LENGTH = 4096

SOAP_ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"
AV_TRANSPORT_NS = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL_NS = "urn:schemas-upnp-org:service:RenderingControl:1"
ZONE_GROUP_TOPOLOGY_NS = "urn:schemas-upnp-org:service:ZoneGroupTopology:1"
DIDL_NS = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
DC_NS = "http://purl.org/dc/elements/1.1/"
UPNP_NS = "urn:schemas-upnp-org:metadata-1-0/upnp/"
R_NS = "urn:schemas-rinconnetworks-com:metadata-1-0/"

_SSDP_SEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_MULTICAST}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 1\r\n"
    f"ST: {SSDP_ST}\r\n"
    "\r\n"
).encode("ascii")

_XML_FORBIDDEN = re.compile(br"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_SONOS_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

ET.register_namespace("", DIDL_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("upnp", UPNP_NS)
ET.register_namespace("r", R_NS)


class SonosError(Exception):
    """A bounded discovery, validation, transport, or Sonos protocol error."""


@dataclass(frozen=True)
class SonosTrack:
    """A track that the parent route has made available to a Sonos speaker."""

    url: str
    title: str
    artist: str = ""
    album: str = ""
    artwork_url: str = ""
    duration_seconds: int | float | None = None
    content_type: str = "audio/mpeg"


@dataclass
class _Speaker:
    id: str
    name: str
    model: str
    address: str
    coordinator_id: str


@dataclass(frozen=True)
class _SonosLocation:
    address: str
    target: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _has_controls(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _bounded_xml(payload: bytes | bytearray | str, *, limit: int, context: str) -> ET.Element:
    """Parse XML only after applying size and entity-declaration guards."""
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = bytes(payload)
    if len(data) > limit:
        raise SonosError(f"{context} XML response exceeded the size limit")
    if _XML_FORBIDDEN.search(data):
        raise SonosError(f"{context} XML doctype/entity declarations are rejected")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise SonosError(f"invalid {context} XML response") from exc


def _element_text(element: ET.Element, name: str) -> str:
    for child in element.iter():
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _safe_text(value: Any, *, field: str, required: bool = False, limit: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        result = ""
    elif isinstance(value, str):
        result = value.strip()
    else:
        raise SonosError(f"{field} must be text")
    if required and not result:
        raise SonosError(f"{field} is required")
    if len(result) > limit:
        raise SonosError(f"{field} exceeds the size limit")
    if _has_controls(result):
        raise SonosError(f"{field} contains control characters")
    return result


def _lan_ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise SonosError("Sonos peer must use a numeric IPv4 address") from exc
    if (
        not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or address.is_global
    ):
        raise SonosError("Sonos peer must be a private LAN IPv4 address")
    return str(address)


def _validate_sonos_location(value: Any, *, peer_ip: str | None = None) -> _SonosLocation:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH or _has_controls(value):
        raise SonosError("Sonos LOCATION is invalid")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SonosError("Sonos LOCATION is invalid") from exc
    if parsed.scheme.casefold() != "http" or not host or parsed.username is not None or parsed.password is not None:
        raise SonosError("Sonos LOCATION must be an unauthenticated HTTP URL")
    if parsed.fragment or port != SONOS_PORT:
        raise SonosError("Sonos LOCATION must use port 1400 without a fragment")
    try:
        address = _lan_ipv4(host)
        expected = _lan_ipv4(peer_ip) if peer_ip is not None else None
    except SonosError:
        raise
    if expected is not None and address != expected:
        raise SonosError("Sonos LOCATION must point to the responding peer")
    path = parsed.path or "/"
    if not path.startswith("/"):
        raise SonosError("Sonos LOCATION path is invalid")
    target = path + (f"?{parsed.query}" if parsed.query else "")
    return _SonosLocation(address=address, target=target)


def _validate_stream_url(value: Any, *, field: str = "track URL") -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise SonosError(f"{field} is invalid")
    if _has_controls(value) or any(character.isspace() for character in value):
        raise SonosError(f"{field} contains invalid characters")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise SonosError(f"{field} is invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        raise SonosError(f"{field} must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise SonosError(f"{field} must not contain credentials")
    if parsed.fragment:
        raise SonosError(f"{field} must not contain a fragment")
    return value


def _normalize_id(value: Any) -> str:
    if not isinstance(value, str):
        raise SonosError("Sonos device is missing a valid identifier")
    result = value.strip()
    if "::" in result:
        result = result.split("::", 1)[0]
    if result.casefold().startswith("uuid:"):
        result = result[5:]
    if result.endswith(":0"):
        result = result[:-2]
    if not _SONOS_ID.fullmatch(result):
        raise SonosError("Sonos device identifier is invalid")
    return result


def _format_sonos_time(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _duration_seconds(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise SonosError("duration_seconds must be a nonnegative finite number")
    return int(value)


def _parse_sonos_time(value: Any) -> int | None:
    if not isinstance(value, str) or not value or value == "NOT_IMPLEMENTED":
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError:
        return None
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _response_header(response: Any, name: str) -> str | None:
    getter = getattr(response, "getheader", None)
    if callable(getter):
        value = getter(name)
        return None if value is None else str(value)
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        return None if value is None else str(value)
    return None


def _read_bounded_response(response: Any, *, limit: int, context: str) -> bytes:
    content_length = _response_header(response, "Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise SonosError(f"{context} response has an invalid size") from exc
        if declared < 0 or declared > limit:
            raise SonosError(f"{context} response exceeded the size limit")
    try:
        payload = response.read(limit + 1)
    except (OSError, http.client.HTTPException, socket.timeout, TimeoutError) as exc:
        raise SonosError(f"{context} response could not be read") from exc
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > limit:
        raise SonosError(f"{context} response exceeded the size limit")
    return bytes(payload)


def _parse_ssdp_response(payload: bytes) -> dict[str, str]:
    if len(payload) > MAX_SSDP_BYTES:
        return {}
    lines = payload.replace(b"\r\n", b"\n").split(b"\n")
    if not lines or not lines[0].upper().startswith((b"HTTP/1.1 200", b"HTTP/1.0 200")):
        return {}
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        try:
            header_name = key.decode("ascii").strip().casefold()
            header_value = value.decode("iso-8859-1").strip()
        except UnicodeDecodeError:
            continue
        if header_name and header_name not in headers:
            headers[header_name] = header_value
    st = headers.get("st", "")
    server = headers.get("server", "")
    if st and "zoneplayer" not in st.casefold():
        return {}
    if server and "sonos" not in server.casefold():
        return {}
    return headers


def _didl_metadata(track: SonosTrack, index: int) -> str:
    """Build one escaped DIDL-Lite music-track item for AddURIToQueue."""
    url = _validate_stream_url(track.url)
    title = _safe_text(track.title, field="track title", required=True)
    artist = _safe_text(track.artist, field="track artist")
    album = _safe_text(track.album, field="track album")
    artwork_url = _safe_text(track.artwork_url, field="artwork URL", limit=MAX_URL_LENGTH)
    if artwork_url:
        _validate_stream_url(artwork_url, field="artwork URL")
    duration = _duration_seconds(track.duration_seconds)

    root = ET.Element(f"{{{DIDL_NS}}}DIDL-Lite")
    item = ET.SubElement(
        root,
        f"{{{DIDL_NS}}}item",
        {"id": f"lumina-track-{index}", "parentID": "-1", "restricted": "true"},
    )
    ET.SubElement(item, f"{{{DC_NS}}}title").text = title
    if track.content_type not in {"audio/mpeg", "audio/flac", "audio/wav", "audio/mp4", "audio/aac"}:
        raise SonosError("unsupported track content type")
    resource_attributes = {"protocolInfo": f"http-get:*:{track.content_type}:*"}
    if duration is not None:
        resource_attributes["duration"] = _format_sonos_time(duration)
    ET.SubElement(item, f"{{{DIDL_NS}}}res", resource_attributes).text = url
    if artist:
        ET.SubElement(item, f"{{{UPNP_NS}}}artist").text = artist
    if album:
        ET.SubElement(item, f"{{{UPNP_NS}}}album").text = album
    if artwork_url:
        ET.SubElement(item, f"{{{UPNP_NS}}}albumArtURI").text = artwork_url
    ET.SubElement(item, f"{{{UPNP_NS}}}class").text = "object.item.audioItem.musicTrack"
    descriptor = ET.SubElement(
        item,
        f"{{{DIDL_NS}}}desc",
        {"id": "cdudn", "nameSpace": R_NS},
    )
    descriptor.text = "RINCON_AssociatedZPUDN"
    result = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    if len(result.encode("utf-8")) > MAX_METADATA_BYTES:
        raise SonosError("track metadata exceeds the size limit")
    return result


def _soap_body(service_ns: str, action: str, arguments: Sequence[tuple[str, Any]]) -> bytes:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]+", action):
        raise SonosError("invalid Sonos action")
    argument_xml = "".join(
        f"<{name}>{escape(str(value))}</{name}>" for name, value in arguments
    )
    body = (
        '<?xml version="1.0"?>'
        f'<s:Envelope xmlns:s="{SOAP_ENVELOPE_NS}" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{service_ns}">'
        f"{argument_xml}"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return body.encode("utf-8")


class SonosService:
    """Discover and control Sonos devices on the local LAN."""

    _CONTROL_PATHS = {
        "AVTransport": "/MediaRenderer/AVTransport/Control",
        "RenderingControl": "/MediaRenderer/RenderingControl/Control",
        "ZoneGroupTopology": "/ZoneGroupTopology/Control",
    }
    _SERVICE_NAMESPACES = {
        "AVTransport": AV_TRANSPORT_NS,
        "RenderingControl": RENDERING_CONTROL_NS,
        "ZoneGroupTopology": ZONE_GROUP_TOPOLOGY_NS,
    }

    def __init__(
        self,
        *,
        socket_factory: Callable[..., Any] | None = None,
        connection_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        discovery_timeout: float = DISCOVERY_TIMEOUT_SECONDS,
        request_timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if discovery_timeout <= 0 or request_timeout <= 0:
            raise ValueError("Sonos timeouts must be positive")
        self._socket_factory = socket_factory
        self._connection_factory = connection_factory
        self._clock = clock
        self._discovery_timeout = min(float(discovery_timeout), DISCOVERY_TIMEOUT_SECONDS)
        self._request_timeout = min(float(request_timeout), REQUEST_TIMEOUT_SECONDS)
        self._speakers: dict[str, _Speaker] = {}
        self._last_discovery_at: float | None = None

    def _new_socket(self) -> Any:
        factory = self._socket_factory or socket.socket
        try:
            return factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        except OSError as exc:
            raise SonosError("Sonos discovery socket could not be created") from exc

    def _new_connection(self, address: str, *, timeout: float | None = None) -> Any:
        factory = self._connection_factory or http.client.HTTPConnection
        try:
            return factory(address, SONOS_PORT, timeout=timeout or self._request_timeout)
        except (OSError, ValueError) as exc:
            raise SonosError("Sonos connection could not be created") from exc

    def discover(self) -> list[dict]:
        """Discover at most 32 responsive Sonos devices and cache them briefly."""
        self._speakers.clear()
        self._last_discovery_at = None
        deadline = self._clock() + self._discovery_timeout
        candidates: list[tuple[str, _SonosLocation, str]] = []
        seen_locations: set[tuple[str, str]] = set()
        sock = self._new_socket()
        try:
            sock.settimeout(self._discovery_timeout)
            try:
                sock.sendto(_SSDP_SEARCH, (SSDP_MULTICAST, SSDP_PORT))
            except OSError as exc:
                raise SonosError("Sonos discovery request could not be sent") from exc
            while len(candidates) < MAX_DISCOVERY_RESULTS and self._clock() < deadline:
                remaining = max(0.01, min(self._discovery_timeout, deadline - self._clock()))
                try:
                    sock.settimeout(remaining)
                    payload, peer = sock.recvfrom(MAX_SSDP_BYTES + 1)
                except (socket.timeout, TimeoutError):
                    break
                except OSError as exc:
                    raise SonosError("Sonos discovery response could not be read") from exc
                if not isinstance(payload, (bytes, bytearray)):
                    continue
                headers = _parse_ssdp_response(bytes(payload))
                location = headers.get("location")
                if not location or not isinstance(peer, tuple) or not peer:
                    continue
                try:
                    peer_ip = _lan_ipv4(str(peer[0]))
                    validated = _validate_sonos_location(location, peer_ip=peer_ip)
                except SonosError:
                    continue
                key = (validated.address, validated.target)
                if key in seen_locations:
                    continue
                seen_locations.add(key)
                candidates.append((validated.address, validated, headers.get("usn", "")))
        finally:
            try:
                sock.close()
            except OSError:
                pass

        # Keep the SSDP wait and the bounded HTTP verification as separate
        # phases.  A final SSDP receive timeout must not discard a response
        # that arrived just before that timeout.
        verification_deadline = self._clock() + self._request_timeout
        records: list[_Speaker] = []
        seen_ids: set[str] = set()
        seen_addresses: set[str] = set()
        for address, location, usn in candidates:
            timeout = self._remaining_timeout(verification_deadline)
            if timeout is None:
                break
            try:
                payload = self._http_get(address, location.target, timeout=timeout)
                record = self._parse_device_description(payload, address=address, fallback_id=usn)
            except SonosError:
                continue
            if record.id in seen_ids or record.address in seen_addresses:
                continue
            records.append(record)
            seen_ids.add(record.id)
            seen_addresses.add(record.address)
            if len(records) >= MAX_DISCOVERY_RESULTS:
                break

        if not records:
            return []

        # A topology response gives every member the current group coordinator.
        # A device-description result remains usable as a standalone group when
        # an older/faulty player does not answer this optional enrichment call.
        for record in tuple(records):
            timeout = self._remaining_timeout(verification_deadline)
            if timeout is None:
                break
            try:
                topology = self._soap_call(
                    record,
                    "ZoneGroupTopology",
                    "GetZoneGroupState",
                    (),
                    timeout=timeout,
                )
                self._apply_topology(records, topology)
                break
            except SonosError as exc:
                if str(exc).startswith("Sonos SOAP fault"):
                    raise
                continue

        self._speakers = {record.id: record for record in records[:MAX_DISCOVERY_RESULTS]}
        self._last_discovery_at = self._clock()
        return [self._public_speaker(record) for record in self._speakers.values()]

    def _remaining_timeout(self, deadline: float) -> float | None:
        remaining = deadline - self._clock()
        if remaining <= 0:
            return None
        return min(self._request_timeout, remaining)

    def _http_get(self, address: str, target: str, *, timeout: float | None = None) -> bytes:
        # `address` has already passed _lan_ipv4, and target came from a URL
        # whose host and port were validated.  No urllib redirect handler is
        # involved: the connection is pinned to this numeric peer.
        connection = self._new_connection(address, timeout=timeout)
        try:
            try:
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Accept": "application/xml,text/xml",
                        "Host": f"{address}:{SONOS_PORT}",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
            except (OSError, http.client.HTTPException, socket.timeout, TimeoutError) as exc:
                raise SonosError("Sonos device description request failed") from exc
            status = int(getattr(response, "status", 0))
            if 300 <= status < 400:
                raise SonosError("Sonos device description redirects are rejected")
            if status != 200:
                raise SonosError(f"Sonos device description returned HTTP {status}")
            return _read_bounded_response(response, limit=MAX_DEVICE_XML_BYTES, context="device description")
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def _parse_device_description(self, payload: bytes, *, address: str, fallback_id: str) -> _Speaker:
        root = _bounded_xml(payload, limit=MAX_DEVICE_XML_BYTES, context="device description")
        if _local_name(root.tag) != "root":
            raise SonosError("Sonos device description has an invalid root")
        device = next((element for element in root.iter() if _local_name(element.tag) == "device"), None)
        if device is None:
            raise SonosError("Sonos device description has no device")
        device_type = _element_text(device, "deviceType")
        if device_type and "zoneplayer" not in device_type.casefold():
            raise SonosError("device description is not a Sonos zone player")
        identifier = _element_text(device, "UDN") or fallback_id
        device_id = _normalize_id(identifier)
        name = _element_text(device, "friendlyName") or device_id
        model = _element_text(device, "modelName") or _element_text(device, "modelNumber") or "Sonos"
        try:
            name = _safe_text(name, field="speaker name") or device_id
        except SonosError:
            name = device_id
        try:
            model = _safe_text(model, field="speaker model") or "Sonos"
        except SonosError:
            model = "Sonos"
        return _Speaker(id=device_id, name=name, model=model, address=address, coordinator_id=device_id)

    def _apply_topology(self, records: list[_Speaker], response: dict[str, str]) -> None:
        raw_state = response.get("ZoneGroupState", "")
        if not raw_state:
            return
        root = _bounded_xml(raw_state, limit=MAX_SOAP_XML_BYTES, context="zone topology")
        by_id = {record.id: record for record in records}
        by_address = {record.address: record for record in records}
        for group in (element for element in root.iter() if _local_name(element.tag) == "ZoneGroup"):
            coordinator_raw = group.attrib.get("Coordinator", "")
            if not coordinator_raw:
                continue
            try:
                coordinator_id = _normalize_id(coordinator_raw)
            except SonosError:
                continue
            for member in (element for element in group.iter() if _local_name(element.tag) == "ZoneGroupMember"):
                try:
                    member_id = _normalize_id(member.attrib.get("UUID", ""))
                except SonosError:
                    continue
                location_value = member.attrib.get("Location", "")
                location: _SonosLocation | None = None
                if location_value:
                    try:
                        location = _validate_sonos_location(location_value)
                    except SonosError:
                        location = None
                record = by_id.get(member_id)
                if record is None and location is not None:
                    record = by_address.get(location.address)
                    if record is not None and record.id != member_id:
                        record = None
                if record is None:
                    if location is None or len(records) >= MAX_DISCOVERY_RESULTS:
                        continue
                    if location.address in by_address:
                        continue
                    name = member.attrib.get("ZoneName", "") or member_id
                    try:
                        name = _safe_text(name, field="speaker name") or member_id
                    except SonosError:
                        name = member_id
                    record = _Speaker(
                        id=member_id,
                        name=name,
                        model="Sonos",
                        address=location.address,
                        coordinator_id=coordinator_id,
                    )
                    records.append(record)
                    by_id[record.id] = record
                    by_address[record.address] = record
                elif location is not None and record.address != location.address:
                    # A topology payload must not be allowed to swap the
                    # address of an already verified device identifier.
                    continue
                record.coordinator_id = coordinator_id
                topology_name = member.attrib.get("ZoneName", "")
                if topology_name:
                    try:
                        record.name = _safe_text(topology_name, field="speaker name") or record.name
                    except SonosError:
                        pass

    def _soap_call(
        self,
        speaker: _Speaker,
        service_name: str,
        action: str,
        arguments: Sequence[tuple[str, Any]],
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        try:
            control_path = self._CONTROL_PATHS[service_name]
            service_ns = self._SERVICE_NAMESPACES[service_name]
        except KeyError as exc:
            raise SonosError("unsupported Sonos service") from exc
        payload = _soap_body(service_ns, action, arguments)
        connection = self._new_connection(speaker.address, timeout=timeout)
        try:
            try:
                connection.request(
                    "POST",
                    control_path,
                    body=payload,
                    headers={
                        "Content-Type": 'text/xml; charset="utf-8"',
                        "SOAPACTION": f"{service_ns}#{action}",
                        "Host": f"{speaker.address}:{SONOS_PORT}",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
            except (OSError, http.client.HTTPException, socket.timeout, TimeoutError) as exc:
                raise SonosError("Sonos SOAP request failed") from exc
            status = int(getattr(response, "status", 0))
            body = _read_bounded_response(response, limit=MAX_SOAP_XML_BYTES, context="SOAP")
            if status != 200:
                self._raise_soap_fault(body, status=status)
            root = _bounded_xml(body, limit=MAX_SOAP_XML_BYTES, context="SOAP")
            body_element = next((child for child in root if _local_name(child.tag) == "Body"), None)
            if body_element is None:
                raise SonosError("Sonos SOAP response has no body")
            children = list(body_element)
            fault = next((child for child in children if _local_name(child.tag) == "Fault"), None)
            if fault is not None:
                self._raise_fault_element(fault)
            if not children:
                return {}
            response_element = children[0]
            return {
                _local_name(child.tag): child.text or ""
                for child in response_element
            }
        finally:
            try:
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _raise_fault_element(fault: ET.Element) -> None:
        code = _element_text(fault, "errorCode") or _element_text(fault, "faultcode")
        description = _element_text(fault, "errorDescription") or _element_text(fault, "faultstring")
        if len(description) > MAX_TEXT_LENGTH:
            description = description[:MAX_TEXT_LENGTH]
        if code:
            raise SonosError(f"Sonos SOAP fault {code}: {description or 'action failed'}")
        raise SonosError(f"Sonos SOAP fault: {description or 'action failed'}")

    def _raise_soap_fault(self, body: bytes, *, status: int) -> None:
        try:
            root = _bounded_xml(body, limit=MAX_SOAP_XML_BYTES, context="SOAP fault")
        except SonosError as exc:
            raise SonosError(f"Sonos SOAP request returned HTTP {status}") from exc
        fault = next((element for element in root.iter() if _local_name(element.tag) == "Fault"), None)
        if fault is not None:
            self._raise_fault_element(fault)
        raise SonosError(f"Sonos SOAP request returned HTTP {status}")

    def _require_speaker(self, speaker_id: str) -> _Speaker:
        if not isinstance(speaker_id, str) or not speaker_id:
            raise SonosError("speaker_id must refer to a discovered Sonos device")
        now = self._clock()
        if self._last_discovery_at is None or now - self._last_discovery_at >= DISCOVERY_TTL_SECONDS:
            self._speakers.clear()
            self._last_discovery_at = None
            raise SonosError("Sonos discovery is stale; run discovery again")
        record = self._speakers.get(speaker_id)
        if record is None:
            raise SonosError("speaker_id was not returned by recent discovery")
        return record

    def _coordinator(self, record: _Speaker) -> _Speaker:
        coordinator = self._speakers.get(record.coordinator_id)
        if coordinator is None:
            raise SonosError("the discovered group coordinator is unavailable; run discovery again")
        return coordinator

    def _mark_verified_activity(self) -> None:
        """Extend a verified discovery only after a device answers successfully."""
        if self._last_discovery_at is not None and self._speakers:
            self._last_discovery_at = self._clock()

    @staticmethod
    def _public_speaker(record: _Speaker) -> dict:
        return {
            "id": record.id,
            "name": record.name,
            "model": record.model,
            "coordinator_id": record.coordinator_id,
        }

    def play_queue(self, speaker_id: str, tracks: list[SonosTrack]) -> dict:
        """Replace the coordinator queue with at most 100 guarded stream URLs."""
        record = self._require_speaker(speaker_id)
        if not isinstance(tracks, list) or not tracks:
            raise SonosError("tracks must be a non-empty list")
        if len(tracks) > MAX_QUEUE_TRACKS:
            raise SonosError("at most 100 tracks may be queued")
        metadata = []
        for index, track in enumerate(tracks):
            if not isinstance(track, SonosTrack):
                raise SonosError("tracks must contain SonosTrack values")
            metadata.append(_didl_metadata(track, index))

        coordinator = self._coordinator(record)
        self._soap_call(coordinator, "AVTransport", "RemoveAllTracksFromQueue", (("InstanceID", 0),))
        for track, didl in zip(tracks, metadata, strict=True):
            self._soap_call(
                coordinator,
                "AVTransport",
                "AddURIToQueue",
                (
                    ("InstanceID", 0),
                    ("EnqueuedURI", _validate_stream_url(track.url)),
                    ("EnqueuedURIMetaData", didl),
                    ("DesiredFirstTrackNumberEnqueued", 0),
                    ("EnqueueAsNext", 0),
                ),
            )
        queue_uri = f"x-rincon-queue:{coordinator.id}#0"
        self._soap_call(
            coordinator,
            "AVTransport",
            "SetAVTransportURI",
            (("InstanceID", 0), ("CurrentURI", queue_uri), ("CurrentURIMetaData", "")),
        )
        self._soap_call(coordinator, "AVTransport", "Play", (("InstanceID", 0), ("Speed", 1)))
        self._mark_verified_activity()
        return {
            "ok": True,
            "speaker_id": record.id,
            "coordinator_id": coordinator.id,
            "queued": len(tracks),
            "queue_uri": queue_uri,
        }

    def control(self, speaker_id: str, action: str, value: int | None = None) -> dict:
        """Send a bounded transport, volume, or seek action to a group."""
        record = self._require_speaker(speaker_id)
        if not isinstance(action, str) or not action.strip():
            raise SonosError("action is required")
        normalized_action = action.strip().casefold()
        transport_actions = {"pause": "Pause", "play": "Play", "stop": "Stop", "next": "Next", "previous": "Previous"}
        if normalized_action in transport_actions:
            if value is not None:
                raise SonosError(f"{normalized_action} does not accept a value")
            soap_action = transport_actions[normalized_action]
            arguments: tuple[tuple[str, Any], ...] = (("InstanceID", 0),)
            if soap_action == "Play":
                arguments += (("Speed", 1),)
            service_name = "AVTransport"
        elif normalized_action == "volume":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise SonosError("volume must be an integer from 0 to 100")
            soap_action = "SetVolume"
            arguments = (("InstanceID", 0), ("Channel", "Master"), ("DesiredVolume", value))
            service_name = "RenderingControl"
        elif normalized_action == "seek":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SonosError("seek must be a nonnegative integer number of seconds")
            soap_action = "Seek"
            arguments = (("InstanceID", 0), ("Unit", "REL_TIME"), ("Target", _format_sonos_time(value)))
            service_name = "AVTransport"
        else:
            raise SonosError("unsupported Sonos action")

        coordinator = self._coordinator(record)
        self._soap_call(coordinator, service_name, soap_action, arguments)
        self._mark_verified_activity()
        return {
            "ok": True,
            "speaker_id": record.id,
            "coordinator_id": coordinator.id,
            "action": normalized_action,
            "value": value,
        }

    def status(self, speaker_id: str) -> dict:
        """Return group transport, volume, and current-track information."""
        record = self._require_speaker(speaker_id)
        coordinator = self._coordinator(record)
        transport = self._soap_call(coordinator, "AVTransport", "GetTransportInfo", (("InstanceID", 0),))
        volume_response = self._soap_call(
            coordinator,
            "RenderingControl",
            "GetVolume",
            (("InstanceID", 0), ("Channel", "Master")),
        )
        position = self._soap_call(coordinator, "AVTransport", "GetPositionInfo", (("InstanceID", 0),))
        volume: int | None = None
        if volume_response.get("CurrentVolume", ""):
            try:
                candidate_volume = int(volume_response["CurrentVolume"])
            except ValueError as exc:
                raise SonosError("Sonos returned an invalid volume") from exc
            if not 0 <= candidate_volume <= 100:
                raise SonosError("Sonos returned an out-of-range volume")
            volume = candidate_volume
        track = self._track_from_position(position)
        group = [
            self._public_speaker(member)
            for member in sorted(self._speakers.values(), key=lambda item: item.id)
            if member.coordinator_id == coordinator.id
        ]
        state = transport.get("CurrentTransportState") or None
        self._mark_verified_activity()
        return {
            "speaker_id": record.id,
            "coordinator_id": coordinator.id,
            "transport_state": state,
            "playing": state == "PLAYING",
            "volume": volume,
            "position_seconds": track["position_seconds"] if track else None,
            "track": track,
            "group": group,
        }

    @staticmethod
    def _track_from_position(position: dict[str, str]) -> dict | None:
        metadata_text = position.get("TrackMetaData", "")
        metadata_root: ET.Element | None = None
        if metadata_text:
            metadata_root = _bounded_xml(metadata_text, limit=MAX_SOAP_XML_BYTES, context="track metadata")
        uri = position.get("TrackURI", "")
        title = artist = album = artwork_url = ""
        if metadata_root is not None:
            item = next(
                (element for element in metadata_root.iter() if _local_name(element.tag) in {"item", "container"}),
                metadata_root,
            )
            title = _element_text(item, "title")
            artist = _element_text(item, "artist")
            album = _element_text(item, "album")
            artwork_url = _element_text(item, "albumArtURI")
            if not uri:
                uri = _element_text(item, "res")
        if not uri and not title and not artist and not album:
            return None
        for field, value in (("track URI", uri), ("track title", title), ("track artist", artist), ("track album", album), ("artwork URL", artwork_url)):
            _safe_text(value, field=field, limit=MAX_URL_LENGTH if field.endswith("URL") or field.endswith("URI") else MAX_TEXT_LENGTH)
        return {
            "url": uri,
            "title": title,
            "artist": artist,
            "album": album,
            "artwork_url": artwork_url,
            "duration_seconds": _parse_sonos_time(position.get("TrackDuration", "")),
            "position_seconds": _parse_sonos_time(position.get("RelTime", "")),
            "track_number": int(position["Track"]) if position.get("Track", "").isdigit() else None,
        }


service = SonosService()


__all__ = ["SonosError", "SonosService", "SonosTrack", "service"]
