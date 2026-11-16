"""Rebuild the declared native dependency; never patch an installed environment.

Prerequisites: Python 3.12+, system `patch`, and pdm-backend==2.5.0 in a build
interpreter. From the repository root, for example:

  python3 -m venv work/native-builder
  work/native-builder/bin/python -m pip install pdm-backend==2.5.0
  work/native-builder/bin/python src/website_client/native_dependencies/rebuild.py

These setup commands create and populate an isolated build interpreter. This script downloads the
hash-pinned upstream source, applies the checked-in native patch in temporary
storage, and writes the companion wheel and provenance beside this script.
It does not modify installed packages. A hash/build failure stops without claiming
a usable replacement. --output selects a separate existing destination directory.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

RESOURCES = Path(__file__).resolve().parent
BUILD_VERSION = '2.5.0'
VERSION = '0.9.6+websiteclient.1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=RESOURCES)
    parser.add_argument('--source', type=Path, help='Read an already downloaded exact source archive instead of downloading it.')
    options = parser.parse_args()
    if importlib.metadata.version('pdm-backend') != BUILD_VERSION:
        raise SystemExit('Use the declared pdm-backend build version; see --help.')
    output = options.output.resolve()
    if not output.is_dir():
        raise SystemExit('The output directory must already exist.')
    source = json.loads((RESOURCES / 'source.json').read_text())
    with tempfile.TemporaryDirectory(prefix='website-client-native-build-') as temporary:
        root = Path(temporary)
        archive = root / source['filename']
        if options.source:
            archive.write_bytes(options.source.read_bytes())
        else:
            with urllib.request.urlopen(source['url'], timeout=30) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise SystemExit('Unexpected source archive size.')
            archive.write_bytes(raw)
        if digest(archive) != source['sha256']:
            raise SystemExit('Upstream source SHA-256 mismatch; build stopped.')
        with tarfile.open(archive) as package:
            package.extractall(root, filter='data')
        project = root / 'arazzo_runner-0.9.6'
        patched = subprocess.run(['patch', '--batch', '-p1', '-i', str(RESOURCES / 'upstream-json-null.patch')],
                                 cwd=project, capture_output=True)
        if patched.returncode:
            raise SystemExit('Native patch did not apply; build stopped.')
        os.environ['SOURCE_DATE_EPOCH'] = '1767225600'
        previous = Path.cwd()
        try:
            os.chdir(project)
            from pdm.backend import build_wheel
            wheel_name = build_wheel(str(output))
        finally:
            os.chdir(previous)
        wheel = output / wheel_name
        provenance = {'name': 'arazzo-runner', 'version': VERSION, 'upstreamVersion': '0.9.6',
                      'source': source, 'patchSha256': digest(RESOURCES / 'upstream-json-null.patch'),
                      'buildBackend': {'name': 'pdm-backend', 'version': BUILD_VERSION},
                      'wheel': {'filename': wheel.name, 'sha256': digest(wheel)},
                      'modifiedFiles': ['arazzo_runner/http.py', 'arazzo_runner/executor/parameter_processor.py', 'pyproject.toml', 'NOTICE']}
        (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
        print(json.dumps(provenance))


if __name__ == '__main__':
    main()
