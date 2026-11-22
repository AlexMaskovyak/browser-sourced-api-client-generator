"""Build, install and run generated wheels without importing their source tree."""

import json
from pathlib import Path
import subprocess
import sys
import zipfile

from support.workflow import FIXTURES
from website_client.autonomous import _profile_id
from website_client.discovery_manifest import write_manifest
from website_client.discovery_manifest.records import Artifact, Kind, Manifest, Source
from website_client.runtime_profile import ProfileStore, service_origin

def supplied_bundle(tmp_path, url):
    workflow, definition = tmp_path / 'workflow.json', tmp_path / 'openapi.json'
    workflow.write_bytes((FIXTURES / 'workflow.json').read_bytes())
    spec = json.loads((FIXTURES / 'openapi.json').read_text())
    spec['servers'] = [{'url': url}]
    definition.write_text(json.dumps(spec))
    return workflow


def runtime_association(tmp_path, url):
    identifier = _profile_id(url)
    with ProfileStore('neutral', url).locked() as store:
        old = store.load()
    with ProfileStore(identifier, url).locked() as store:
        store.save(old['storage_state'], old['headers'])
    item = Artifact.create('runtime.json', Kind.REPORT, json.dumps({'runtimeProfiles': {service_origin(url): identifier}}), Source.REPORT)
    path = tmp_path / 'association.json'
    write_manifest(Manifest.create((item,), ()), path)
    return path


def installed_target(project, tmp_path):
    build = subprocess.run([sys.executable, '-c', 'from poetry.core.masonry.api import build_wheel; build_wheel("dist")'], cwd=project, capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stderr
    wheel = next((project / 'dist').glob('*.whl'))
    companion = project / 'arazzo_runner-0.9.6+websiteclient.1-py3-none-any.whl'
    sibling = wheel.parent / companion.name
    sibling.write_bytes(companion.read_bytes())
    target = tmp_path / 'installed'
    install = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--target', str(target), str(wheel), str(sibling)], capture_output=True, text=True, timeout=60)
    assert install.returncode == 0, install.stderr
    with zipfile.ZipFile(wheel) as package:
        metadata = package.read(next(name for name in package.namelist() if name.endswith('/METADATA'))).decode()
        assert 'arazzo-runner (==0.9.6+websiteclient.1)' in metadata
        assert 'file://' not in metadata
        assert any(name.endswith('/_workflows.json') for name in package.namelist())
        assert any('/definitions/' in name for name in package.namelist())
    return target


def installed_cli(target, name, arguments, tmp_path, *, stdout=subprocess.PIPE):
    # Launch pip's actual installed entry script in a fresh isolated interpreter.
    # Dependencies come from this test environment; clean containers are M4 acceptance.
    bootstrap = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name="__main__")'
    return subprocess.run([sys.executable, '-I', '-c', bootstrap, str(target), str(target / 'bin' / name), *arguments], cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE, text=True, timeout=40)


