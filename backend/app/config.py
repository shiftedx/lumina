from __future__ import annotations

import logging
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path
import re
from urllib.parse import ParseResult, urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


APP_VERSION = "2.10.2"
WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]"}
# Opt-in LAN HTTP mode (ADR 0001 amendment): RFC 1918 IPv4 only. No IPv6: Starlette's TrustedHostMiddleware
# cannot match a bracketed IPv6 Host header, so such a URL would start but reject every request.
logger = logging.getLogger(__name__)
LAN_NETWORKS = tuple(ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
DNS_HOST_PATTERN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


class EnvSettings(BaseSettings):
    data_dir: Path = Path(__file__).resolve().parents[1] / ".data"
    database_filename: str = "app.db"
    library_dirname: str = "library"
    temp_dirname: str = "temp"
    archive_filename: str = "archive.txt"
    host: str = "127.0.0.1"
    port: int = 8765
    reconcile_interval_seconds: int = 60
    remote_stream_idle_ttl_seconds: int = 30 * 60
    remote_stream_max_lifetime_seconds: int = 6 * 60 * 60
    remote_stream_refresh_margin_seconds: int = 60
    remote_stream_cache_dirname: str = "stream-cache"
    remote_stream_cache_max_bytes_global: int = 20 * 1024 * 1024 * 1024
    # Guarded upstream-HLS relay (the Twitch VOD playback path).
    relay_max_sessions_global: int = 24
    relay_max_sessions_per_user: int = 4
    # A playing relay requests every few seconds; this long without one means the tab is gone (or a VOD is paused,
    # which reacquires on resume). Live, never paused for long, goes sooner.
    relay_idle_ttl_seconds: int = 300
    live_relay_idle_ttl_seconds: int = 60
    relay_max_concurrent_reads_global: int = 16
    relay_max_concurrent_reads_per_user: int = 4
    relay_max_manifest_bytes: int = 4 * 1024 * 1024
    relay_max_bytes_per_response: int = 16 * 1024 * 1024
    relay_max_key_bytes: int = 4096
    relay_max_resources_per_generation: int = 8192
    relay_request_timeout_seconds: float = 30
    # Guarded live-HLS relay (the YouTube live viewing path). A live viewing
    # session is transient and has its own capacity pool, kept well below the
    # VOD relay's so a burst of live viewers cannot starve VOD playback.
    live_relay_max_sessions_global: int = 12
    live_relay_max_sessions_per_user: int = 2
    # Durable "record from now" live acquisitions (issue #97). A long-running
    # recording holds a worker thread pair and writes to disk for its whole
    # runtime, so its concurrency is bounded well below the transient viewing
    # pools. A recording is deterministically finalized after a bounded number of
    # restart-recovery re-launches so a poison recording cannot loop forever, and
    # the captured chat retains a bounded rolling window in memory.
    live_recording_max_active_global: int = 4
    live_recording_max_active_per_user: int = 2
    live_recording_max_recovery_attempts: int = 3
    # A per-recording runtime ceiling (like the live viewing session's lifetime)
    # and a per-recording disk ceiling bound one recording's cost. When either is
    # reached the recording is finalized as a usable partial, never a false
    # complete. Defaults: 6 hours, 8 GiB per recording.
    live_recording_max_runtime_seconds: float = 6 * 60 * 60
    live_recording_max_disk_bytes: int = 8 * 1024 * 1024 * 1024
    live_recording_media_poll_interval_seconds: float = 2.0
    live_recording_media_max_consecutive_failures: int = 6
    live_recording_chat_max_events: int = 5000
    live_recording_chat_max_consecutive_failures: int = 6
    live_recording_chat_poll_interval_seconds: float = 2.0
    # Provider request timeout for the recording's durable chat-capture fetcher
    # (the recording chat surface is the only live-chat consumer in 1.0).
    live_recording_chat_request_timeout_seconds: float = 20
    # Scheduling an upcoming broadcast (issue #98): the waiter re-inspects the
    # source at this cadence near the scheduled time, waits up to
    # ``max_probe_interval`` between probes when the start is far off, starts
    # probing ``pre_roll`` before the scheduled time, and gives up if the broadcast
    # is delayed past its scheduled start by more than ``grace`` (excessive delay).
    # ``max_probe_attempts`` is a defensive ceiling on total inspections.
    live_recording_schedule_poll_interval_seconds: float = 30.0
    live_recording_schedule_pre_roll_seconds: float = 60.0
    live_recording_schedule_max_probe_interval_seconds: float = 5 * 60.0
    live_recording_schedule_grace_seconds: float = 6 * 60 * 60.0
    live_recording_schedule_max_probe_attempts: int = 2000
    # A RESTART-DURABLE upper bound on the waiting phase (issue #98). The waiter's
    # own bounds are per-process — ``max_probe_attempts`` resets each ``wait()`` run
    # and the wall-clock grace only applies once a start time is known — so a
    # perpetually-upcoming source with NO announced start could hold an active slot
    # indefinitely across frequent restarts. ``plan_recovery`` enforces a durable
    # deadline from persisted columns: ``scheduled_start_at + grace`` when a start is
    # known, else ``created_at +`` this cap. 48h is generous enough for a
    # no-announced-start upcoming that goes live within ~2 days, while still bounding
    # a source that never appears.
    live_recording_schedule_max_waiting_seconds: float = 48 * 60 * 60.0
    # Comma-separated container paths under which admins may register storage
    # roots (the deployment's media mounts). Empty = only the built-in library.
    storage_mount_parents: str = ""
    # Recovery window before quarantined managed files are purged.
    quarantine_retention_hours: float = 7 * 24
    app_public_url: str | None = None
    frontend_public_url: str | None = None
    allowed_origins: str = ""
    trusted_proxy_ips: str = ""
    # LUMINA_LAN_HTTP=1: serve plain http on a private LAN IP public URL without a proxy (ADR 0001 amendment).
    lan_http: bool = False
    session_cookie_name: str = "archive_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_duration_hours: int = 24 * 14  # absolute session lifetime
    session_idle_hours: int = 24 * 3
    enable_api_docs: bool = False
    # LUMINA_JELLYFIN_TRACE=1: log method, route template and query parameter names (never values) per Jellyfin request.
    jellyfin_trace: bool = False
    # LUMINA_ARTWORK_PASS=off: no background artwork renditions (the realstack e2e server; on-demand still works).
    artwork_pass: bool = True
    # Admin-approved private local inference endpoint (OpenAI-compatible). These
    # env values are the defaults until an admin saves their own; unset/"" disables.
    ai_base_url: str = ""
    ai_model: str = ""
    ai_max_concurrency: int = 3
    ai_context_tokens: int = 145_000
    asr_base_url: str = ""
    asr_model: str = ""
    # On-device model runtimes baked into the image. Operators never set these;
    # tests point them at a stub server.
    llama_server_bin: str = "/opt/lumina-llama/bin/llama-server"
    asr_python: str = "/opt/lumina-asr/bin/python"
    asr_server_script: str = "/opt/lumina-asr/server.py"
    # TMDB v3 API key used until an admin saves one (write-only). Unset/"" = no TMDB.
    tmdb_api_key: str = ""
    # Read-only folder of cast photos in the metadata/People layout; unset/"" = off.
    people_dir: str = ""
    # The name before 2.11, used while LUMINA_PEOPLE_DIR is empty: docker-compose.yml always passes the
    # new name, so an alias would let its empty value hide an old name set in an override.
    jellyfin_people_dir: str = ""

    model_config = SettingsConfigDict(env_prefix="LUMINA_", extra="ignore")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / self.database_filename}"

    @property
    def library_root(self) -> Path:
        return self.data_dir / self.library_dirname

    @property
    def temp_root(self) -> Path:
        return self.data_dir / self.temp_dirname

    @property
    def remote_stream_cache_root(self) -> Path:
        return self.data_dir / self.remote_stream_cache_dirname

    @property
    def archive_path(self) -> Path:
        return self.data_dir / self.archive_filename

    @property
    def resolved_app_public_url(self) -> str:
        explicit = (self.app_public_url or "").strip().rstrip("/")
        if explicit:
            return explicit
        public_host = "127.0.0.1" if self.host in WILDCARD_HOSTS else self.host
        return f"http://{public_host}:{self.port}"

    @property
    def resolved_frontend_public_url(self) -> str:
        explicit = (self.frontend_public_url or "").strip().rstrip("/")
        if explicit:
            return explicit
        return self.resolved_app_public_url

    @property
    def allowed_origins_list(self) -> list[str]:
        configured = [value.strip().rstrip("/") for value in self.allowed_origins.split(",") if value.strip()]
        if self.resolved_app_public_url not in configured:
            configured.append(self.resolved_app_public_url)
        if self.resolved_frontend_public_url not in configured:
            configured.append(self.resolved_frontend_public_url)
        parsed_public_url = urlparse(self.resolved_app_public_url)
        local_http_public_url = parsed_public_url.scheme == "http" and parsed_public_url.hostname in {"127.0.0.1", "localhost"}
        if self.host in WILDCARD_HOSTS and (local_http_public_url or self.lan_http):
            localhost_aliases = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
            for origin in sorted(localhost_aliases):
                if origin not in configured:
                    configured.append(origin)
        return configured

    @property
    def trusted_hosts_list(self) -> list[str]:
        hosts = {"127.0.0.1", "localhost"}
        for candidate in (self.resolved_app_public_url, self.resolved_frontend_public_url):
            parsed = urlparse(candidate)
            if parsed.hostname:
                hosts.add(parsed.hostname)
        return sorted(hosts)

    @property
    def trusted_proxy_networks(self) -> list[IPv4Network | IPv6Network]:
        networks: list[IPv4Network | IPv6Network] = []
        for value in self.trusted_proxy_ips.split(","):
            candidate = value.strip()
            if candidate:
                networks.append(ip_network(candidate, strict=False))
        return networks

    def validate_runtime_security(self) -> None:
        public_url = urlparse(self.resolved_app_public_url)
        hostname = (public_url.hostname or "").casefold()
        if (
            public_url.username is not None
            or public_url.password is not None
            or public_url.path not in {"", "/"}
            or public_url.params
            or public_url.query
            or public_url.fragment
        ):
            raise ValueError("The public URL must be a canonical origin without credentials, path, query, or fragment.")
        if self.lan_http:
            self._validate_lan_http(public_url)
            return
        try:
            parsed_address = ip_address(hostname)
            loopback_host = parsed_address.is_loopback
            public_host_is_ip = True
        except ValueError:
            loopback_host = hostname == "localhost"
            public_host_is_ip = False
        proxy_configured = bool(self.trusted_proxy_networks)

        if not loopback_host and public_url.scheme != "https":
            raise ValueError("Remote Lumina access must use HTTPS behind the supported trusted proxy.")
        if not loopback_host and (public_host_is_ip or not DNS_HOST_PATTERN.fullmatch(hostname) or public_url.port not in {None, 443}):
            raise ValueError("Remote Lumina access requires a bare DNS hostname on the standard HTTPS port.")
        if not loopback_host and self.resolved_app_public_url != self.resolved_app_public_url.lower():
            raise ValueError("Remote Lumina access requires a lowercase canonical origin.")
        if (self.session_cookie_secure or proxy_configured) and public_url.scheme != "https":
            raise ValueError("Secure cookies or a trusted proxy require an HTTPS public URL.")
        if public_url.scheme == "https" and not self.session_cookie_secure:
            raise ValueError("Secure session cookies are required for the HTTPS deployment.")
        if public_url.scheme == "https" and not proxy_configured:
            raise ValueError("The HTTPS deployment requires at least one explicitly trusted proxy peer.")
        if public_url.scheme == "https":
            networks = self.trusted_proxy_networks
            if len(networks) != 1 or networks[0].prefixlen != networks[0].max_prefixlen:
                raise ValueError("The supported HTTPS deployment requires exactly one proxy IP, not a subnet or chain.")
            if self.resolved_frontend_public_url != self.resolved_app_public_url:
                raise ValueError("The HTTPS deployment requires the app and frontend to use the same public URL.")
            if set(self.allowed_origins_list) != {self.resolved_app_public_url}:
                raise ValueError("The HTTPS deployment allows only the public URL as a browser origin.")

    def _validate_lan_http(self, public_url: ParseResult) -> None:
        if public_url.scheme != "http":
            raise ValueError("LAN HTTP mode serves plain http only; use the Remote HTTPS setup for https.")
        try:
            address = ip_address(public_url.hostname or "")
        except ValueError:
            address = None
        if address is None or not any(address in network for network in LAN_NETWORKS if network.version == address.version):
            raise ValueError("LAN HTTP mode requires a private LAN IP public URL (10/8, 172.16/12 or 192.168/16), never a DNS name, IPv6 or public IP.")
        if self.resolved_app_public_url != self.resolved_app_public_url.lower():
            raise ValueError("LAN HTTP mode requires a lowercase canonical origin.")
        if self.session_cookie_secure:
            raise ValueError("LAN HTTP mode cannot use Secure cookies over plain http.")
        # A trusted proxy is allowed beside LAN mode: it fronts the optional public address.
        # LAN requests still arrive over plain http; only requests the proxy marks https get Secure cookies.
        # A range is allowed (Traefik's docker bridge IP is not fixed) but every peer in it may set X-Forwarded-*: warn
        # unless it is a private bridge-sized range (/16 or narrower) clear of the LAN's own /24.
        lan = ip_network(f"{address}/24", strict=False)
        for network in self.trusted_proxy_networks:
            if (
                network.version != 4 or network.prefixlen < 16 or network.overlaps(lan)
                or not any(network.subnet_of(private) for private in LAN_NETWORKS)
            ):
                logger.warning(
                    "LUMINA_TRUSTED_PROXY_IPS trusts %s: every host in it may spoof client addresses and https. "
                    "Name only the reverse proxy's docker network (a private /16 or narrower, not the LAN %s).",
                    network, lan,
                )
        if self.resolved_frontend_public_url != self.resolved_app_public_url:
            raise ValueError("LAN HTTP mode requires the app and frontend to use the same public URL.")
        loopback_aliases = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        if not set(self.allowed_origins_list) <= {self.resolved_app_public_url} | loopback_aliases:
            raise ValueError("LAN HTTP mode allows only the public URL (and loopback) as a browser origin.")

    @property
    def remote_https_enabled(self) -> bool:
        return urlparse(self.resolved_app_public_url).scheme == "https"


settings = EnvSettings()
