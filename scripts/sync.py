#!/usr/bin/env python3
"""Sync the game's dynamic illustrations (animated outfit art) into this repository.

1. Reads each client's skin_table and its network config -> version file -> resVersion ->
   hot_update_list.json: Global (the EN skin_table, ArknightsAssets/ArknightsGamedata), then CN
   (the zh_CN one, Kengxxiao/ArknightsGameData).
2. Picks every skin with a dynIllustId whose bundle the list carries. Global comes first: CN only
   fills the skins Global's plan does not have (outfits CN released first), each from CN's own
   client. The day Global's client carries one, its Global bundle is built into a new folder and the
   manifest moves to it; the CN folder stays, like every folder. model.json's source.server says
   which client a model came from.
3. Downloads only bundles that have no folder yet (GET, one at a time, with a pause), checks
   their size and md5 against the list, decodes the skeleton, atlas and atlas pages, and writes
   models/<slug>/<md5_12>/ (skeleton.skel|json, skeleton.atlas, page<N>.webp, model.json). A skin
   with a dynEntranceId also gets its entrance sequence from the same bundle: entrance.skel|json,
   entrance.atlas, entrance-page<N>.webp and its soundtrack, entrance.mp3. The illustration prefab's
   own mesh layers (backdrops and effects drawn with the skeleton, scripts/layers.py) go into
   layers.json and layer<N>.webp; the client's shared shader bundle ([uc]shaders.ab, fetched once
   per run and md5-checked like the rest) names the shaders their materials use, and its shared FX
   texture bundles (refs/fx/texture/..., listed in shared-bundles.json) hold the noise, dissolve and
   ramp textures of their effects: each is fetched, md5-checked, the first time a layer needs it.
4. Reads each new skeleton with the vendored Spine 3.8 runtime (scripts/inspect-skeleton.mjs)
   for its animations and bounds (and the layers' bounds), then points manifest.json at the new
   folder.

A model that fails is skipped and reported; the manifest only ever names complete folders.
Folders are never deleted. A folder is rewritten in one case only: its model.json has a lower
layersVersion than layers.LAYERS_VERSION (the layers' format grew), and then only its layers
(layers.json, layer<N>.webp and model.json's `layers` and `layersVersion`) are exported again from
its bundle, downloaded and checked again; the skeleton, atlas, pages and entrance stay byte for
byte. Every file ever written stays at the commit that wrote it. A bundle that downloaded and verified but could not be
turned into a model is recorded in sync-failures.json and not fetched again until its md5 or
this code changes (--retry-failed, or naming it with --only, tries it anyway).

Usage:
  python scripts/sync.py                     # everything new
  python scripts/sync.py --limit 10          # at most 10 downloads this run
  python scripts/sync.py --only 'char_1044_hsgma2#2' --only 'char_1012_skadi2@iteration#2'
  python scripts/sync.py --dry-run           # plan only, no downloads or writes
  python scripts/sync.py --retry-failed      # also retry bundles recorded in sync-failures.json
  python scripts/sync.py --bundles DIR       # build from local unpacked bundles (DIR/<slug>.ab), md5-checked
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
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import l2d  # noqa: E402
import layers  # noqa: E402
import particles as particle_export  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLATFORM = 'Android'


@dataclass(frozen=True)
class Client:
    server: str  # model.json's source.server
    name: str
    skin_table_url: str
    network_config_url: str


# In order of precedence: a skin comes from the first client whose plan has it.
CLIENTS = (
    Client('en', 'Global', 'https://raw.githubusercontent.com/ArknightsAssets/ArknightsGamedata/master/en/gamedata/excel/skin_table.json',
           'https://ak-conf.arknights.global/config/prod/official/network_config'),
    Client('cn', 'CN', 'https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/skin_table.json',
           'https://ak-conf.hypergryph.com/config/prod/official/network_config'),
)


@dataclass
class ClientList:
    """One client's list for this run, and the shared bundles read from it."""
    client: Client
    asset_base: str
    res_version: str
    hot_update_list: dict
    shaders: dict | None = None
    shared: object = None

USER_AGENT = 'arkpedia-l2d-assets-sync (+https://github.com/arkpedia/arkpedia-l2d-assets)'
FAILURES_FILE = 'sync-failures.json'
# A change to any of these retries every recorded failure once: the fix may be in them.
CODE_FILES = ['scripts/l2d.py', 'scripts/entrance_camera.py', 'scripts/layers.py', 'scripts/effects.py', 'scripts/particles.py', 'scripts/sync.py', 'scripts/spine.mjs',
              'scripts/layers.mjs', 'scripts/inspect-skeleton.mjs', 'vendor/spine-core-3.8/spine-core.js', 'requirements.txt',
              'shared-bundles.json']
