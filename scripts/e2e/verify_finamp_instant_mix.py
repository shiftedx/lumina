"""Replay Finamp 1.0.2's InstantMix request against the disposable #343 server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def login(client: httpx.Client, username: str, password: str, device: str) -> str:
    auth = f'MediaBrowser Client="Finamp protocol proof", Device="Proof", DeviceId="{device}", Version="1.0.2"'
    result = client.post('/Users/AuthenticateByName', json={'Username': username, 'Pw': password},
                         headers={'Authorization': auth}).raise_for_status().json()
    client.headers['Authorization'] = f'{auth}, Token="{result["AccessToken"]}"'
    return result['User']['Id']


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    fixture = json.loads(parser.parse_args().fixture.read_text())
    origin = urlsplit(fixture['serverURL'])
    if origin.scheme != 'http' or origin.hostname != '127.0.0.1' or not fixture.get('musicHomeWaitRequestID'):
        parser.error('Use a disposable #343 loopback proof server.')
    with (httpx.Client(base_url=fixture['serverURL'], timeout=30, trust_env=False) as owner,
          httpx.Client(base_url=fixture['serverURL'], timeout=30, trust_env=False) as member):
        owner_id = login(owner, fixture['username'], fixture['password'], 'finamp-proof-owner')
        member_id = login(member, fixture['musicHomeMemberUsername'], fixture['musicHomeMemberPassword'], 'finamp-proof-member')
        def mix(client, user_id, seed, limit=100):  # noqa: ANN001, ANN202
            return client.get(f'/Items/{seed}/InstantMix', params={
                'userId': user_id, 'limit': limit, 'enableImages': 'true',
                'enableImageTypes': 'Primary,Disc,Thumb,Art',
            }).raise_for_status().json()
        first, second = fixture['audioTrackID'], fixture['audioSecondTrackID']
        for seed in (first, fixture['audioAlbumID'], fixture['audioArtistID']):
            rows = mix(owner, owner_id, seed)['Items']
            assert {first, second} <= {row['Id'] for row in rows}
            assert all(row['Type'] == 'Audio' for row in rows)
        assert mix(owner, owner_id, first)['Items'][0]['Id'] == first
        assert len(mix(owner, owner_id, first, 1)['Items']) == 1
        assert mix(owner, owner_id, first, 0)['Items'] == []
        for key in ('musicHomePrivateTrackID', 'musicHomePrivateAlbumID'):
            assert mix(owner, owner_id, fixture[key])['Items'] == []
            assert mix(member, member_id, fixture[key])['Items']
        print('PASS Finamp-shaped track, album, artist, limits and member visibility')


if __name__ == '__main__':
    main()
