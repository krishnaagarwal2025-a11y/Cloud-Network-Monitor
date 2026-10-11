#!/usr/bin/env python3
"""Install pinned Grafana OSS locally after explicitly approving its download."""

import hashlib
from pathlib import Path
import platform
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
VERSION = '13.2.3'
URL = 'https://dl.grafana.com/grafana/release/13.2.3/grafana_13.2.3_36482603486_linux_amd64.tar.gz'
# Official download page, retrieved 2026-10-11:
# https://grafana.com/grafana/download/13.2.3?edition=oss&platform=linux
SHA256 = '6107ad27016296aac38e0d7ffa8753ab540b5541ad27e94790f771289d733235'


def main():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('This pinned download is for Linux x86_64 only')
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True)
    target = runtime / f'grafana-{VERSION}'
    if target.exists():
        raise RuntimeError(f'{target} already exists; refusing to overwrite it')
    # Extraction occurs in a new private staging directory, then moves atomically.
    with tempfile.TemporaryDirectory(prefix='grafana-install-', dir=runtime) as temporary:
        temporary = Path(temporary)
        archive = temporary / 'grafana.tar.gz'
        digest = hashlib.sha256()
        print(f'Downloading Grafana OSS {VERSION} from the official release host...', flush=True)
        with urlopen(URL, timeout=60) as response, archive.open('xb') as output:
            print(f'Download size: {response.headers.get("Content-Length", "unknown")} bytes', flush=True)
            while block := response.read(1024 * 1024):
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != SHA256:
            raise RuntimeError('Grafana archive checksum did not match the official SHA256')
        extracted = temporary / 'extracted'
        extracted.mkdir()
        with tarfile.open(archive) as package:
            package.extractall(extracted, filter='data')
        homes = [p.parent.parent for p in extracted.rglob('bin/grafana')]
        if len(homes) != 1:
            raise RuntimeError('Expected exactly one Grafana binary in the archive')
        homes[0].rename(target)
    print(f'Installed: {target}')
    print('No system services or system packages were modified.')


if __name__ == '__main__':
    main()
