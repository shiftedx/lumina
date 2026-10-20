"""Personal connection, extraction isolation and revocation at the API/service boundaries."""
from http.cookiejar import CookieJar
from unittest.mock import Mock

import pytest
import yt_dlp

from app.models import YouTubeSession
from app.services import two_factor, youtube_session as sessions
from app.services.yt_dlp_service import YtDlpService, _PREVIEW_CACHE, _RESOLVE_CACHE
from app.services.remote_streaming import RemoteStreamingService, StreamNotFoundError, redact_preview_info
from support import make_user
from test_remote_streaming import FakeHlsPackager, FakeReader, NoopResolver, progressive_info

VIDEO = 'https://www.youtube.com/watch?v=abcdefghijk'


def cookie_file(value='synthetic-session', expiry=4102444800):
    return f'# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t{expiry}\tSAPISID\t{value}\n'


def test_connection_is_explicit_encrypted_owner_bound_and_not_exported(db_factory, api_client):
    alice, bob = make_user('alice'), make_user('bob')
    with db_factory.begin() as db:
        db.add_all([alice, bob])
    current = {'user': alice}
    client = api_client(user=lambda: current['user'], base_url='http://localhost')
    path = '/api/me/youtube-session'
    assert client.get(path).json()['status'] == 'not_connected'
    assert client.put(path, json={'cookies': cookie_file(), 'consent': False}).status_code == 400
    invalid = client.put(path, json={'cookies': 'sensitive-invalid-input', 'consent': True})
    assert invalid.status_code == 400 and 'sensitive-invalid-input' not in invalid.text
    payload = cookie_file() + '.example.com\tTRUE\t/\tTRUE\t4102444800\tSID\tsynthetic-other-site\n'
    result = client.put(path, json={'cookies': payload, 'consent': True})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'connected'
    assert result.headers['cache-control'] == 'no-store'
    assert 'synthetic-session' not in result.text
    with db_factory() as db:
        row = db.get(YouTubeSession, alice.id)
        assert 'synthetic-session' not in row.encrypted_cookies
        assert two_factor.unseal(row.encrypted_cookies, f'youtube-session:{bob.id}') is None
        assert two_factor.unseal(row.encrypted_cookies, alice.id) is None
        saved = sessions.load(db, alice.id)
        assert [(c.domain, c.name, c.secure) for c in saved.cookies] == [('.youtube.com', 'SAPISID', True)]
    assert two_factor.key_path().stat().st_mode & 0o777 == 0o600
    current['user'] = bob
    assert client.get(path).json()['status'] == 'not_connected'
    assert client.delete(path).status_code == 200
    current['user'] = alice
    assert client.get(path).json()['status'] == 'connected'
    assert 'synthetic-session' not in client.get('/api/me/export').text
    assert client.delete(path).json()['status'] == 'not_connected'
    with db_factory() as db:
        assert db.get(YouTubeSession, alice.id) is None


def test_expired_session_or_missing_key_requires_reconnection(db_factory, api_client, monkeypatch):
    user = make_user('member')
    with db_factory() as db:
        db.add(user)
        db.commit()
        sessions.connect(db, user.id, cookie_file(expiry=2000000000))
    client = api_client(user=user, base_url='http://localhost')
    monkeypatch.setattr(sessions.time, 'time', lambda: 2000000001)
    assert client.get('/api/me/youtube-session').json()['status'] == 'needs_reconnect'
    monkeypatch.setattr(sessions.time, 'time', lambda: 1000000000)
    two_factor.key_path().unlink()
    assert client.get('/api/me/youtube-session').json()['status'] == 'needs_reconnect'
    assert not two_factor.key_path().exists()  # reading never silently replaces a lost encryption key


class Extractor:
    def __init__(self, options, calls, *, fail=False):
        self.options, self.calls, self.fail = options, calls, fail
        self.cookiejar = CookieJar()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def sanitize_info(self, info): return info
    def extract_info(self, url, *, download):
        values = [cookie.value for cookie in self.cookiejar]
        self.calls.append((url, values, download, dict(self.options)))
        if not values or self.fail:
            raise yt_dlp.utils.DownloadError('Sign in to confirm your age')
        info = progressive_info()
        info.update(extractor='youtube', extractor_key='Youtube', webpage_url=VIDEO, availability='needs_auth', cookies=values[0])
        return info


def test_personal_retry_is_uncached_never_reaches_another_member_or_acquisition(db_factory, monkeypatch):
    calls = []
    monkeypatch.setattr(YtDlpService, "validate_source_url", lambda self, url: url)
    _PREVIEW_CACHE.clear(); _RESOLVE_CACHE.clear()
    with db_factory() as db:
        sessions.connect(db, 'alice', cookie_file('synthetic-alice'))
        sessions.connect(db, 'bob', cookie_file('synthetic-bob'))
        service = YtDlpService(db, ydl_factory=lambda options: Extractor(options, calls))
        for owner, expected in [('alice', 'synthetic-alice'), ('bob', 'synthetic-bob'), ('alice', 'synthetic-alice')]:
            response = service.preview_for_playback(VIDEO, owner)
            assert response.capabilities.can_play and not response.capabilities.can_acquire
            assert calls[-1][1] == [expected]
            assert all('cookie' not in key.lower() for key in calls[-1][3])
            assert 'cookies' not in response.raw
            assert response.raw['formats'][0]['http_headers'] == {}
            assert '_lumina_youtube_session' not in redact_preview_info(response.raw)
        assert not _PREVIEW_CACHE and not _RESOLVE_CACHE
        with pytest.raises(yt_dlp.utils.DownloadError, match='personal settings'):
            service.preview_for_playback(VIDEO, 'unconnected')
        with pytest.raises(yt_dlp.utils.DownloadError):
            service.preview(VIDEO)  # discovery/acquisition previews remain anonymous
        assert calls[-1][1] == []
        # A session is loaded only for canonical single-video YouTube links.
        with pytest.raises(yt_dlp.utils.DownloadError):
            service.preview_for_playback('https://source.example/watch/1', 'alice')
        assert calls[-1][1] == []


