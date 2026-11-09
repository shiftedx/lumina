"""Deployment completion retains verified recovery images before pruning old ones."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
A, B, C, D, OLD = ['sha256:' + char * 64 for char in 'abcde']


def pin(name, image, created='2026-11-01'):  # noqa: ANN001, ANN201
    return {'Name': '/' + name, 'Image': image, 'Created': created,
            'State': {'Running': False}, 'Config': {'Healthcheck': {'Test': ['NONE']}},
            'HostConfig': {'RestartPolicy': {'Name': 'no'}, 'NetworkMode': 'none'}}


@pytest.fixture
def retention(monkeypatch):  # noqa: ANN001, ANN201
    spec = importlib.util.spec_from_file_location('deployment_retention', ROOT / 'scripts/finalize-deployment.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = {'lumina': {'Name': '/lumina', 'Image': D, 'State': {'Running': True, 'Health': {'Status': 'healthy'}}}}
    images, commands, creations = {A, B, C, D, OLD}, [], []

    def docker(*args):  # noqa: ANN002, ANN201
        commands.append(args)
        if args[0] == 'inspect':
            return json.dumps([rows[args[1].lstrip('/')]])
        if args[0] == 'ps':
            return '\n'.join(rows)
        if args[:2] == ('image', 'inspect'):
            if args[2] not in images:
                raise subprocess.CalledProcessError(1, ['docker', *args])
            return json.dumps([{'Id': args[2]}])
        if args[:2] == ('container', 'rm'):
            del rows[args[2].lstrip('/')]
            return ''
        if args[:2] == ('image', 'prune'):
            assert args[2:] == ('--all', '--force', '--filter', 'label=org.opencontainers.image.source=https://github.com/shiftedx/lumina')
            images.intersection_update(row['Image'] for row in rows.values())
            return ''
        raise AssertionError(args)

    def create(args, **kwargs):  # noqa: ANN001, ANN003, ANN201
        assert args[:2] == ['sh', str(ROOT / 'scripts/preserve-rollback.sh')]
        image, name = args[-2:]
        rows[name] = pin(name, image)
        creations.append(name)
        return 0

    monkeypatch.setattr(module, 'docker', docker)
    monkeypatch.setattr(module.subprocess, 'check_call', create)
    return module, rows, images, commands, creations


def history(tmp_path, images):  # noqa: ANN001, ANN201
    path = tmp_path / 'known-good.json'
    path.write_text(json.dumps({'images': images}))
    return path


def test_success_keeps_three_distinct_verified_images_and_protects_other_containers(retention, tmp_path):  # noqa: ANN001, ANN201
    module, rows, images, commands, _ = retention
    state = history(tmp_path, [A, B, C])
    rows.update({name: pin(name, image) for name, image in [('lumina-a-rollback', A), ('lumina-b-rollback', B), ('lumina-c-rollback', C), ('lumina-old-rollback', OLD)]})
    rows['unrelated'] = {'Name': '/unrelated', 'Image': C}
    result = module.finalize('lumina', state)
    assert json.loads(state.read_text())['images'] == [D, A, B]
    assert result == {'known_good_images': 3, 'rollback_pins': 2, 'retired_pins': 2}
    assert set(rows) == {'lumina', 'lumina-a-rollback', 'lumina-b-rollback', 'unrelated'}
    assert images == {D, A, B, C}  # Docker retains C for the unrelated container.
    assert ('image', 'inspect', B) in commands[:commands.index(('container', 'rm', '/lumina-c-rollback'))]


def test_completion_creates_missing_safe_pins_and_is_idempotent(retention, tmp_path):  # noqa: ANN001, ANN201
    module, rows, images, _, creations = retention
    state = history(tmp_path, [D, A, B])
    module.finalize('lumina', state)
    module.finalize('lumina', state)
    assert json.loads(state.read_text())['images'] == [D, A, B]
    assert len(creations) == 2 and len(rows) == 3
    assert images == {D, A, B}
    assert all(module.safe_pin(row) for name, row in rows.items() if name != 'lumina')


@pytest.mark.parametrize('failure', ['unhealthy', 'missing recovery image', 'unsafe pin', 'uninitialized history'])
def test_failed_preconditions_preserve_images_pins_and_history(retention, tmp_path, failure):  # noqa: ANN001, ANN201
    module, rows, images, commands, creations = retention
    state = history(tmp_path, [A, B, C])
    rows['lumina-a-rollback'] = pin('lumina-a-rollback', A)
    if failure == 'unhealthy':
        rows['lumina']['State']['Health']['Status'] = 'unhealthy'
    elif failure == 'missing recovery image':
        images.remove(B)
    elif failure == 'unsafe pin':
        rows['lumina-a-rollback']['State']['Running'] = True
    else:
        state.unlink()
    before = state.read_bytes() if state.exists() else None
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.finalize('lumina', state)
    assert (state.read_bytes() if state.exists() else None) == before
    assert not creations and 'lumina-a-rollback' in rows
    assert not any(command[:2] in [('container', 'rm'), ('image', 'prune')] for command in commands)


def test_first_install_builds_history_without_guessing_from_tags(retention, tmp_path):  # noqa: ANN001, ANN201
    module, rows, images, _, _ = retention
    state = tmp_path / 'known-good.json'
    images.clear()
    for image in [A, B, C, D]:
        images.add(image)
        rows['lumina']['Image'] = image
        module.finalize('lumina', state)
    assert json.loads(state.read_text())['images'] == [D, C, B]
    assert images == {D, C, B}
    assert len(rows) == 3
