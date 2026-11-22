"""The native fix is a versioned sibling artifact, never a runtime source patch."""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import tomllib
import zipfile

ROOT = Path(__file__).parents[2]
RESOURCES = ROOT / 'src' / 'website_client' / 'native_dependencies'


def test_companion_provenance_version_and_license_are_exact():
    provenance = json.loads((RESOURCES / 'provenance.json').read_text())
    wheel = RESOURCES / provenance['wheel']['filename']
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == provenance['wheel']['sha256']
    assert hashlib.sha256((RESOURCES / 'upstream-json-null.patch').read_bytes()).hexdigest() == provenance['patchSha256']
    assert provenance['source']['sha256'] == '5491c9facc1a552ad278657315d527889a63353185f6c93456d8af01180f9102'
    assert importlib.metadata.version('arazzo-runner') == provenance['version'] == '0.9.6+websiteclient.1'
    with zipfile.ZipFile(wheel) as package:
        metadata = package.read(next(name for name in package.namelist() if name.endswith('.dist-info/METADATA'))).decode()
        notice = package.read(next(name for name in package.namelist() if name.endswith('/licenses/NOTICE'))).decode()
        assert 'Version: 0.9.6+websiteclient.1' in metadata
        assert 'file://' not in metadata
        assert 'Modified by browser-sourced-api-client-generator' in notice
        assert any(name.endswith('/licenses/LICENSE') for name in package.namelist())
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert 'arazzo-runner (==0.9.6+websiteclient.1)' in project['project']['dependencies']
    assert project['tool']['poetry']['dependencies']['arazzo-runner']['path'].endswith(wheel.name)