def test_failed_authenticated_retry_is_safe_and_does_not_assume_account_expired(db_factory):
    calls = []
    with db_factory() as db:
        sessions.connect(db, 'alice', cookie_file())
        service = YtDlpService(db, ydl_factory=lambda options: Extractor(options, calls, fail=True))
        with pytest.raises(yt_dlp.utils.DownloadError) as failure:
            service.preview_for_playback(VIDEO, 'alice')
        assert str(failure.value) == sessions.FALLBACK
        assert YtDlpService.explain_download_error(str(failure.value)) == sessions.FALLBACK
        assert YtDlpService.classify_download_error(str(failure.value)) == 'sign_in_required'
        assert sessions.snapshot(db, 'alice')['status'] == 'connected'


def test_disconnect_revokes_private_streams_and_they_never_use_disk_cache(db_factory, api_client, monkeypatch):
    from app import main
    monkeypatch.setattr(sessions, 'SessionLocal', db_factory)
    cache = Mock()
    service = RemoteStreamingService(resolver=NoopResolver(), reader=FakeReader(), hls_packager=FakeHlsPackager(), stream_cache=cache)
    monkeypatch.setattr(main, 'remote_streams', service)
    user = make_user('alice')
    with db_factory() as db:
        db.add(user)
        db.commit()
        sessions.connect(db, user.id, cookie_file())
        generation = sessions.load(db, user.id).generation
    info = progressive_info()
    info['_lumina_youtube_session'] = generation
    private = service.register(owner_user_id=user.id, source_url=VIDEO, info=info)
    public = service.register(owner_user_id='bob', source_url=VIDEO, info=progressive_info())
    spec = service.serve_content(user.id, private.stream_id, 'bytes=0-4')
    assert b''.join(spec.body) == b'progr'
    spec.close()
    cache.lookup.assert_not_called()
    cache.capture.assert_not_called()
    client = api_client(user=user, base_url='http://localhost')
    assert client.delete('/api/me/youtube-session').status_code == 200
    with pytest.raises(StreamNotFoundError): service.describe(user.id, private.stream_id)
    assert service.describe('bob', public.stream_id).status == 'ready'
    # In-flight extraction that registers after disconnect still cannot serve a retained signed URL.
    late = service.register(owner_user_id=user.id, source_url=VIDEO, info=info)
    with pytest.raises(StreamNotFoundError): service.describe(user.id, late.stream_id)


@pytest.mark.parametrize('text', [
    cookie_file(expiry=1),
    cookie_file().replace('.youtube.com', '.youtube.com.example'),
    cookie_file().replace('4102444800', '9' * 5000),
    cookie_file().replace('synthetic-session', 'invalid; injected=value'),
    '# Netscape HTTP Cookie File\nmalformed-sensitive-content',
])
def test_bad_cookie_imports_are_rejected_without_echoing_or_saving_them(db_factory, api_client, text):
    user = make_user('member')
    client = api_client(user=user, base_url='http://localhost')
    result = client.put('/api/me/youtube-session', json={'cookies': text, 'consent': True})
    assert result.status_code == 400
    assert 'synthetic-session' not in result.text and 'malformed-sensitive-content' not in result.text
    with db_factory() as db:
        assert db.get(YouTubeSession, user.id) is None


def test_preview_api_uses_only_the_requesting_members_session_and_returns_safe_fallback(db_factory, api_client, monkeypatch):
    from app import main
    import app.services.yt_dlp_service as extractor_module
    calls = []
    alice, bob = make_user('alice'), make_user('bob')
    with db_factory() as db:
        db.add_all([alice, bob]); db.commit()
        sessions.connect(db, alice.id, cookie_file('synthetic-private-session'))
    user = {'current': alice}
    monkeypatch.setattr(main, 'resolve_request_user_snapshot', lambda *args: user['current'])
    monkeypatch.setattr(main, 'SessionLocal', db_factory)
    monkeypatch.setattr(main, '_gate_snapshot', lambda *args, **kwargs: None)
    monkeypatch.setattr(main, '_require_provider_policy', lambda *args: None)
    monkeypatch.setattr(main, '_playback_ceiling', lambda *args: None)
    monkeypatch.setattr(YtDlpService, 'validate_source_url', lambda self, url: url)
    monkeypatch.setattr(extractor_module, 'PolicyYoutubeDL', lambda options, **kwargs: Extractor(options, calls))
    streams = RemoteStreamingService(resolver=NoopResolver(), reader=FakeReader(), hls_packager=FakeHlsPackager())
    monkeypatch.setattr(main, 'remote_streams', streams)
    published = []
    monkeypatch.setattr(main.events, 'publish', lambda name, payload: published.append(payload))
    client = api_client(user=lambda: user['current'], base_url='http://localhost')
    result = client.post('/api/preview', json={'source_url': VIDEO})
    assert result.status_code == 200, result.text
    assert result.json()['playback']['status'] == 'ready'
    assert result.json()['capabilities']['can_acquire'] is False
    assert 'synthetic-private-session' not in result.text and '_lumina_' not in result.text
    assert 'synthetic-private-session' not in str(published)
    user['current'] = bob
    denied = client.post('/api/preview', json={'source_url': VIDEO})
    assert denied.status_code == 400
    assert denied.json()['detail'] == {'category': 'sign_in_required', 'message': sessions.FALLBACK}