# The client's shared FX texture bundles by CAB name (scripts/shared_bundles.py writes it).
SHARED_BUNDLES_FILE = 'shared-bundles.json'


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


def client_list(client: Client) -> tuple[str, str, dict]:
    """(asset base URL, resVersion, hot_update_list) of the client's Android build."""
    config = get_json(client.network_config_url)
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
        return {'schemaVersion': 1, 'server': CLIENTS[0].server, 'resVersion': None, 'models': {}}
    manifest = json.loads(path.read_text('utf-8'))
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('models'), dict):
        raise SystemExit('manifest.json is not schemaVersion 1')
    return manifest


def write_json(path: Path, value, *, compact: bool = False) -> None:
    """Writes JSON through a temporary file so a crash never leaves half a file. `compact` (layers.json,
    mostly vertex and frame numbers the site downloads) leaves out the indentation."""
    tmp = path.with_name(f'.{path.name}.tmp')
    text = json.dumps(value, separators=(',', ':'), ensure_ascii=False) if compact else json.dumps(value, indent=2, ensure_ascii=False)
    tmp.write_text(text + '\n', 'utf-8')
    os.replace(tmp, path)


def code_version() -> str:
    """First 12 hex of a sha256 over the files that turn a bundle into a model."""
    digest = hashlib.sha256()
    for name in CODE_FILES:
        digest.update(name.encode() + b'\0' + (ROOT / name).read_bytes() + b'\0')
    return digest.hexdigest()[:12]


def read_failures() -> dict:
    path = ROOT / FAILURES_FILE
    if not path.exists():
        return {}
    document = json.loads(path.read_text('utf-8'))
    if document.get('schemaVersion') != 1 or not isinstance(document.get('failures'), dict):
        raise SystemExit(f'{FAILURES_FILE} is not schemaVersion 1')
    return document['failures']


def write_failures(failures: dict) -> None:
    write_json(ROOT / FAILURES_FILE, {'schemaVersion': 1, 'failures': dict(sorted(failures.items()))})


def write_manifest(manifest: dict) -> None:
    manifest['models'] = dict(sorted(manifest['models'].items()))
    write_json(ROOT / 'manifest.json', manifest)


