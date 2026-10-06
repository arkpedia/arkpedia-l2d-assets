#!/usr/bin/env python3
"""Sync Global dynamic illustrations (animated outfit art) into this repository.

1. Reads the EN skin_table (ArknightsAssets/ArknightsGamedata, master) and the Global client's
   network config -> version file -> resVersion -> hot_update_list.json.
2. Picks every skin with a dynIllustId whose bundle the list carries.
3. Downloads only bundles that have no folder yet (GET, one at a time, with a pause), checks
   their size and md5 against the list, decodes the skeleton, atlas and atlas pages, and writes
   models/<slug>/<md5_12>/ (skeleton.skel|json, skeleton.atlas, page<N>.webp, model.json).
4. Reads each new skeleton with the vendored Spine 3.8 runtime (scripts/inspect-skeleton.mjs)
   for its animations and bounds, then points manifest.json at the new folder.

A model that fails is skipped and reported; the manifest only ever names complete folders.
Folders are never deleted or rewritten.

Usage:
  python scripts/sync.py                     # everything new
  python scripts/sync.py --limit 10          # at most 10 downloads this run
  python scripts/sync.py --only 'char_1044_hsgma2#2' --only 'char_1012_skadi2@iteration#2'
  python scripts/sync.py --dry-run           # plan only, no downloads or writes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import l2d  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKIN_TABLE_URL = 'https://raw.githubusercontent.com/ArknightsAssets/ArknightsGamedata/master/en/gamedata/excel/skin_table.json'
NETWORK_CONFIG_URL = 'https://ak-conf.arknights.global/config/prod/official/network_config'
PLATFORM = 'Android'
SERVER = 'en'
USER_AGENT = 'arkpedia-l2d-assets-sync (+https://github.com/arkpedia/arkpedia-l2d-assets)'


def log(message: str) -> None:
    print(message, flush=True)


def get(url: str, *, attempts: int = 3, timeout: int = 180) -> bytes:
    """GET with a few retries. Nothing in this script sends anything but GET."""
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts or (isinstance(error, urllib.error.HTTPError) and error.code in (403, 404)):
                raise
            log(f'  retry {attempt}/{attempts - 1} after {error}')
            time.sleep(delay)
            delay *= 3
    raise AssertionError('unreachable')


def get_json(url: str):
    return json.loads(get(url).decode('utf-8'))


def client_list() -> tuple[str, str, dict]:
    """(asset base URL, resVersion, hot_update_list) of the Global Android client."""
    config = get_json(NETWORK_CONFIG_URL)
    content = json.loads(config['content']) if isinstance(config.get('content'), str) else config['content']
    network = content['configs'][content['funcVer']]['network']
    version = get_json(network['hv'].replace('{0}', PLATFORM))
    res_version = version['resVersion']
    asset_base = network['hu']
    hot_update_list = get_json(f'{asset_base.rstrip("/")}/{PLATFORM}/assets/{res_version}/hot_update_list.json')
    return asset_base, res_version, hot_update_list


def read_manifest() -> dict:
    path = ROOT / 'manifest.json'
    if not path.exists():
        return {'schemaVersion': 1, 'server': SERVER, 'resVersion': None, 'models': {}}
    manifest = json.loads(path.read_text('utf-8'))
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('models'), dict):
        raise SystemExit('manifest.json is not schemaVersion 1')
    return manifest


def write_json(path: Path, value) -> None:
    """Writes JSON through a temporary file so a crash never leaves half a file."""
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', 'utf-8')
    os.replace(tmp, path)


def write_manifest(manifest: dict) -> None:
    manifest['models'] = dict(sorted(manifest['models'].items()))
    write_json(ROOT / 'manifest.json', manifest)


def file_record(folder: Path, name: str, **extra) -> dict:
    data = (folder / name).read_bytes()
    return {'file': name, **extra, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def inspect(folder: Path) -> dict:
    result = subprocess.run(['node', str(ROOT / 'scripts' / 'inspect-skeleton.mjs'), str(folder)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise l2d.SyncError('Spine 3.8 runtime could not read the skeleton: ' + ' | '.join(tail))
    return json.loads(result.stdout)


def build_model(planned: l2d.Planned, dat: bytes, res_version: str, staging: Path) -> dict:
    """Decodes one bundle into `staging` and returns its model.json content."""
    bundle = l2d.unpack_dat(dat, planned)
    decoded = l2d.decode_bundle(bundle, planned.dyn_illust_id)
    json_skeleton = l2d.is_json_skeleton(decoded.skeleton)
    skeleton_file = 'skeleton.json' if json_skeleton else 'skeleton.skel'
    (staging / skeleton_file).write_bytes(decoded.skeleton)
    atlas_text, original_pages = l2d.rewrite_atlas(decoded.atlas_text)
    if original_pages != decoded.page_names:
        raise l2d.SyncError(f'Atlas pages changed while rewriting: {original_pages} vs {decoded.page_names}')
    (staging / 'skeleton.atlas').write_bytes(atlas_text.encode('utf-8'))
    textures = []
    for index, image in enumerate(decoded.pages):
        name = f'page{index}.webp'
        (staging / name).write_bytes(l2d.encode_webp(image))
        textures.append(file_record(staging, name, width=image.width, height=image.height))

    found = inspect(staging)
    expected_pages = [f'page{i}.webp' for i in range(len(decoded.pages))]
    if found['pages'] != expected_pages:
        raise l2d.SyncError(f'Runtime sees atlas pages {found["pages"]}, expected {expected_pages}')
    declared = l2d.spine_version(decoded.skeleton)
    if found['spineVersion'] != declared:
        raise l2d.SyncError(f'Runtime read version {found["spineVersion"]}, file declares {declared}')

    model = {
        'schemaVersion': 1,
        'skinId': planned.skin_id,
        'dynIllustId': planned.dyn_illust_id,
        'spineVersion': declared,
        'skeleton': file_record(staging, skeleton_file, format='json' if json_skeleton else 'binary'),
        'atlas': file_record(staging, 'skeleton.atlas'),
        'textures': textures,
        'premultipliedAlpha': True,
        'animations': found['animations'],
        'bounds': found['bounds'],
        'mixes': decoded.mixes,
        'source': {'server': SERVER, 'bundle': planned.bundle, 'md5': planned.md5, 'resVersion': res_version},
    }
    write_json(staging / 'model.json', model)
    if decoded.masked_pages:
        log(f'  note: pages with a separate [alpha] mask: {decoded.masked_pages}')
    log(f'  skeleton {decoded.skeleton_name}, atlas {decoded.atlas_name}, pages {decoded.page_names}')
    return model


def existing_model(planned: l2d.Planned) -> dict | None:
    path = ROOT / planned.folder / 'model.json'
    if not path.exists():
        return None
    model = json.loads(path.read_text('utf-8'))
    if model.get('skinId') != planned.skin_id or model.get('source', {}).get('md5') != planned.md5:
        raise l2d.SyncError(f'{planned.folder} exists but holds a different model')
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', action='append', default=[], help='skinId to sync (repeatable, or comma-separated)')
    parser.add_argument('--limit', type=int, default=0, help='download at most this many bundles (0 = no limit)')
    parser.add_argument('--pause', type=float, default=3.0, help='seconds between downloads')
    parser.add_argument('--dry-run', action='store_true', help='plan only: no downloads, no writes')
    parser.add_argument('--report', default=str(ROOT / '.cache' / 'sync-report.json'), help='where to write the run report')
    args = parser.parse_args(argv)
    only = {s.strip() for value in args.only for s in value.split(',') if s.strip()}

    manifest = read_manifest()
    log('Reading the EN skin table and the Global client list')
    skin_table = get_json(SKIN_TABLE_URL)
    asset_base, res_version, hot_update_list = client_list()
    plan = l2d.plan_models(skin_table, hot_update_list)
    log(f'resVersion {res_version}: {len(plan.models)} skins with dynamic art listed, {len(plan.unlisted)} not in the client list')

    models = plan.models
    if only:
        unknown = only - {m.skin_id for m in models}
        if unknown:
            log(f'Not found or not listed: {sorted(unknown)}')
        models = [m for m in models if m.skin_id in only]

    report = {'resVersion': res_version, 'added': [], 'repointed': [], 'current': 0, 'failed': [],
              'unlisted': plan.unlisted, 'deferred': [], 'downloadedBytes': 0}
    pending = []
    for planned in models:
        try:
            model = existing_model(planned)
        except l2d.SyncError as error:
            report['failed'].append({'skinId': planned.skin_id, 'error': str(error)})
            continue
        target = f'{planned.folder}/model.json'
        if model is None:
            pending.append(planned)
        elif manifest['models'].get(planned.skin_id) != target:
            report['repointed'].append(planned.skin_id)
            if not args.dry_run:
                manifest['models'][planned.skin_id] = target
        else:
            report['current'] += 1
    if args.limit and len(pending) > args.limit:
        report['deferred'] = [p.skin_id for p in pending[args.limit:]]
        pending = pending[:args.limit]
    need = sum(p.total_size for p in pending)
    log(f'{report["current"]} current, {len(pending)} to download ({need / 1e6:.1f} MB), {len(report["deferred"])} deferred by --limit')

    if args.dry_run:
        for planned in pending:
            log(f'  would fetch {planned.skin_id}: {l2d.download_url(asset_base, PLATFORM, res_version, planned.bundle)}')
        print(json.dumps(report, indent=2))
        return 0

    staging_root = ROOT / '.cache' / 'staging'
    staging_root.mkdir(parents=True, exist_ok=True)
    for index, planned in enumerate(pending):
        if index:
            time.sleep(args.pause)
        url = l2d.download_url(asset_base, PLATFORM, res_version, planned.bundle)
        log(f'[{index + 1}/{len(pending)}] {planned.skin_id} <- {url}')
        staging = Path(tempfile.mkdtemp(prefix=f'{l2d.slug_for(planned.skin_id)}-', dir=staging_root))
        try:
            dat = get(url)
            report['downloadedBytes'] += len(dat)
            build_model(planned, dat, res_version, staging)
            final = ROOT / planned.folder
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                raise l2d.SyncError(f'{planned.folder} already exists')
            os.replace(staging, final)
            manifest['models'][planned.skin_id] = f'{planned.folder}/model.json'
            manifest['resVersion'] = res_version
            write_manifest(manifest)
            report['added'].append(planned.skin_id)
            log(f'  wrote {planned.folder}')
        except Exception as error:  # noqa: BLE001 - one model never stops the run
            report['failed'].append({'skinId': planned.skin_id, 'error': f'{type(error).__name__}: {error}'})
            log(f'  FAILED {planned.skin_id}: {error}')
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    manifest['resVersion'] = res_version
    write_manifest(manifest)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n', 'utf-8')
    log(f'Added {len(report["added"])}, re-pointed {len(report["repointed"])}, current {report["current"]}, '
        f'failed {len(report["failed"])}, downloaded {report["downloadedBytes"] / 1e6:.1f} MB')
    for failure in report['failed']:
        log(f'  failed: {failure["skinId"]}: {failure["error"]}')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as out:
            out.write(f'### Dynamic art sync ({res_version})\n\n')
            out.write(f'- Added: {len(report["added"])} {", ".join(report["added"])}\n')
            out.write(f'- Already current: {report["current"]}\n')
            out.write(f'- Deferred by limit: {len(report["deferred"])}\n')
            out.write(f'- Not in the client list: {", ".join(report["unlisted"]) or "none"}\n')
            out.write(f'- Downloaded: {report["downloadedBytes"] / 1e6:.1f} MB\n')
            for failure in report['failed']:
                out.write(f'- **Failed** `{failure["skinId"]}`: {failure["error"]}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
