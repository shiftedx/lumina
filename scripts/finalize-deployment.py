#!/usr/bin/env python3
"""After deployment verification, retain the current and two prior known-good images."""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import re
import subprocess

SOURCE = 'https://github.com/shiftedx/lumina'


def docker(*args: str) -> str:
    return subprocess.check_output(['docker', *args], text=True)


def inspect(name: str) -> dict:
    return json.loads(docker('inspect', name))[0]


def safe_pin(row: dict) -> bool:
    return (not row['State']['Running'] and row['Config'].get('Healthcheck', {}).get('Test') == ['NONE']
            and row['HostConfig']['RestartPolicy']['Name'] == 'no' and row['HostConfig']['NetworkMode'] == 'none')


def finalize(container: str, state: Path) -> dict:
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_name(state.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = inspect(container)
        if current['State'].get('Health', {}).get('Status') != 'healthy':
            raise ValueError('Deployment is not healthy; retention unchanged')
        rows = [inspect(name) for name in docker('ps', '-a', '--format', '{{.Names}}').splitlines()]
        pins = [row for row in rows if row['Name'].startswith('/' + container + '-') and 'rollback' in row['Name']]
        if any(not safe_pin(row) for row in pins):
            raise ValueError('Rollback pins must be stopped, isolated and healthcheck-disabled')
        if not state.exists() and pins:
            raise ValueError('Initialize history from verified deployments before retiring existing pins')
        history = json.loads(state.read_text())['images'] if state.exists() else []
        if (not isinstance(history, list) or len(history) > 3
                or any(not isinstance(value, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', value) for value in history)
                or len(set(history)) != len(history)):
            raise ValueError('Invalid known-good image history')
        images = [current['Image'], *(image for image in history if image != current['Image'])][:3]
        for image in images:
            docker('image', 'inspect', image)  # Validate every recovery image before removing anything.
        keep = set()
        for image in images[1:]:
            matching = sorted((row for row in pins if row['Image'] == image), key=lambda row: row['Created'], reverse=True)
            if matching:
                keep.add(matching[0]['Name'])
            else:
                name = container + '-rollback-' + image.removeprefix('sha256:')[:12]
                subprocess.check_call(['sh', str(Path(__file__).with_name('preserve-rollback.sh')), image, name], stdout=subprocess.DEVNULL)
                pin = inspect(name)
                if pin['Image'] != image or not safe_pin(pin):
                    raise ValueError('Recovery pin validation failed')
                keep.add(pin['Name'])
        fresh = inspect(container)
        if fresh['Image'] != current['Image'] or fresh['State']['Health']['Status'] != 'healthy':
            raise ValueError('Deployment changed during verification; retention unchanged')
        temporary = state.with_name(state.name + '.tmp')
        temporary.write_text(json.dumps({'images': images}, indent=2) + '\n')
        temporary.chmod(0o600)
        temporary.replace(state)
        removed = [row['Name'] for row in pins if row['Name'] not in keep]
        for name in removed:
            docker('container', 'rm', name)
        # Docker protects all images referenced by any remaining container, including unrelated services.
        docker('image', 'prune', '--all', '--force', '--filter', 'label=org.opencontainers.image.source=' + SOURCE)
        for image in images:
            docker('image', 'inspect', image)
        return {'known_good_images': len(images), 'rollback_pins': len(keep), 'retired_pins': len(removed)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('container', help='Production container name, for example lumina')
    parser.add_argument('state', type=Path, help='Persistent known-good image history outside disposable checkouts')
    args = parser.parse_args()
    print(json.dumps(finalize(args.container, args.state)))