def file_record(folder: Path, name: str, **extra) -> dict:
    data = (folder / name).read_bytes()
    return {'file': name, **extra, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def inspect(folder: Path, name: str) -> dict:
    """The vendored Spine 3.8 runtime's reading of <name>.skel|json + <name>.atlas in `folder`
    ('layers': of layers.json against the illustration skeleton, for its bounds)."""
    result = subprocess.run(['node', str(ROOT / 'scripts' / 'inspect-skeleton.mjs'), str(folder), name],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        # The error's own message, not the stack under it.
        message = next((line for line in lines if not line.lstrip().startswith('at ')), '') or ' | '.join(lines[-3:])
        raise l2d.SyncError(f'Spine 3.8 runtime could not read the {"layers" if name == "layers" else "skeleton"}: {message.strip()}')
    return json.loads(result.stdout)


def write_layers(staging: Path, exported) -> dict:
    """layers.json and layer<N>.webp (lossless, straight alpha as shipped: every layer shader samples its
    texture straight), framed by the Spine runtime, and layerParticles.json when the export has particles
    (written first: layers.json's `particles` records its bytes and sha256). Returns model.json's `layers`
    record."""
    document = exported.document
    particles = exported.particles
    records = document['textures'] + document['effectTextures'] + (particles['textures'] if particles else [])
    if len(records) != len(exported.textures):
        raise l2d.SyncError(f'{len(records)} texture records for {len(exported.textures)} textures')
    for record, image in zip(records, exported.textures):
        (staging / record['file']).write_bytes(l2d.encode_webp(image))
        record.update({k: v for k, v in file_record(staging, record['file']).items() if k in ('bytes', 'sha256')})
    if particles is not None:
        write_json(staging / particle_export.FILE, particles, compact=True)
        document['particles'] = {**file_record(staging, particle_export.FILE), 'version': particle_export.VERSION}
    write_json(staging / 'layers.json', document, compact=True)
    framed = inspect(staging, 'layers')
    document['bounds'] = framed['bounds']
    document['effectBounds'] = framed['effectBounds']
    write_json(staging / 'layers.json', document, compact=True)
    return file_record(staging, 'layers.json')


def record_coverage() -> tuple[int, list[str]]:
    """`node scripts/particle_coverage.mjs <ROOT> --add`: an entry in particle-coverage.json for each folder
    with particles that has none (an existing entry is never changed). Returns its exit code and output lines."""
    result = subprocess.run(['node', str(Path(__file__).resolve().parent / 'particle_coverage.mjs'), str(ROOT), '--add'],
                            capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout + result.stderr).strip().splitlines()


def describe_layers(exported) -> str:
    """One line for the log: what is drawn and what was left out."""
    c, o = exported.counts, exported.document['omitted']
    drawn = f'{c["layers"]} layers ({c["effects"]} with effect shaders, {c["exact"]} approximations with their exact effect; {c["static"]} static, ' \
            f'{c["animated"]} animated, {c["follow"]} on bones, {c["only"]} per animation, {c["states"]} with triggered states), ' \
            f'{len(exported.textures)} textures, {c["parts"]} skeleton part(s)'
    if 'particles' in c:
        drawn += f', {c["particles"]} particle systems in {c["particleRuns"]} runs ({c["particleTextures"]} textures of their own)'
    left = f'left out: {o["particles"]} particle systems, {o["trails"]} trails, {o["skinned"]} skinned, {o["hidden"]} hidden, ' \
           f'{len(o["custom"])} custom shaders, {len(o["externalTexture"])} textures in other bundles, {len(o["other"])} other, ' \
           f'{o["holders"]} shared-bundle effects'
    return f'{drawn}; {left}'


def build_model(planned: l2d.Planned, bundle: bytes, res_version: str, staging: Path, shaders: dict,
                shared=None, *, particles: bool) -> tuple[dict, list[dict], dict | None]:
    """Decodes one verified bundle (l2d.unpack_dat) into `staging`. Returns its model.json content,
    per atlas page how the texture was shipped (separate [alpha] mask or not) and how its alpha
    measured, and the layers' counts. `particles`: export the ParticleSystems too (--particles)."""
    decoded = l2d.decode_bundle(bundle, planned.dyn_illust_id, planned.dyn_entrance_id, shaders, shared, particles=particles)
    skeleton, textures, found, declared = write_skeleton(staging, 'skeleton', 'page', decoded.skeleton,
                                                         decoded.atlas_text, decoded.page_names, decoded.pages)
    if 'Idle' not in found['animations']:
        # The site loops Idle and the bounds are framed from it; an entrance skeleton has only Start.
        raise l2d.SyncError(f'{decoded.skeleton_name} has no Idle animation (it has {sorted(found["animations"])}); '
                            'is it the entrance skeleton?')

    exported = decoded.layers(found['slots']) if decoded.layers is not None else None
    model = {
        'schemaVersion': 1,
        'skinId': planned.skin_id,
        'dynIllustId': planned.dyn_illust_id,
        'spineVersion': declared,
        'skeleton': skeleton,
        'atlas': file_record(staging, 'skeleton.atlas'),
        'textures': textures,
        'premultipliedAlpha': True,
        'animations': found['animations'],
        'bounds': found['bounds'],
        'mixes': decoded.mixes,
        'dynEntranceId': planned.dyn_entrance_id,
        'entrance': build_entrance(staging, decoded.entrance, declared) if planned.dyn_entrance_id else None,
        'layers': write_layers(staging, exported) if exported is not None else None,
        'layersVersion': layers.LAYERS_VERSION,
        'source': {'server': planned.server, 'bundle': planned.bundle, 'md5': planned.md5, 'resVersion': res_version},
    }
    write_json(staging / 'model.json', model)
    log(f'  skeleton {decoded.skeleton_name} (chosen by {decoded.skeleton_choice}), atlas {decoded.atlas_name}')
    for info in decoded.page_info:
        log(f'  page {describe_page(info)}')
    page_info = list(decoded.page_info)
    if decoded.entrance:
        entrance = model['entrance']
        audio = entrance['audio']
        log(f'  entrance {decoded.entrance.skeleton_name}, atlas {decoded.entrance.atlas_name}, '
            f'Start {entrance["animations"]["Start"]}s, soundtrack '
            + (f'{decoded.entrance.audio_name} {audio["duration"]}s ({audio["bytes"] / 1e3:.0f} KB MP3)' if audio else 'none'))
        camera = entrance['camera']
        log('  entrance camera ' + (f'{len(camera["frames"])} keys, view height {min(f[3] for f in camera["frames"])}-'
                                    f'{max(f[3] for f in camera["frames"])}, {len(camera["fades"])} full-screen fade(s)' if camera else 'none named'))
        for info in decoded.entrance.page_info:
            log(f'  entrance page {describe_page(info)}')
        page_info += [{**info, 'entrance': True} for info in decoded.entrance.page_info]
    return model, page_info, report_layers(exported)


def report_layers(exported) -> dict | None:
    """Logs what the layers export drew and left out; returns the counts for the run report."""
    if exported is None:
        log('  layers: none (the bundle has no illustration prefab)')
        return None
    log(f'  layers: {describe_layers(exported)}')
    for info in exported.texture_info:
        log(f'  layer texture {info["name"]} {info["width"]}x{info["height"]} wrap {"/".join(info["wrap"])}: measured {info["measured"]["alpha"]}, drawn straight')
    o = exported.document['omitted']
    return {**exported.counts, 'omitted': {k: (v if isinstance(v, int) else len(v)) for k, v in o.items()}}


LAYER_FILE_PREFIX = 'layer'  # layers.json and layer<N>.webp: what a layers re-export replaces


def relayer_model(planned: l2d.Planned, bundle: bytes, staging: Path, shaders: dict, shared, *, particles: bool) -> tuple[dict, dict | None]:
    """Exports the layers of an existing folder again (its layersVersion is older than LAYERS_VERSION)
    into `staging`: a copy of the folder whose layers.json, layerParticles.json and layer<N>.webp are
    replaced and whose model.json gains the new `layers` record and layersVersion. Everything else is
    copied byte for byte. Returns the new model.json and the layers' counts."""
    final = ROOT / planned.folder
    model = json.loads((final / 'model.json').read_text('utf-8'))
    for path in final.iterdir():
        if path.is_file() and not path.name.startswith(LAYER_FILE_PREFIX) and path.name != 'model.json':
            shutil.copy2(path, staging / path.name)
    decoded = l2d.decode_bundle(bundle, planned.dyn_illust_id, planned.dyn_entrance_id, shaders, shared, particles=particles)
    exported = decoded.layers(inspect(staging, 'skeleton')['slots']) if decoded.layers is not None else None
    rebuilt = {}
    for key, value in model.items():
        if key == 'layersVersion':
            continue
        rebuilt[key] = value
        if key == 'layers':
            rebuilt['layers'] = write_layers(staging, exported) if exported is not None else None
            rebuilt['layersVersion'] = layers.LAYERS_VERSION
    write_json(staging / 'model.json', rebuilt)
    return rebuilt, report_layers(exported)


def load_shared_textures(asset_base: str, res_version: str, hot_update_list: dict, bundles: Path | None, report: dict, pause: float):
    """layers.SharedTextures over shared-bundles.json: each shared FX bundle from .cache/shared/<md5>.ab,
    a local copy in --bundles (<dir>/<download name>.ab or .dat), or one GET (after the run's pause),
    checked against the client list's size and md5. A bundle the list does not carry is None: the
    layers that need it are left out under externalTexture."""
    path = ROOT / SHARED_BUNDLES_FILE
    table = (json.loads(path.read_text('utf-8')) if path.exists() else {}).get('bundles') or {}

    def fetch(name: str):
        info = l2d.shared_bundle(hot_update_list, name)
        if info is None:
            log(f'  shared bundle {name} is not in the client list')
            return None
        cache = ROOT / '.cache' / 'shared' / f'{info.md5}.ab'
        local = l2d.download_name(name)
        if cache.exists():
            data = l2d.verify_bundle(cache.read_bytes(), info)
        elif bundles is not None and (bundles / local.replace('.dat', '.ab')).exists():
            data = l2d.verify_bundle((bundles / local.replace('.dat', '.ab')).read_bytes(), info)
        elif bundles is not None and (bundles / local).exists():
            data = l2d.unpack_dat((bundles / local).read_bytes(), info)
        else:
            time.sleep(pause)
            url = l2d.download_url(asset_base, PLATFORM, res_version, info.bundle)
            log(f'  shared textures <- {url}')
            dat = get(url)
            report['downloadedBytes'] += len(dat)
            data = l2d.unpack_dat(dat, info)
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            cache.write_bytes(data)
        return data

    return layers.SharedTextures(table, fetch, l2d._patch_unitypy())


def write_skeleton(staging: Path, name: str, page_prefix: str, skeleton_bytes: bytes, atlas_text: str,
                   page_names: list[str], pages: list) -> tuple[dict, list[dict], dict, str]:
    """Writes <name>.skel|json, <name>.atlas (pages renamed <page_prefix>N.webp) and the pages, then
    reads them back with the Spine 3.8 runtime. Returns the skeleton record, the texture records,
    what the runtime found and the version the file declares."""
    json_skeleton = l2d.is_json_skeleton(skeleton_bytes)
    skeleton_file = f'{name}.json' if json_skeleton else f'{name}.skel'
    (staging / skeleton_file).write_bytes(skeleton_bytes)
    rewritten, original_pages = l2d.rewrite_atlas(atlas_text, page_prefix)
    if original_pages != page_names:
        raise l2d.SyncError(f'Atlas pages changed while rewriting: {original_pages} vs {page_names}')
    (staging / f'{name}.atlas').write_bytes(rewritten.encode('utf-8'))
    textures = []
    for index, image in enumerate(pages):
        page = f'{page_prefix}{index}.webp'
        (staging / page).write_bytes(l2d.encode_webp(image))
        textures.append(file_record(staging, page, width=image.width, height=image.height))
    found = inspect(staging, name)
    expected_pages = [f'{page_prefix}{i}.webp' for i in range(len(pages))]
    if found['pages'] != expected_pages:
        raise l2d.SyncError(f'Runtime sees atlas pages {found["pages"]}, expected {expected_pages}')
    declared = l2d.spine_version(skeleton_bytes)
    if found['spineVersion'] != declared:
        raise l2d.SyncError(f'Runtime read version {found["spineVersion"]}, file declares {declared}')
    record = file_record(staging, skeleton_file, format='json' if json_skeleton else 'binary')
    return record, textures, found, declared


def build_entrance(staging: Path, entrance: l2d.DecodedEntrance | None, version: str) -> dict:
    """model.json's `entrance`: the sequence the game plays before the illustration (its one
    animation, Start), framed in the illustration's own coordinates, and its soundtrack."""
    if entrance is None:
        raise l2d.SyncError('The skin has an entrance but none was decoded')
    skeleton, textures, found, declared = write_skeleton(staging, 'entrance', 'entrance-page', entrance.skeleton,
                                                         entrance.atlas_text, entrance.page_names, entrance.pages)
    if declared != version:
        raise l2d.SyncError(f'Entrance skeleton is Spine {declared}, the illustration {version}')
    if 'Start' not in found['animations']:
        raise l2d.SyncError(f'{entrance.skeleton_name} has no Start animation (it has {sorted(found["animations"])})')
    # The camera the game plays the entrance through, over the entrance animation's own length.
    camera = entrance.camera(found['animations']['Start'])
    audio = None
    if entrance.audio_wav is not None:
        mp3, duration = l2d.wav_to_mp3(entrance.audio_wav)
        (staging / 'entrance.mp3').write_bytes(mp3)
        audio = file_record(staging, 'entrance.mp3', duration=duration)
    return {
        'skeleton': skeleton,
        'atlas': file_record(staging, 'entrance.atlas'),
        'textures': textures,
        'animations': found['animations'],
        'bounds': found['bounds'],
        'camera': camera,
        'audio': audio,
    }


def describe_page(info: dict) -> str:
    """One line per atlas page for the log and the run summary."""
    how = ('RGB + [alpha] mask, kept as shipped (already premultiplied)' if info['mask']
           else 'RGBA, already premultiplied, kept as shipped' if info['alpha'] == 'premultiplied'
           else 'RGBA, straight, premultiplied here')
    text = (f'{info["page"]}: {how}; transparentColour {info["transparentColour"]}, '
            f'semiColourAboveAlpha {info["semiColourAboveAlpha"]}')
    if info.get('resizedFrom'):
        text += f'; resized from {info["resizedFrom"][0]}x{info["resizedFrom"][1]}'
    return text


def load_shaders(asset_base: str, res_version: str, hot_update_list: dict, bundles: Path | None) -> dict:
    """layers.shader_table of the client's shared shader bundle: from .cache/shared/<md5>.ab, a local
    copy in --bundles, or one GET; always checked against the list's size and md5."""
    info = l2d.shared_bundle(hot_update_list, l2d.SHADER_BUNDLE)
    if info is None:
        raise l2d.SyncError(f'The client list has no {l2d.SHADER_BUNDLE}; layers cannot name their shaders')
    cache = ROOT / '.cache' / 'shared' / f'{info.md5}.ab'
    data = None
    if cache.exists():
        data = l2d.verify_bundle(cache.read_bytes(), info)
    elif bundles is not None and (bundles / l2d.SHADER_BUNDLE).exists():
        data = l2d.verify_bundle((bundles / l2d.SHADER_BUNDLE).read_bytes(), info)
    elif bundles is not None and (bundles / l2d.download_name(l2d.SHADER_BUNDLE)).exists():
        data = l2d.unpack_dat((bundles / l2d.download_name(l2d.SHADER_BUNDLE)).read_bytes(), info)
    else:
        url = l2d.download_url(asset_base, PLATFORM, res_version, info.bundle)
        log(f'Shared shaders <- {url}')
        data = l2d.unpack_dat(get(url), info)
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        cache.write_bytes(data)
    table = layers.shader_table_of_bundle(data, l2d._patch_unitypy())
    if not table:
        raise l2d.SyncError(f'{l2d.SHADER_BUNDLE} holds no readable shader')
    log(f'Shared shaders: {len(table)} from {info.bundle} ({info.md5[:12]})')
    return table


def stamp_manifest(manifest: dict, report: dict) -> None:
    """The client lists the manifest was last brought up to: resVersion is Global's, resVersions every
    client's by server."""
    manifest['resVersion'] = report['resVersion']
    manifest['resVersions'] = report['resVersions']


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
    parser.add_argument('--retry-failed', action='store_true', help=f'also retry bundles recorded in {FAILURES_FILE}')
    parser.add_argument('--report', default=str(ROOT / '.cache' / 'sync-report.json'), help='where to write the run report')
    parser.add_argument('--bundles', type=Path, default=None,
                        help='build from local unpacked bundles (<dir>/<slug>.ab, md5-checked against the list) instead of downloading')
    parser.add_argument('--particles', action=argparse.BooleanOptionalAction, default=True,
                        help='export the particle systems (layerParticles.json), as layersVersion 4 requires (the default); '
                             '--no-particles leaves them out, for trial comparisons only: the validator rejects a folder of layersVersion 4 '
                             'without them, so such a run is never committed')
    args = parser.parse_args(argv)
    only = {s.strip() for value in args.only for s in value.split(',') if s.strip()}

    manifest = read_manifest()
    failures = read_failures()
    recorded = json.dumps(failures, sort_keys=True)
    code = code_version()
    clients: dict[str, ClientList] = {}
    planned_all: list[l2d.Planned] = []
    unlisted: set[str] = set()
    for client in CLIENTS:
        log(f'Reading the {client.name} skin table and client list')
        skin_table = get_json(client.skin_table_url)
        asset_base, res_version, hot_update_list = client_list(client)
        clients[client.server] = ClientList(client, asset_base, res_version, hot_update_list)
        plan = l2d.plan_models(skin_table, hot_update_list, client.server)
        taken = {m.skin_id for m in planned_all}
        fills = [m for m in plan.models if m.skin_id not in taken]
        planned_all += fills
        unlisted |= set(plan.unlisted)
        log(f'{client.name} resVersion {res_version}: {len(plan.models)} skins with dynamic art listed, {len(plan.unlisted)} not in the client list'
            + ('' if client == CLIENTS[0] else f'; {len(fills)} of them not planned from {", ".join(c.name for c in CLIENTS[:CLIENTS.index(client)])}, '
               f'synced from {client.name}'))
    # A skin a later client fills is listed after all.
    unlisted = sorted(unlisted - {m.skin_id for m in planned_all})
    slugs: dict[str, str] = {}
    for planned in planned_all:
        other = slugs.setdefault(l2d.slug_for(planned.skin_id), planned.skin_id)
        if other != planned.skin_id:
            raise l2d.SyncError(f'{planned.skin_id} and {other} share the folder name {l2d.slug_for(planned.skin_id)}')
    primary = clients[CLIENTS[0].server]

    def url_of(planned: l2d.Planned) -> str:
        source = clients[planned.server]
        return l2d.download_url(source.asset_base, PLATFORM, source.res_version, planned.bundle)

    models = planned_all
    if only:
        unknown = only - {m.skin_id for m in models}
        if unknown:
            log(f'Not found or not listed: {sorted(unknown)}')
        models = [m for m in models if m.skin_id in only]

    report = {'resVersion': primary.res_version, 'resVersions': {server: c.res_version for server, c in clients.items()}, 'code': code,
              'added': [], 'relayered': [], 'repointed': [], 'current': 0, 'failed': [], 'knownFailures': [], 'unlisted': unlisted,
              'deferred': [], 'downloadedBytes': 0, 'pages': {}, 'layers': {},
              # Skins synced from a client other than Global's, until Global's has them.
              'otherClients': {m.skin_id: m.server for m in planned_all if m.server != primary.client.server}}
    # Drop records of skins that no longer have dynamic art in any client's list.
    listed = {m.skin_id for m in planned_all}
    for skin_id in [s for s in failures if s not in listed]:
        del failures[skin_id]
    pending = []
    relayer = set()  # skinIds whose existing folder only needs its layers exported again
    for planned in models:
        try:
            model = existing_model(planned)
        except l2d.SyncError as error:
            report['failed'].append({'skinId': planned.skin_id, 'error': str(error)})
            continue
        stale = model is not None and int(model.get('layersVersion') or 1) < layers.LAYERS_VERSION
        if model is None or stale:
            known = l2d.known_failure(failures, planned, code)
            if known and not (args.retry_failed or planned.skin_id in only):
                report['knownFailures'].append({'skinId': planned.skin_id, 'md5': planned.md5, 'error': known.get('error', '')})
            else:
                pending.append(planned)
                if stale:
                    relayer.add(planned.skin_id)
            if model is None:
                continue
        else:
            failures.pop(planned.skin_id, None)  # its folder exists and is current, so any old record is stale
        target = f'{planned.folder}/model.json'
        if manifest['models'].get(planned.skin_id) != target:
            report['repointed'].append(planned.skin_id)
            if not args.dry_run:
                manifest['models'][planned.skin_id] = target
        else:
            report['current'] += 1
    if args.limit and len(pending) > args.limit:
        report['deferred'] = [p.skin_id for p in pending[args.limit:]]
        pending = pending[:args.limit]
    need = sum(p.total_size for p in pending)
    log(f'{report["current"]} current, {len(pending)} to download ({need / 1e6:.1f} MB; {len(relayer & {p.skin_id for p in pending})} of them only to '
        f'export their layers again for layersVersion {layers.LAYERS_VERSION}), {len(report["deferred"])} deferred by --limit, '
        f'{len(report["knownFailures"])} skipped as failed before (same bundle, same code)')

    if args.dry_run:
        for planned in pending:
            log(f'  would fetch {planned.skin_id}{" (layers only)" if planned.skin_id in relayer else ""}: '
                f'{url_of(planned)}')
        print(json.dumps(report, indent=2))
        return 0

    # Each client's own shared bundles: its shader bundle and FX textures carry its own md5s.
    for source in clients.values():
        source.shared = load_shared_textures(source.asset_base, source.res_version, source.hot_update_list, args.bundles, report, args.pause)
    for source in clients.values():
        if not any(p.server == source.client.server for p in pending):
            continue
        try:
            source.shaders = load_shaders(source.asset_base, source.res_version, source.hot_update_list, args.bundles)
        except Exception as error:  # noqa: BLE001 - reported below; nothing is built without it
            # Not recorded as a failure of any model: the bundle may download tomorrow. Nothing is
            # built (or its layers exported again) without the shaders its layers need.
            log(f'{source.client.name} shared shaders unavailable, building nothing from it: {error}')
            for planned in [p for p in pending if p.server == source.client.server]:
                report['failed'].append({'skinId': planned.skin_id, 'error': f'shared shaders unavailable: {error}'})
            pending = [p for p in pending if p.server != source.client.server]

    staging_root = ROOT / '.cache' / 'staging'
    staging_root.mkdir(parents=True, exist_ok=True)
    for index, planned in enumerate(pending):
        local = args.bundles / f'{l2d.slug_for(planned.skin_id)}.ab' if args.bundles else None
        if index and local is None:
            time.sleep(args.pause)
        source = clients[planned.server]
        url = url_of(planned)
        log(f'[{index + 1}/{len(pending)}] {planned.skin_id} <- {local or url}')
        staging = Path(tempfile.mkdtemp(prefix=f'{l2d.slug_for(planned.skin_id)}-', dir=staging_root))
        try:
            # A failed or unverified download (or local copy) is not recorded: it may work tomorrow.
            if local is not None:
                if not local.exists():
                    raise l2d.SyncError(f'{local} not found (--bundles)')
                bundle = l2d.verify_bundle(local.read_bytes(), planned)
            else:
                dat = get(url)
                report['downloadedBytes'] += len(dat)
                bundle = l2d.unpack_dat(dat, planned)
        except Exception as error:  # noqa: BLE001 - one model never stops the run
            report['failed'].append({'skinId': planned.skin_id, 'error': f'{type(error).__name__}: {error}'})
            log(f'  FAILED {planned.skin_id}: {error}')
            shutil.rmtree(staging, ignore_errors=True)
            continue
        try:
            final = ROOT / planned.folder
            if planned.skin_id in relayer:
                # Only the layers change: the rest of the folder is copied over unchanged, and the
                # folder is swapped for the copy in one rename once everything is written.
                _, layer_report = relayer_model(planned, bundle, staging, source.shaders, source.shared, particles=args.particles)
                old = final.with_name(final.name + '.replaced')
                shutil.rmtree(old, ignore_errors=True)
                os.replace(final, old)
                os.replace(staging, final)
                shutil.rmtree(old, ignore_errors=True)
                report['relayered'].append(planned.skin_id)
                if layer_report is not None:
                    report['layers'][planned.skin_id] = layer_report
                failures.pop(planned.skin_id, None)
                log(f'  exported the layers of {planned.folder} again (layersVersion {layers.LAYERS_VERSION})')
                continue
            _, page_info, layer_report = build_model(planned, bundle, source.res_version, staging, source.shaders, source.shared, particles=args.particles)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                raise l2d.SyncError(f'{planned.folder} already exists')
            os.replace(staging, final)
            manifest['models'][planned.skin_id] = f'{planned.folder}/model.json'
            stamp_manifest(manifest, report)
            write_manifest(manifest)
            report['added'].append(planned.skin_id)
            report['pages'][planned.skin_id] = page_info
            if layer_report is not None:
                report['layers'][planned.skin_id] = layer_report
            failures.pop(planned.skin_id, None)
            log(f'  wrote {planned.folder}')
        except Exception as error:  # noqa: BLE001 - one model never stops the run
            message = f'{type(error).__name__}: {error}'
            report['failed'].append({'skinId': planned.skin_id, 'error': message})
            failures[planned.skin_id] = l2d.failure_record(planned, code, source.res_version, message)
            log(f'  FAILED {planned.skin_id}: {error}')
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    for source in clients.values():
        if source.shared.used:
            log(f'{source.client.name} shared texture bundles read: {", ".join(sorted(source.shared.used))}')
    if report['added'] or report['repointed']:
        # A run that changes nothing leaves manifest.json untouched, so it produces no commit.
        stamp_manifest(manifest, report)
        write_manifest(manifest)
    if json.dumps(failures, sort_keys=True) != recorded:
        write_failures(failures)
    status = 0
    if args.particles and (report['added'] or report['relayered']):
        # A folder written with its particles gets an entry in particle-coverage.json when it has none (a new
        # bundle's first export); an entry it has stays, and the validator holds the export to it.
        code, lines = record_coverage()
        report['coverage'] = lines[-1] if lines else ''
        log(f'Particle coverage: {report["coverage"]}')
        if code != 0:
            log('  particle_coverage.mjs --add failed:\n' + '\n'.join(lines))
            status = 1
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n', 'utf-8')
    log(f'Added {len(report["added"])}, layers exported again {len(report["relayered"])}, re-pointed {len(report["repointed"])}, current {report["current"]}, '
        f'failed {len(report["failed"])}, skipped as failed before {len(report["knownFailures"])}, '
        f'downloaded {report["downloadedBytes"] / 1e6:.1f} MB')
    for failure in report['failed']:
        log(f'  failed: {failure["skinId"]}: {failure["error"]}')
    for failure in report['knownFailures']:
        log(f'  failed before, not retried: {failure["skinId"]}: {failure["error"]}')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as out:
            out.write(f'### Dynamic art sync ({", ".join(f"{server} {v}" for server, v in report["resVersions"].items())})\n\n')
            out.write(f'- Added: {len(report["added"])} {", ".join(report["added"])}\n')
            out.write(f'- Layers exported again (layersVersion {layers.LAYERS_VERSION}): {len(report["relayered"])} {", ".join(report["relayered"])}\n')
            out.write(f'- Already current: {report["current"]}\n')
            out.write(f'- Deferred by limit: {len(report["deferred"])}\n')
            out.write(f'- Not in any client list: {", ".join(report["unlisted"]) or "none"}\n')
            out.write(f'- Synced from CN until Global has them: {", ".join(report["otherClients"]) or "none"}\n')
            out.write(f'- Downloaded: {report["downloadedBytes"] / 1e6:.1f} MB\n')
            if 'coverage' in report:
                out.write(f'- Particle coverage: {report["coverage"]}\n')
            for failure in report['failed']:
                out.write(f'- **Failed** `{failure["skinId"]}`: {failure["error"]}\n')
            for failure in report['knownFailures']:
                out.write(f'- Failed before, not retried until its bundle or the code changes: `{failure["skinId"]}`: {failure["error"]}\n')
            if report['layers']:
                out.write('\n#### Layers\n\n')
                for skin_id, counts in report['layers'].items():
                    o = counts['omitted']
                    drawn = f'; {counts["particles"]} particle systems drawn' if 'particles' in counts else ''
                    out.write(f'- `{skin_id}`: {counts["layers"]} drawn ({counts["static"]} static, {counts["animated"]} animated){drawn}; left out '
                              f'{o["custom"]} custom shaders, {o["externalTexture"]} external textures, {o["other"]} other, '
                              f'{o["particles"]} particle systems\n')
            if report['pages']:
                out.write('\n#### Page textures\n\n')
                for skin_id, pages in report['pages'].items():
                    for info in pages:
                        out.write(f'- `{skin_id}` {describe_page(info)}\n')
    return status


if __name__ == '__main__':
    sys.exit(main())
