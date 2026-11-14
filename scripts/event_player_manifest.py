"""Hash and validate reviewed browser packs independently of a GPU/runtime."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def mp3_duration(data):
    """Sum MPEG-1 Layer III frames (our fixed, reviewed encoding)."""
    offset = 0
    if data[:3] == b'ID3':
        offset = 10 + sum((data[6 + i] & 127) << (7 * (3 - i)) for i in range(4))
    seconds = 0
    rates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    while offset + 4 <= len(data):
        header = int.from_bytes(data[offset:offset + 4], 'big')
        assert header >> 21 == 0x7ff and (header >> 17) & 15 == 13, 'Invalid MPEG-1 Layer III frame'
        bitrate, rate = (header >> 12) & 15, (header >> 10) & 3
        assert 0 < bitrate < 15 and rate < 3
        sample_rate = [44100, 48000, 32000][rate]
        size = 144000 * rates[bitrate] // sample_rate + ((header >> 9) & 1)
        assert offset + size <= len(data), 'Truncated MP3 frame'
        seconds += 1152 / sample_rate
        offset += size
    assert offset == len(data) and seconds > 0
    return seconds


def seal(root, folders, renderer):
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'schemaVersion': 1, 'packs': []}
    old = {p['folder']: p for p in manifest['packs']}
    renderer_hash = sha(renderer.read_bytes())
    for relative in folders:
        folder = root / relative
        scene = json.loads((folder / 'scene.json').read_text())
        files = [{'file': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())} for p in sorted(folder.iterdir()) if p.is_file()]
        old[relative] = {'folder': relative, 'reviewed': True, 'source': scene['source'], 'clip': scene['clip'],
                         'rendererSchemaVersion': 1, 'rendererSourceSha256': renderer_hash,
                         'poster': {'file': 'menu.webp', 'time': scene['duration'], 'width': 1280, 'height': 720},
                         'notRendered': ['Game-only interaction/text controls', 'ParticleSystem emitters', 'Non-UI mesh effects'], 'files': files}
    manifest['packs'] = sorted(old.values(), key=lambda p: p['folder'])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


def validate(root):
    from PIL import Image
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['schemaVersion'] == 1
    seen = set()
    for pack in manifest['packs']:
        folder = (root / pack['folder']).resolve()
        assert folder.is_relative_to(root.resolve()) and pack['reviewed'] is True
        assert folder not in seen
        seen.add(folder)
        listed = set()
        for f in pack['files']:
            file = (folder / f['file']).resolve()
            assert file.is_relative_to(folder) and file.name == f['file']
            data = file.read_bytes()
            assert len(data) == f['bytes'] and sha(data) == f['sha256'], file
            listed.add(file.name)
        assert listed == {p.name for p in folder.iterdir() if p.is_file()}
        scene = json.loads((folder / 'scene.json').read_text())
        assert scene['schemaVersion'] == pack['rendererSchemaVersion'] == 1
        assert scene['source'] == pack['source'] and scene['clip'] == pack['clip']
        assert 0 < scene['duration'] <= 15 and (scene['width'], scene['height']) == (1280, 720)
        source = json.loads((root.parent / scene['source']).read_text())
        assert any(a['name'] == scene['clip'] and a['candidate'] for a in source['animations'])
        for i, node in enumerate(scene['nodes']):
            assert node['parent'] is None or 0 <= node['parent'] < i
            if 'stencil' in node:
                stencil = node['stencil']
                assert set(stencil) == {'compare', 'operation', 'readMask', 'writeMask', 'colorMask'}
                assert stencil['compare'] in (0, 1, 2) and stencil['operation'] in (0, 1, 2)
                assert all(type(stencil[key]) is int and 0 <= stencil[key] <= 255 for key in ('readMask', 'writeMask'))
                assert type(stencil['colorMask']) is int and 0 <= stencil['colorMask'] <= 15
            if 'euler' in node:
                assert len(node['euler']) == 3
            if 'image' in node:
                im = node['image']
                assert im['texture'] is None or 0 <= im['texture'] < len(scene['textures'])
                assert im['material'] is None or 0 <= im['material'] < len(scene['materials'])
        for curve in scene['curves']:
            assert 0 <= curve['node'] < len(scene['nodes']) and curve['keys']
            assert [k[0] for k in curve['keys']] == sorted(k[0] for k in curve['keys'])
            target = scene['nodes'][curve['node']]
            for part in curve['property'].split('.'):
                target = target[int(part)] if isinstance(target, list) else target[part]
            assert isinstance(target, (int, float, bool)), 'Animated target must be a scalar'
        for texture in scene['textures']:
            with Image.open(folder / texture['file']) as image:
                assert image.size == (texture['width'], texture['height'])
                image.verify()
        for material in scene['materials']:
            assert 0 <= material['shader'] < len(scene['shaders'])
        for shader in scene['shaders']:
            assert shader['vertex'].startswith('#version 300 es') and shader['fragment'].startswith('#version 300 es')
            assert 'void main()' in shader['vertex'] and 'void main()' in shader['fragment']
        if scene.get('sound'):
            sound = scene['sound']
            bank = (root.parent / sound['source']).parent
            asset = json.loads((bank / 'asset.json').read_text())
            waveform = (folder / sound['file']).read_bytes()
            assert any(f['sha256'] == sound['sourceDecodedSha256'] for f in asset['files']), 'Unverified decoded sound source'
            assert sound['encoding'] == 'mp3-192k' and abs(mp3_duration(waveform) - sound['duration']) < 0.15
            assert 0 <= sound['offset'] < scene['duration']
        with Image.open(folder / pack['poster']['file']) as image:
            assert image.size == (pack['poster']['width'], pack['poster']['height'])
            image.verify()
    assert seen == {p.parent.resolve() for p in root.rglob('scene.json')}, 'Unreviewed or unindexed player pack'
    print(f'Validated {len(seen)} reviewed browser packs and their original source/audio references')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['seal', 'validate'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] / 'event-transitions/players')
    parser.add_argument('--renderer', type=Path)
    parser.add_argument('--reviewed', action='append', default=[])
    args = parser.parse_args()
    if args.command == 'seal':
        if not args.renderer or not args.reviewed:
            parser.error('sealing requires a renderer and explicitly reviewed folders')
        seal(args.root, args.reviewed, args.renderer)
    validate(args.root)


if __name__ == '__main__':
    main()
