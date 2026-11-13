#!/usr/bin/env python3
"""Recover original event UI entrances; this archives sources, it is not a player.

python scripts/event_transitions.py recover [--workers 3] [--only act42side]
python scripts/event_transitions.py validate
The reviewed manifest URLs in event-transitions/recovery-sources.json make old
CDN versions discoverable without guessing filenames or resource versions.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import gzip
import hashlib
import json
import math
import re
import tempfile
from collections import Counter
from pathlib import Path

import l2d
import sync

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'event-transitions'
CACHE = ROOT / '.cache' / 'event-transitions'
SCHEMA = 1
# A candidate is evidence for inspection, never a claim that a sequence plays.
ENTRANCE = re.compile(r'(entry|enter|entrance|transition|(?:^|_)trans(?:_|$)|(?:^|_)(?:start|open|in)(?:_|$))', re.I)


def read_json(path):
    path = Path(path)
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_text('utf-8'))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if Path(path).suffix == '.gz':
        Path(path).write_bytes(gzip.compress(text.encode('utf-8'), mtime=0))
    else:
        Path(path).write_text(text, 'utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def portable(value, key=''):
    """Preserve Unity int64 pointers and non-JSON values without JS rounding."""
    if isinstance(value, dict):
        return {k: portable(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [portable(v) for v in value]
    if isinstance(value, bytes):
        return {'$bytesBase64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, float) and not math.isfinite(value):
        return {'$float': str(value)}
    if isinstance(value, int) and (key == 'm_PathID' or abs(value) > 2**53 - 1):
        return str(value)
    return value


def candidate(name):
    return bool(ENTRANCE.search(name)) and not re.search(r'loop|idle|exit|leave|outro|close|(?:^|_)out(?:_|$)', name, re.I)


def animation_info(obj, data):
    times = []
    for field in ('m_FloatCurves', 'm_PositionCurves', 'm_RotationCurves', 'm_EulerCurves', 'm_ScaleCurves'):
        for curve in data.get(field, []):
            times.extend(k['time'] for k in curve.get('curve', {}).get('m_Curve', []))
    # Non-legacy clips can store their animation in muscle/streamed buffers.
    times.append((data.get('m_MuscleClip') or {}).get('m_StopTime', 0))
    return {'id': str(getattr(obj, 'path_id', obj)), 'name': data.get('m_Name', ''),
            'candidate': candidate(data.get('m_Name', '')),
            'durationSeconds': max(times, default=0), 'sampleRate': data.get('m_SampleRate'),
            'events': portable(data.get('m_Events', []))}


def refresh_inventory(folder):
    asset = read_json(folder / 'asset.json')
    clips = read_json(folder / 'animations.json.gz')['clips']
    inventory = [animation_info(c['id'], c['data']) for c in clips]
    if asset['animations'] != inventory:
        asset['animations'] = inventory
        write_json(folder / 'asset.json', asset)
    return asset


def manifest_sources():
    return read_json(OUT / 'recovery-sources.json')['sources']


def load_manifest(source):
    url = source['manifestUrl']
    path = CACHE / 'manifests' / (digest(url.encode()) + '.json')
    if path.exists():
        data = read_json(path)
    else:
        data = sync.get_json(url)
        write_json(path, data)
    if data.get('versionId') != source['resVersion']:
        raise ValueError(f'{url}: expected {source["resVersion"]}, got {data.get("versionId")}')
    return data


def discover():
    latest = {}
    errors = []
    sources = manifest_sources()
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        futures = {pool.submit(load_manifest, s): s for s in sources}
        for future in concurrent.futures.as_completed(futures):
            s = futures[future]
            try:
                data = future.result()
                # An upstream test upload is retained as evidence, not a release.
                if not re.match(r'^\d{2}-\d{2}-\d{2}-', s['resVersion']):
                    continue
                for info in data['abInfos']:
                    if (not info['name'].startswith('activity/') or not info['name'].endswith('.ab')
                            or info['name'] == 'activity/commonassets.ab'):
                        continue
                    key = s['server'], info['name']
                    if key not in latest or s['resVersion'] > latest[key]['source']['resVersion']:
                        latest[key] = {'info': info, 'source': s}
            except Exception as error:
                errors.append({'source': s, 'error': str(error)})
    print(f'Discovered {len(latest)} activity bundles from {len(sources)} manifests; {len(errors)} manifest errors', flush=True)
    return list(latest.values()), errors


def download(plan):
    info, source = plan['info'], plan['source']
    record = l2d.SharedBundle(info['name'], info['md5'], info.get('totalSize', 0), info.get('abSize', 0))
    url = l2d.download_url(source['assetBase'], 'Android', source['resVersion'], info['name'])
    path = CACHE / 'bundles' / (info['md5'] + '.dat')
    if path.exists():
        data = path.read_bytes()
    else:
        data = sync.get(url, attempts=1, timeout=45)
        # Verify before caching, including the uncompressed bundle's published MD5.
        l2d.unpack_dat(data, record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    bundle = l2d.unpack_dat(data, record)
    return data, bundle, url


def recover_bundle(plan):
    """A worker owns its UnityPy environment and atomically publishes one folder."""
    info, source = plan['info'], plan['source']
    activity = info['name'].split('/')[-1].removeprefix('[uc]').removesuffix('.ab')
    shared = plan.get('role') == 'shared'
    result = {'server': source['server'], 'activityId': activity, 'bundle': info['name'], 'md5': info['md5']}
    folder = OUT / 'shared' / info['md5'] if shared else OUT / 'events' / source['server'] / activity / info['md5'][:12]
    try:
        if (folder / 'asset.json').exists():
            asset = refresh_inventory(folder)
            if asset['source']['bundleMd5'] != info['md5']:
                raise ValueError(f'{folder}: bundle MD5 prefix collision')
            if not asset['source'].get('manifestEntry'):
                original_manifest = load_manifest(asset['source'])
                asset['source']['manifestEntry'] = next(i for i in original_manifest['abInfos']
                    if i['name'] == asset['source']['bundle'] and i['md5'] == info['md5'])
                write_json(folder / 'asset.json', asset)
            return {**result, 'status': 'recovered', 'asset': str((folder / 'asset.json').relative_to(OUT)),
                    'candidates': [a['name'] for a in asset['animations'] if a['candidate']]}
        dat, bundle, url = download(plan)
        env = l2d._patch_unitypy().load(bundle)
        animations, decode_errors = [], []
        for obj in env.objects:
            if obj.type.name != 'AnimationClip':
                continue
            try:
                animations.append(animation_info(obj, obj.read_typetree()))
            except Exception as error:
                decode_errors.append({'id': str(obj.path_id), 'type': obj.type.name, 'error': str(error)})
        candidates = [a for a in animations if a['candidate']]
        if not candidates and not shared:
            return {**result, 'status': 'inspection-failed' if decode_errors else 'no-candidate-clip', 'animations': [a['name'] for a in animations], 'decodeErrors': decode_errors}
        OUT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='entrance-', dir=CACHE) as temp:
            staging = Path(temp)
            (staging / 'bundle.dat').write_bytes(dat)
            objects, textures, clips, containers, externals = [], [], [], [], {}
            type_counts = Counter()
            for obj in env.objects:
                kind = obj.type.name
                type_counts[kind] += 1
                asset_file = obj.assets_file
                file_name = asset_file.name
                externals[file_name] = [e.path for e in asset_file.externals]
                try:
                    tree = obj.read_typetree()
                    if kind == 'AssetBundle':
                        containers.extend({'path': k, 'asset': portable(v['asset'])} for k, v in tree.get('m_Container', []))
                    if kind == 'Texture2D':
                        image = obj.read().image
                        filename = f'textures/{len(textures)}.webp'
                        (staging / filename).parent.mkdir(exist_ok=True)
                        # Keep raw channels, including separate alpha-mask textures.
                        image.save(staging / filename, format='WEBP', lossless=True, exact=True)
                        textures.append({'id': str(obj.path_id), 'file': filename, 'name': tree.get('m_Name'),
                                         'width': image.width, 'height': image.height, 'alpha': 'as-decoded'})
                        tree = {k: v for k, v in tree.items() if k not in ('image data', 'm_PlatformBlob')}
                        tree['$decodedTexture'] = filename
                    if kind == 'AnimationClip':
                        clips.append({'id': str(obj.path_id), 'data': portable(tree)})
                    if kind == 'AudioClip':
                        audio = []
                        for index, (name, raw) in enumerate(obj.read().samples.items()):
                            filename = f'audio/{obj.path_id}-{index}{Path(name).suffix or ".wav"}'
                            (staging / filename).parent.mkdir(exist_ok=True)
                            (staging / filename).write_bytes(raw)
                            audio.append({'name': name, 'file': filename})
                        tree['$decodedAudio'] = audio
                    objects.append({'id': str(obj.path_id), 'file': file_name, 'type': kind, 'data': portable(tree)})
                except Exception as error:
                    # The untouched, verified archive retains bytes even if a decoder cannot.
                    decode_errors.append({'id': str(obj.path_id), 'type': kind, 'error': str(error)})
            write_json(staging / 'animations.json.gz', {'schemaVersion': SCHEMA, 'clips': clips})
            scene = {'schemaVersion': SCHEMA, 'containers': containers, 'externals': externals, 'objects': objects}
            (staging / 'scene.json.gz').write_bytes(gzip.compress(json.dumps(scene, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode(), mtime=0))
            files = []
            for path in sorted(staging.rglob('*')):
                if path.is_file():
                    raw = path.read_bytes()
                    files.append({'file': str(path.relative_to(staging)), 'bytes': len(raw), 'sha256': digest(raw)})
            asset = {'schemaVersion': SCHEMA, 'server': source['server'], 'activityId': activity,
                     'status': 'source-recovered', 'role': 'shared' if shared else 'event', 'browserPlayable': False,
                     'source': {**source, 'bundle': info['name'], 'bundleMd5': info['md5'],
                                'manifestEntry': info,
                                'archiveUrl': url, 'archiveSha256': digest(dat), 'bundleSha256': digest(bundle)},
                     'animations': animations, 'textures': textures, 'objectTypes': dict(sorted(type_counts.items())),
                     'externalFiles': externals, 'decodeErrors': decode_errors, 'files': files}
            write_json(staging / 'asset.json', asset)
            folder.parent.mkdir(parents=True, exist_ok=True)
            # Staging is on the same filesystem; readers never see a partial pack.
            Path(staging).rename(folder)
        return {**result, 'status': 'recovered', 'asset': str((folder / 'asset.json').relative_to(OUT)),
                'candidates': [a['name'] for a in candidates]}
    except Exception as error:
        return {**result, 'status': 'unavailable', 'source': source, 'error': str(error)}


def recover(args):
    CACHE.mkdir(parents=True, exist_ok=True)
    plans, errors = discover()
    if args.only:
        plans = [p for p in plans if any(a in p['info']['name'] for a in args.only)]
    if args.limit:
        plans = plans[:args.limit]
    results = []
    # UnityPy decompression hooks and environments stay isolated between workers.
    with concurrent.futures.ProcessPoolExecutor(args.workers) as pool:
        for index, result in enumerate(pool.map(recover_bundle, plans), 1):
            results.append(result)
            print(f'{index}/{len(plans)} {result["server"]} {result["activityId"]}: {result["status"]}', flush=True)
    report = {'schemaVersion': SCHEMA, 'manifestErrors': errors, 'bundles': sorted(results, key=lambda r: (r['server'], r['activityId']))}
    write_json(CACHE / 'last-recovery.json', report)
    # A limited run must not remove earlier recoveries or their coverage evidence.
    old = read_json(OUT / 'coverage.json') if (OUT / 'coverage.json').exists() else {'bundles': []}
    merged = {(r['server'], r['activityId']): r for r in old['bundles']}
    merged.update({(r['server'], r['activityId']): r for r in results})
    report['bundles'] = sorted(merged.values(), key=lambda r: (r['server'], r['activityId']))
    write_json(OUT / 'coverage.json', report)
    build_index()
    print(dict(Counter(r['status'] for r in report['bundles'])), flush=True)


def build_index():
    index = {'schemaVersion': SCHEMA, 'browserPlayable': False, 'assets': []}
    catalogue = read_json(OUT / 'activity-catalogue.json')['regions']
    for path in sorted((OUT / 'events').rglob('asset.json')):
        a = refresh_inventory(path.parent)
        activity = catalogue.get(a['server'], {}).get('activities', {}).get(a['activityId'], {})
        index['assets'].append({'server': a['server'], 'activityId': a['activityId'],
                                'name': activity.get('name', a['activityId']),
                                'activityType': activity.get('type'), 'isReplicate': activity.get('isReplicate'),
                                'asset': str(path.relative_to(OUT)), 'status': a['status'],
                                'candidates': [c['name'] for c in a['animations'] if c['candidate']]})
    write_json(OUT / 'manifest.json', index)


def validate():
    manifest = read_json(OUT / 'manifest.json')
    assert manifest['schemaVersion'] == SCHEMA and manifest['browserPlayable'] is False
    seen = set()
    shared_manifest = read_json(OUT / 'shared-manifest.json') if (OUT / 'shared-manifest.json').exists() else {'assets': []}
    for entry in manifest['assets'] + shared_manifest['assets']:
        path = (OUT / entry['asset']).resolve()
        assert path.is_relative_to(OUT.resolve()), path
        assert str(path) not in seen, path
        seen.add(str(path))
        asset = read_json(path)
        assert asset['browserPlayable'] is False and asset['status'] == 'source-recovered'
        assert asset['activityId'] == entry['activityId'] and asset['server'] == entry['server']
        if asset.get('role') != 'shared':
            assert entry['candidates'] == [a['name'] for a in asset['animations'] if a['candidate']]
            assert entry['candidates'], path
        for file in asset['files']:
            target = (path.parent / file['file']).resolve()
            assert target.is_relative_to(path.parent), target
            data = target.read_bytes()
            assert len(data) == file['bytes'] and digest(data) == file['sha256'], target
        archive = (path.parent / 'bundle.dat').read_bytes()
        source = asset['source']
        assert digest(archive) == source['archiveSha256'], path
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            assert len(z.namelist()) == 1
            bundle = z.read(z.namelist()[0])
        assert hashlib.md5(bundle).hexdigest() == source['bundleMd5'], path
        assert digest(bundle) == source['bundleSha256'], path
        original = source.get('manifestEntry', {})
        if original.get('totalSize'):
            assert len(archive) == original['totalSize'], path
        if original.get('abSize'):
            assert len(bundle) == original['abSize'], path
        scene = json.loads(gzip.decompress((path.parent / 'scene.json.gz').read_bytes()))
        ids = {o['id'] for o in scene['objects'] if o['type'] == 'AnimationClip'}
        clips = read_json(path.parent / 'animations.json.gz')['clips']
        assert {c['id'] for c in clips} == ids, path
        assert {a['id'] for a in asset['animations'] if a['candidate']} <= ids, path
        import wave
        for obj in scene['objects']:
            if obj['type'] != 'AudioClip':
                continue
            audio = obj['data']
            for sample in audio.get('$decodedAudio', []):
                target = path.parent / sample['file']
                if target.suffix.lower() == '.wav':
                    with wave.open(str(target)) as wav:
                        assert wav.getnframes() > 0, target
                        assert wav.getnchannels() == audio['m_Channels'], target
                        assert wav.getframerate() == audio['m_Frequency'], target
                        assert abs(wav.getnframes() / wav.getframerate() - audio['m_Length']) < 0.1, target
        from PIL import Image
        for t in asset['textures']:
            with Image.open(path.parent / t['file']) as image:
                assert image.size == (t['width'], t['height']), t
                image.verify()
    expected = {str(p.resolve()) for family in ('events', 'shared') for p in (OUT / family).rglob('asset.json')}
    assert seen == expected, 'Unindexed asset folders'
    print(f'Validated {len(seen)} source recoveries, archives, scene/curve records, and all texture hashes')
    if (OUT / 'players/manifest.json').exists():
        from event_player_manifest import validate as validate_players
        validate_players(OUT / 'players')


def recover_shared(args):
    """Preserve version-matched shared shaders/common UI, with explicit CAB links."""
    plans = {}
    cab_bundles = {k.lower(): v for k, v in read_json(ROOT / 'shared-bundles.json')['bundles'].items()}
    for entry in read_json(OUT / 'manifest.json')['assets']:
        asset = read_json(OUT / entry['asset'])
        source = asset['source']
        source = {k: source[k] for k in ('server', 'resVersion', 'assetBase', 'manifestUrl')}
        data = load_manifest(source)
        wanted = {'[uc]uishaders.ab', '[uc]shaders.ab', 'activity/commonassets.ab'}
        # The original run's authored clip names often remain in a rerun bundle.
        # Only request banks whose exact activity prefix is present in this source.
        audio_ids = {asset['activityId']}
        for animation in asset['animations']:
            prefix = re.match(r'^(act[a-z0-9]+)_', animation['name'], re.I)
            if prefix:
                audio_ids.add(prefix[1].lower())
        wanted.update(f'audio/custom_se/{activity}.ab' for activity in audio_ids)
        for files in asset['externalFiles'].values():
            wanted.update(cab_bundles[cab] for ext in files if (cab := ext.split('/')[-1].lower()) in cab_bundles)
        for info in data['abInfos']:
            if info['name'] not in wanted:
                continue
            plans.setdefault(info['md5'], {'info': info, 'source': source, 'role': 'shared'})
    results = []
    with concurrent.futures.ProcessPoolExecutor(args.workers) as pool:
        for index, result in enumerate(pool.map(recover_bundle, plans.values()), 1):
            results.append(result)
            print(f'{index}/{len(plans)} shared {result["bundle"]}: {result["status"]}', flush=True)
    assets = []
    for path in sorted((OUT / 'shared').rglob('asset.json')):
        a = read_json(path)
        assets.append({'server': a['server'], 'activityId': a['activityId'], 'bundle': a['source']['bundle'],
                       'asset': str(path.relative_to(OUT)), 'externalFiles': list(a['externalFiles'])})
    write_json(OUT / 'shared-manifest.json', {'schemaVersion': SCHEMA, 'assets': assets, 'coverage': results})
    dependency_report()


def dependency_report():
    """Link CABs only to bytes named by that event's source manifest."""
    by_cab = {}
    expected_by_manifest = {}
    sounds = []
    for entry in read_json(OUT / 'shared-manifest.json')['assets']:
        shared_asset = read_json(OUT / entry['asset'])
        for cab in entry['externalFiles']:
            by_cab.setdefault(cab.lower(), []).append((entry['asset'], shared_asset['source']['bundleMd5']))
        folder = (OUT / entry['asset']).parent
        scene = json.loads(gzip.decompress((folder / 'scene.json.gz').read_bytes()))
        for obj in scene['objects']:
            if obj['type'] == 'AudioClip':
                d = obj['data']
                sounds.append({'asset': entry['asset'], 'id': obj['id'], 'name': d.get('m_Name'),
                               'durationSeconds': d.get('m_Length'), 'channels': d.get('m_Channels'),
                               'samples': d.get('$decodedAudio', [])})
    packs, cues = [], []
    for entry in read_json(OUT / 'manifest.json')['assets']:
        a = read_json(OUT / entry['asset'])
        manifest_url = a['source']['manifestUrl']
        if manifest_url not in expected_by_manifest:
            expected_by_manifest[manifest_url] = {i['md5'] for i in load_manifest(a['source'])['abInfos'] if i.get('md5')}
        expected = expected_by_manifest[manifest_url]
        dependencies = []
        for local, files in a['externalFiles'].items():
            for file_id, external in enumerate(files, 1):
                cab = external.split('/')[-1].lower()
                dependencies.append({'fromFile': local, 'fileId': file_id, 'external': external,
                                     'recoveredSharedPacks': [pack for pack, md5 in by_cab.get(cab, []) if md5 in expected]})
        packs.append({'asset': entry['asset'], 'dependencies': dependencies})
        for animation in a['animations']:
            if animation['events']:
                cues.append({'asset': entry['asset'], 'clipId': animation['id'], 'clipName': animation['name'],
                             'entranceCandidate': animation['candidate'], 'events': animation['events']})
    write_json(OUT / 'dependencies.json', {'schemaVersion': SCHEMA, 'note': 'CAB names and bundle MD5s must both match the event source manifest. Missing packs remain unresolved; a resolved reference is not proof of browser playability.', 'packs': packs})
    write_json(OUT / 'audio-catalogue.json', {'schemaVersion': SCHEMA, 'sounds': sounds})
    write_json(OUT / 'animation-cues.json', {'schemaVersion': SCHEMA, 'note': 'Original event data only. Do not execute recovered callback names. Sound/event associations still require verification.', 'clips': cues})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('recover')
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--only', action='append')
    p.add_argument('--limit', type=int, default=0)
    p = sub.add_parser('shared')
    p.add_argument('--workers', type=int, default=3)
    sub.add_parser('validate')
    args = parser.parse_args()
    if args.command == 'recover':
        recover(args)
    elif args.command == 'shared':
        recover_shared(args)
    else:
        validate()


if __name__ == '__main__':
    main()
