"""Durable household defaults and member allowances (ADR 0024). Rates are integer bits/second."""
from __future__ import annotations

from ipaddress import IPv6Address, ip_address, ip_network
from itertools import count

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSettings, User


# Startup and owner writes publish this tiny hint so disabled-policy HTTP paths keep their existing query cost.
# The durable object remains authoritative; enabled transfers reload it through the short delivery cache.
_runtime: tuple[bool, int] = (False, 0)
_revisions = count(1)


def configure_runtime(policy: HouseholdResources) -> None:
    global _runtime
    _runtime = (policy.upload_bps is not None, next(_revisions))


def runtime_state() -> tuple[bool, int]:
    return _runtime


def playback_device(request: Request) -> str:
    """Use authenticated credential provenance for browser/device session ownership."""
    return (getattr(request.state, "resource_device_id", None)
            or getattr(request.state, "connected_app_id", None) or "web")


class ResourceAllowances(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    remote_stream_bps: int = Field(default=8_000_000, ge=0, le=10_000_000_000)
    remote_member_bps: int = Field(default=10_000_000, ge=0, le=10_000_000_000)
    playback_sessions: int = Field(default=2, ge=0, le=1000)
    video_encodes: int = Field(default=1, ge=0, le=1000)
    download_running: int = Field(default=1, ge=0, le=1000)
    download_queued: int = Field(default=25, ge=0, le=10000)
    ai_running: int = Field(default=1, ge=0, le=1000)
    ai_queued: int = Field(default=10, ge=0, le=10000)

    @model_validator(mode="after")
    def relationships(self) -> ResourceAllowances:
        if self.remote_member_bps and (not self.remote_stream_bps or self.remote_stream_bps > self.remote_member_bps):
            raise ValueError("The stream ceiling must fit the member bandwidth allowance.")
        if self.playback_sessions and (not self.video_encodes or self.video_encodes > self.playback_sessions):
            raise ValueError("Video encode slots must fit the member playback slots.")
        return self


class HouseholdResources(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    upload_bps: int | None = Field(default=None, ge=128_000, le=10_000_000_000)
    lan_networks: list[str] = Field(default_factory=list, max_length=32)
    defaults: ResourceAllowances = Field(default_factory=ResourceAllowances)

    @field_validator("lan_networks")
    @classmethod
    def networks(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            network = ip_network(value, strict=True)
            if network.prefixlen == 0 or network.is_multicast or network.is_unspecified:
                raise ValueError("Choose explicit LAN networks, not a catch-all network.")
            result.append(str(network))
        return list(dict.fromkeys(result))


class ResourceOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    remote_stream_bps: int | None = Field(default=None, ge=0, le=10_000_000_000)
    remote_member_bps: int | None = Field(default=None, ge=0, le=10_000_000_000)
    playback_sessions: int | None = Field(default=None, ge=0, le=1000)
    video_encodes: int | None = Field(default=None, ge=0, le=1000)
    download_running: int | None = Field(default=None, ge=0, le=1000)
    download_queued: int | None = Field(default=None, ge=0, le=10000)
    ai_running: int | None = Field(default=None, ge=0, le=1000)
    ai_queued: int | None = Field(default=None, ge=0, le=10000)


def household(db: Session) -> HouseholdResources:
    record = db.get(AppSettings, 1)
    return HouseholdResources.model_validate(record.resource_policy or {} if record else {})


def effective(db: Session, user_id: str | None) -> ResourceAllowances:
    defaults = household(db).defaults.model_dump()
    user = db.get(User, user_id) if user_id else None
    if user:
        defaults.update(ResourceOverrides.model_validate(user.resource_overrides or {}).model_dump(exclude_none=True))
    return ResourceAllowances.model_validate(defaults)


def remote(request: Request, policy: HouseholdResources) -> bool:
    """Only verified, explicitly exempted networks are local; tunnel/ambiguous traffic is remote."""
    if not policy.lan_networks or request.client is None:
        return True
    try:
        peer = ip_address(request.client.host)
        if isinstance(peer, IPv6Address) and peer.ipv4_mapped:
            peer = peer.ipv4_mapped
        proxies = settings.trusted_proxy_networks
        trusted = any(peer.version == net.version and peer in net for net in proxies)
        if request.headers.get("cf-connecting-ip") or request.headers.get("forwarded"):
            return True
        forwarded = request.headers.get("x-forwarded-for")
        if trusted:
            if not forwarded:
                return True
            hops = [ip_address(value.strip()) for value in forwarded.split(",")]
            # Reject client-selected prefixes and all-proxy chains, including forged private visitor addresses.
            if len(hops) != 1:
                return True
            peer = hops[0]
            if isinstance(peer, IPv6Address) and peer.ipv4_mapped:
                peer = peer.ipv4_mapped
            if any(peer.version == net.version and peer in net for net in proxies):
                return True
        elif forwarded or request.headers.get("x-forwarded-proto"):
            return True
        return not any(peer.version == net.version and peer in net for net in map(ip_network, policy.lan_networks))
    except ValueError:
        return True
