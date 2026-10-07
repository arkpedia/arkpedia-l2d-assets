"""Pure helpers for the dynamic illustration sync (no network, no files).

scripts/sync.py does the fetching and writing; everything here is unit tested in
test/test_l2d.py. UnityPy and lz4 are imported only when a bundle is decoded, so the
tests need nothing but Pillow.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Callable

import entrance_camera
import layers

DYN_PREFIX = 'dyn_illust_'
BUNDLE_DIR = 'arts/dynchars/'
SKIN_ID_RE = re.compile(r'^[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?#[0-9]+$')
MD5_RE = re.compile(r'^[0-9a-f]{32}$')
ENTRANCE_ID_RE = re.compile(r'^dyn_entrance_[A-Za-z0-9_#]+$')
ATLAS_HEADER_RE = re.compile(r'^\s*(size|format|filter|repeat|pma)\s*:')


class SyncError(Exception):
    """A problem with one model. The sync skips the model and reports it."""


# ---------------------------------------------------------------------------
# Names and paths


def slug_for(skin_id: str) -> str:
    """skinId with '@' and '#' replaced by '_': char_113_cqbw@epoque#7 -> char_113_cqbw_epoque_7."""
    if not isinstance(skin_id, str) or not SKIN_ID_RE.match(skin_id):
        raise SyncError(f'Unexpected skinId: {skin_id!r}')
    return skin_id.replace('@', '_').replace('#', '_')


def folder_for(skin_id: str, md5: str) -> str:
    """models/<slug>/<first 12 hex of the bundle md5>; a folder never changes once written."""
    if not isinstance(md5, str) or not MD5_RE.match(md5):
        raise SyncError(f'Unexpected bundle md5: {md5!r}')
    return f'models/{slug_for(skin_id)}/{md5[:12]}'


def bundle_name_for(dyn_illust_id: str) -> str:
    """dyn_illust_char_113_cqbw_epoque#7 -> arts/dynchars/char_113_cqbw_epoque#7.ab (lower case)."""
    if not dyn_illust_id.startswith(DYN_PREFIX) or len(dyn_illust_id) == len(DYN_PREFIX):
        raise SyncError(f'Unexpected dynIllustId: {dyn_illust_id!r}')
    return f'{BUNDLE_DIR}{dyn_illust_id[len(DYN_PREFIX):].lower()}.ab'


def download_name(bundle_name: str) -> str:
    """The CDN file name of a bundle: '/' -> '_', '#' -> '__', '.ab' -> '.dat'."""
    if not bundle_name.endswith('.ab'):
        raise SyncError(f'Unexpected bundle name: {bundle_name!r}')
    return bundle_name[:-3].replace('/', '_').replace('#', '__') + '.dat'


def download_url(asset_base: str, platform: str, res_version: str, bundle_name: str) -> str:
    """The CDN URL of a bundle; brackets in shared bundle names ('[uc]shaders.ab') are percent-encoded."""
    from urllib.parse import quote

    return f'{asset_base.rstrip("/")}/{platform}/assets/{res_version}/{quote(download_name(bundle_name))}'


# ---------------------------------------------------------------------------
# Choosing what to sync


@dataclass
class Planned:
    skin_id: str
    dyn_illust_id: str
    # The skin's dynEntranceId: its entrance sequence, played before the illustration, which
    # ships in the same bundle (None when the skin has none). Required, never defaulted, so
    # every place that plans a model has to say.
    dyn_entrance_id: str | None
    bundle: str
    md5: str
    total_size: int
    ab_size: int

    @property
    def folder(self) -> str:
        return folder_for(self.skin_id, self.md5)


@dataclass
class Plan:
    models: list[Planned] = field(default_factory=list)
    unlisted: list[str] = field(default_factory=list)  # has a dynIllustId, bundle not in the client list


def plan_models(skin_table: dict, hot_update_list: dict) -> Plan:
    """Every skin with a dynIllustId whose bundle the client's hot_update_list carries."""
    by_name = {}
    for info in hot_update_list.get('abInfos', []):
        name = info.get('name')
        if isinstance(name, str):
            by_name[name.lower()] = info
    plan = Plan()
    slugs: dict[str, str] = {}
    for skin_id, skin in sorted(skin_table.get('charSkins', {}).items()):
        dyn = skin.get('dynIllustId') if isinstance(skin, dict) else None
        if not dyn:
            continue
        bundle = bundle_name_for(dyn)
        info = by_name.get(bundle)
        if info is None:
            plan.unlisted.append(skin_id)
            continue
        slug = slug_for(skin_id)
        if slug in slugs:
            raise SyncError(f'{skin_id} and {slugs[slug]} share the folder name {slug}')
        slugs[slug] = skin_id
        md5 = str(info.get('md5', '')).lower()
        if not MD5_RE.match(md5):
            raise SyncError(f'{bundle}: hot_update_list has no md5')
        entrance = skin.get('dynEntranceId') or None
        if entrance is not None and not ENTRANCE_ID_RE.match(str(entrance)):
            raise SyncError(f'{skin_id}: unexpected dynEntranceId {entrance!r}')
        plan.models.append(Planned(skin_id, dyn, entrance, info['name'], md5,
                                   int(info.get('totalSize') or 0), int(info.get('abSize') or 0)))
    return plan


# The client's shared shader bundle: the shaders the illustration prefabs' layer materials name by
# reference into it (their blend, cull and queue), fetched once per run when anything is built.
SHADER_BUNDLE = '[uc]shaders.ab'


@dataclass
class SharedBundle:
    bundle: str
    md5: str
    total_size: int
    ab_size: int


def shared_bundle(hot_update_list: dict, name: str) -> SharedBundle | None:
    """A shared bundle the client list carries (by name, case-insensitive), or None."""
    for info in hot_update_list.get('abInfos', []):
        if isinstance(info.get('name'), str) and info['name'].lower() == name.lower():
            md5 = str(info.get('md5', '')).lower()
            if not MD5_RE.match(md5):
                raise SyncError(f'{name}: hot_update_list has no md5')
            return SharedBundle(info['name'], md5, int(info.get('totalSize') or 0), int(info.get('abSize') or 0))
    return None


def failure_record(planned: Planned, code: str, res_version: str, error: str) -> dict:
    """What sync-failures.json keeps about a bundle that downloaded and verified but failed to build."""
    return {'md5': planned.md5, 'code': code, 'resVersion': res_version, 'error': error[:500]}


def known_failure(failures: dict, planned: Planned, code: str) -> dict | None:
    """The recorded failure of this exact bundle with this exact code, if there is one.

    A new bundle (md5) or a change to the sync code means it is worth trying again.
    """
    entry = failures.get(planned.skin_id)
    if isinstance(entry, dict) and entry.get('md5') == planned.md5 and entry.get('code') == code:
        return entry
    return None


# ---------------------------------------------------------------------------
# Atlas


def atlas_page_lines(text: str) -> list[int]:
    """Indexes (into text.splitlines()) of the page name lines of a libGDX/Spine 3.8 atlas.

    A page name is the first non-blank line of the file or of a block after a blank line,
    and the next line is a page header field (size/format/filter/repeat/pma).
    """
    lines = text.splitlines()
    pages = []
    expect = True
    for i, raw in enumerate(lines):
        line = raw.lstrip('﻿') if i == 0 else raw
        if not line.strip():
            expect = True
            continue
        if expect:
            if i + 1 < len(lines) and ATLAS_HEADER_RE.match(lines[i + 1]):
                pages.append(i)
            expect = False
    return pages


def atlas_page_names(text: str) -> list[str]:
    lines = text.splitlines()
    return [lines[i].lstrip('﻿').strip() for i in atlas_page_lines(text)]


def rewrite_atlas(text: str, prefix: str) -> tuple[str, list[str]]:
    """Renames the pages to <prefix>0.webp, <prefix>1.webp, ... ('page' for the illustration,
    'entrance-page' for its entrance) and leaves every other byte alone.

    Returns the new text and the original page names, in order.
    """
    pages = atlas_page_lines(text)
    if not pages:
        raise SyncError('Atlas declares no pages')
    lines = text.splitlines(keepends=True)
    names = []
    for n, i in enumerate(pages):
        raw = lines[i]
        body = raw.rstrip('\r\n')
        ending = raw[len(body):]
        bom = '﻿' if body.startswith('﻿') else ''
        names.append(body.lstrip('﻿').strip())
        lines[i] = f'{bom}{prefix}{n}.webp{ending}'
    if len(set(names)) != len(names):
        raise SyncError(f'Atlas repeats a page name: {names}')
    return ''.join(lines), names


def atlas_page_sizes(text: str) -> list[tuple[int, int] | None]:
    """The size: field of each page, when present."""
    lines = text.splitlines()
    sizes = []
    for i in atlas_page_lines(text):
        size = None
        for line in lines[i + 1:i + 6]:
            m = re.match(r'^\s*size\s*:\s*(\d+)\s*,\s*(\d+)', line)
            if m:
                size = (int(m.group(1)), int(m.group(2)))
                break
            if not ATLAS_HEADER_RE.match(line):
                break
        sizes.append(size)
    return sizes


def texture_name_for_page(page: str) -> str:
    """The Texture2D holding an atlas page is named like the page without '.png'."""
    return page[:-4] if page.lower().endswith('.png') else page


# ---------------------------------------------------------------------------
# Skeleton


def is_json_skeleton(data: bytes) -> bool:
    return data.lstrip(b'\xef\xbb\xbf').lstrip()[:1] == b'{'


def spine_version(data: bytes) -> str:
    """The version string a skeleton declares (JSON skeleton.spine, or the binary header)."""
    if is_json_skeleton(data):
        m = re.search(rb'"spine"\s*:\s*"([^"]+)"', data[:4096])
        if not m:
            raise SyncError('JSON skeleton has no spine version')
        return m.group(1).decode()
    offset = 0

    def string() -> str:
        nonlocal offset
        length, shift = 0, 0
        while True:
            if offset >= len(data) or shift > 28:
                raise SyncError('Invalid Spine binary header')
            byte = data[offset]
            offset += 1
            length |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        if length == 0:
            return ''
        value = data[offset:offset + length - 1]
        offset += length - 1
        return value.decode('utf-8')

    string()  # hash
    version = string()
    if not re.match(r'^\d+\.\d+\.\d+$', version):
        raise SyncError(f'Unrecognised Spine version: {version!r}')
    return version


def script_bytes(script) -> bytes:
    """TextAsset.m_Script as bytes. UnityPy gives str (surrogateescape-decoded) or bytes."""
    if isinstance(script, str):
        return script.encode('utf-8', 'surrogateescape')
    return bytes(script)


def strip_skeleton_ext(name: str) -> str:
    return re.sub(r'\.(skel|json|prefab|bytes|txt|atlas)$', '', name.lower())


ENTRANCE_SUFFIX_RE = re.compile(r'_start(?:#\d+)?$')


def is_main_illust_name(name: str) -> bool:
    """dyn_illust_* but not an entrance skeleton (..._Start, or ..._Start#12 as in
    dyn_illust_char_2023_ling_nian_Start#12); dyn_portrait_* is never the illustration."""
    base = strip_skeleton_ext(name)
    return base.startswith(DYN_PREFIX) and not ENTRANCE_SUFFIX_RE.search(base)


def pick_one(candidates: list, name_of, dyn_illust_id: str, what: str):
    """Exactly one candidate, or the one named after the dynIllustId; anything else is an error."""
    if len(candidates) == 1:
        return candidates[0]
    exact = [c for c in candidates if strip_skeleton_ext(name_of(c)) == dyn_illust_id.lower()]
    if len(exact) == 1:
        return exact[0]
    names = sorted(name_of(c) for c in candidates)
    raise SyncError(f'Expected one {what} for {dyn_illust_id}, found {len(candidates)}: {names}')


DYNCHARS_PREFAB_DIR = 'dyn/arts/dynchars/'


def local_id(ref) -> int | None:
    """The path id of a reference to an object in the same bundle file (None otherwise)."""
    if isinstance(ref, dict) and ref.get('m_FileID', 0) == 0 and ref.get('m_PathID'):
        return ref['m_PathID']
    return None


def illust_prefab_roots(container: dict, dyn_illust_id: str) -> list:
    """Path ids of the illustration prefab the bundle's container lists under dyn/arts/dynchars/.

    Entrance prefabs are listed under dyn/arts/dyncharstart/ and portraits under
    dyn/arts/dynportraits/, so they never match, whatever their skeletons are called.
    """
    prefabs = {path.lower(): root for path, root in container.items()
               if path.lower().startswith(DYNCHARS_PREFAB_DIR) and path.lower().endswith('.prefab')}
    named = [root for path, root in prefabs.items() if path.rsplit('/', 1)[-1] == f'{dyn_illust_id.lower()}.prefab']
    return named or [prefabs[path] for path in sorted(prefabs)]


DYNCHARSTART_PREFAB_DIR = 'dyn/arts/dyncharstart/'


def entrance_prefab_roots(container: dict, dyn_entrance_id: str) -> list:
    """Path ids of the entrance prefab (dyn/arts/dyncharstart/<char>/<dynEntranceId>.prefab).

    Only the prefab named after the skin's own dynEntranceId counts: the entrance has no
    fallback by name, because an illustration and its entrance can share skeleton names.
    """
    wanted = f'{dyn_entrance_id.lower()}.prefab'
    return [root for path, root in sorted(container.items())
            if path.lower().startswith(DYNCHARSTART_PREFAB_DIR) and path.lower().rsplit('/', 1)[-1] == wanted]


def entrance_audio_path(container: dict, dyn_entrance_id: str) -> str | None:
    """The container path of the entrance's soundtrack (dyn/audio/.../dynentrance/<id>/<id>.ogg)."""
    found = [path for path in container
             if '/dynentrance/' in path.lower() and path.lower().rsplit('/', 1)[-1].rsplit('.', 1)[0] == dyn_entrance_id.lower()]
    if len(found) > 1:
        raise SyncError(f'Several soundtracks for {dyn_entrance_id}: {sorted(found)}')
    return found[0] if found else None


def skeleton_data_in_prefabs(roots: list, read) -> set:
    """Path ids of the SkeletonDataAssets the Spine components in these prefabs' object trees use.

    `read(path_id)` returns (type name, typetree) or None. The walk goes from each root
    GameObject to its components, and from each Transform down to its children.
    """
    found, seen, stack = set(), set(), list(roots)
    while stack:
        path_id = stack.pop()
        if path_id is None or path_id in seen:
            continue
        seen.add(path_id)
        entry = read(path_id)
        if not entry:
            continue
        kind, tree = entry
        if kind == 'GameObject':
            for component in tree.get('m_Component') or []:
                if isinstance(component, dict):
                    stack.append(local_id(component.get('component') or component.get('second')))
        elif kind in ('Transform', 'RectTransform'):
            stack.extend(local_id(child) for child in tree.get('m_Children') or [])
            stack.append(local_id(tree.get('m_GameObject')))
        elif kind == 'MonoBehaviour':
            data = local_id(tree.get('skeletonDataAsset'))
            if data is not None:
                found.add(data)
    return found


def choose_illust_skeleton(linked: list, in_prefab: set, dyn_illust_id: str):
    """The illustration's SkeletonDataAsset among `linked` [(path id, skeleton TextAsset name)].

    The game's own answer comes first: the one the dynchars prefab plays. Kal'tsit's boc#6 bundle
    holds the illustration and its entrance under identical names, so names alone cannot tell
    them apart. Only when the prefab does not settle it are entrance and portrait names left out
    and the rest picked by name. Returns (path id, 'prefab' | 'name'), or None when nothing is linked.
    """
    used = [c for c in linked if c[0] in in_prefab]
    if len(used) == 1:
        return used[0][0], 'prefab'
    pool = used or [c for c in linked if is_main_illust_name(c[1])]
    if not pool:
        return None
    return pick_one(pool, lambda c: c[1], dyn_illust_id, 'illustration skeleton')[0], 'name'


# ---------------------------------------------------------------------------
# Textures


def premultiply(image):
    """Straight alpha -> premultiplied alpha (rgb * a / 255, rounded), returned as an RGBA image."""
    from PIL import Image

    rgba = image.convert('RGBA')
    return Image.frombytes('RGBA', rgba.size, rgba.convert('RGBa').tobytes())


def mask_channel(mask):
    """The alpha values of a separate '[alpha]' mask texture: its blue channel, or its alpha
    channel when the colour channels are flat (an Alpha8 texture)."""
    rgba = mask.convert('RGBA')
    r, g, b, a = rgba.split()
    if b.getextrema()[0] == b.getextrema()[1] and a.getextrema()[0] != a.getextrema()[1]:
        return a
    return b


def join_alpha(image, mask):
    """Use a separate mask texture as the page's alpha (resized to the page when needed)."""
    from PIL import Image

    rgba = image.convert('RGBA')
    alpha = mask_channel(mask)
    if alpha.size != rgba.size:
        alpha = alpha.resize(rgba.size, Image.BILINEAR)
    rgba.putalpha(alpha)
    return rgba


# How a page texture stores its colour, told apart by two measurements:
# - transparentColour: mean max(r, g, b) of the texels with alpha 0;
# - semiColourAboveAlpha: share of the texels with alpha 16-63 whose max(r, g, b) exceeds alpha.
# Measured on Global bundles (client 26-09-23-17-49-43_b9cc4a):
# - one RGBA texture (ASTC; hsgma2#2, skadi2@iteration#2, agoat2@epoque#34, ines@ambienceSynesthesia#5,
#   ling@nian#12) is straight alpha with colour bleed: 118-156 and 80-97%;
# - RGB plus a separate [alpha] mask (ETC; chen2#2, chen2@boc#6 both pages, nian#2 both pages) is
#   already premultiplied: 0.02-0.07 and 5-14%.
# - a few single RGBA textures ship already premultiplied (gdglow@summer#12: 0.01 and 0.7%).
# The limits below sit far from both groups; anything between them is 'unclear' and fails the model.
ALPHA_MIN_PIXELS = 64
TRANSPARENT_PREMULTIPLIED_MAX = 8.0
TRANSPARENT_STRAIGHT_MIN = 32.0
SEMI_PREMULTIPLIED_MAX = 0.35
SEMI_STRAIGHT_MIN = 0.5


def classify_alpha(image) -> dict:
    """Whether an RGBA texture's colour is 'straight' or 'premultiplied' ('unclear' when the two
    measurements disagree, land between the limits, or there are too few texels to measure)."""
    from PIL import ImageChops, ImageStat

    r, g, b, a = image.convert('RGBA').split()
    brightest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    histogram = a.histogram()

    transparent_count = histogram[0]
    transparent = None
    if transparent_count >= ALPHA_MIN_PIXELS:
        zero = a.point(lambda v: 255 if v == 0 else 0)
        transparent = ImageStat.Stat(brightest, mask=zero).mean[0]

    semi_count = sum(histogram[16:64])
    semi = None
    if semi_count >= ALPHA_MIN_PIXELS:
        in_band = a.point(lambda v: 255 if 16 <= v < 64 else 0)
        above = ImageChops.subtract(brightest, a).point(lambda v: 255 if v else 0)
        semi = ImageStat.Stat(above, mask=in_band).mean[0] / 255

    def vote(value, premultiplied_max, straight_min):
        if value is None:
            return None
        if value <= premultiplied_max:
            return 'premultiplied'
        if value >= straight_min:
            return 'straight'
        return 'unclear'

    votes = {vote(transparent, TRANSPARENT_PREMULTIPLIED_MAX, TRANSPARENT_STRAIGHT_MIN),
             vote(semi, SEMI_PREMULTIPLIED_MAX, SEMI_STRAIGHT_MIN)} - {None}
    alpha = votes.pop() if len(votes) == 1 else 'unclear'
    return {
        'alpha': alpha,
        'transparentColour': None if transparent is None else round(transparent, 2),
        'semiColourAboveAlpha': None if semi is None else round(semi, 4),
    }


def prepare_page(image, mask=None, size: tuple[int, int] | None = None):
    """One atlas page as the site renders it: premultiplied RGBA at the size the atlas was packed at.

    - A page with a separate '[alpha]' mask: the game's RGB is already premultiplied, so the mask
      becomes the alpha channel and the colour is kept exactly as shipped (never premultiplied again).
    - A page without one: usually one straight-alpha RGBA texture with colour bleed, premultiplied
      here; when both measurements say it is already premultiplied, it is kept as shipped.

    The texture is classified first, and a page that does not look like what its path allows fails
    the model rather than being written wrong. Returns (image, info) for the run report.
    """
    from PIL import Image

    rgba = image.convert('RGBA')
    if mask is not None:
        rgba = join_alpha(rgba, mask)
    allowed = ('premultiplied',) if mask is not None else ('straight', 'premultiplied')
    info = {'mask': mask is not None, **classify_alpha(rgba)}
    if info['alpha'] not in allowed:
        how = 'RGB with a separate [alpha] mask' if mask is not None else 'one RGBA texture'
        raise SyncError(f'Page shipped as {how} should be {" or ".join(allowed)} alpha but looks {info["alpha"]} '
                        f'(transparentColour {info["transparentColour"]}, semiColourAboveAlpha {info["semiColourAboveAlpha"]})')
    page = premultiply(rgba) if info['alpha'] == 'straight' else rgba
    if size and page.size != tuple(size):
        # Spine 3.8 web runtimes compute UVs from the loaded image's size, not the atlas
        # size line, so a page must have exactly the size the atlas was packed at.
        info['resizedFrom'] = list(page.size)
        page = page.resize(tuple(size), Image.LANCZOS)
    return page, info


def encode_webp(image) -> bytes:
    """Lossless WebP, checked by decoding it again."""
    from PIL import Image

    buffer = io.BytesIO()
    image.save(buffer, 'WEBP', lossless=True, quality=100, method=4, exact=True)
    data = buffer.getvalue()
    with Image.open(io.BytesIO(data)) as back:
        if back.convert('RGBA').tobytes() != image.convert('RGBA').tobytes():
            raise SyncError('WebP encode was not lossless')
    return data


# ---------------------------------------------------------------------------
# Audio

MP3_KBPS = 160


def wav_to_mp3(wav: bytes) -> tuple[bytes, float]:
    """A 16-bit PCM WAV (what UnityPy exports for an AudioClip) as a constant-bitrate MP3, and its
    duration in seconds (3 decimals). MP3 because every browser plays it."""
    import lameenc
    import wave

    try:
        with wave.open(io.BytesIO(wav)) as source:
            channels, width, rate, frames = source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()
            pcm = source.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise SyncError(f'Soundtrack is not a readable WAV ({error})') from error
    if width != 2 or channels not in (1, 2) or not frames:
        raise SyncError(f'Soundtrack is {width * 8}-bit, {channels} channel(s), {frames} frames; expected 16-bit mono or stereo')
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_KBPS)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)
    mp3 = bytes(encoder.encode(pcm)) + bytes(encoder.flush())
    if not mp3:
        raise SyncError('MP3 encoder returned nothing')
    return mp3, round(frames / rate, 3)


# ---------------------------------------------------------------------------
# Bundles


def unpack_dat(dat: bytes, planned: Planned) -> bytes:
    """A .dat is a zip holding the bundle. Checks the sizes and md5 the client list gives."""
    import hashlib

    if planned.total_size and len(dat) != planned.total_size:
        raise SyncError(f'{planned.bundle}: downloaded {len(dat)} bytes, list says {planned.total_size}')
    try:
        with zipfile.ZipFile(io.BytesIO(dat)) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise SyncError(f'{planned.bundle}: expected one file in the .dat, found {names}')
            data = archive.read(names[0])
    except zipfile.BadZipFile as error:
        raise SyncError(f'{planned.bundle}: not a zip ({error})') from error
    if planned.ab_size and len(data) != planned.ab_size:
        raise SyncError(f'{planned.bundle}: bundle is {len(data)} bytes, list says {planned.ab_size}')
    if hashlib.md5(data).hexdigest() != planned.md5:
        raise SyncError(f'{planned.bundle}: md5 does not match the client list')
    return data


def verify_bundle(data: bytes, planned) -> bytes:
    """An unpacked bundle read from disk (a local copy): checks its size and md5 against the client list."""
    import hashlib

    if planned.ab_size and len(data) != planned.ab_size:
        raise SyncError(f'{planned.bundle}: local bundle is {len(data)} bytes, list says {planned.ab_size}')
    if hashlib.md5(data).hexdigest() != planned.md5:
        raise SyncError(f'{planned.bundle}: local bundle md5 does not match the client list')
    return data


def _patch_unitypy():
    """Arknights bundles use LZ4AK (an LZ4 variant) under the LZHAM compression flag."""
    import lz4.block
    import UnityPy
    from UnityPy.enums.BundleFile import CompressionFlags
    from UnityPy.helpers import CompressionHelper

    def decompress_lz4ak(data: bytes, uncompressed_size: int) -> bytes:
        block = bytearray(data)
        pos, end = 0, len(block)
        while pos < end:
            token = block[pos]
            literal, match = token & 0x0F, token >> 4
            block[pos] = (literal << 4) | match
            pos += 1
            if literal == 0x0F:
                while pos < end:
                    extra = block[pos]
                    pos += 1
                    literal += extra
                    if extra != 0xFF:
                        break
            pos += literal
            if pos >= end:
                break
            block[pos], block[pos + 1] = block[pos + 1], block[pos]
            pos += 2
            if match == 0x0F:
                while pos < end:
                    extra = block[pos]
                    pos += 1
                    if extra != 0xFF:
                        break
        return lz4.block.decompress(bytes(block), uncompressed_size)

    CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak
    return UnityPy


@dataclass
class DecodedEntrance:
    skeleton: bytes
    skeleton_name: str
    atlas_text: str
    atlas_name: str
    pages: list  # PIL images, premultiplied, in atlas page order
    page_names: list[str]
    page_info: list[dict]
    audio_wav: bytes | None  # the soundtrack as UnityPy exports it, None when the bundle has none
    audio_name: str | None
    # The entrance's camera moves and full-screen fades, sampled over a given duration (the entrance
    # animation's own length): entrance_camera.entrance_camera on this bundle's objects.
    camera: Callable[[float], dict | None]


@dataclass
class Decoded:
    skeleton: bytes
    skeleton_name: str
    skeleton_choice: str  # 'prefab' (the dynchars prefab uses it) or 'name'
    atlas_text: str
    atlas_name: str
    pages: list  # PIL images, premultiplied, in atlas page order
    page_names: list[str]
    page_info: list[dict] = field(default_factory=list)  # per page: mask, alpha class and measurements
    mixes: list[dict] = field(default_factory=list)
    entrance: DecodedEntrance | None = None
    # The illustration prefab's own mesh layers for the skeleton's slot names (layers.export_layers), None
    # for a bundle without the prefab.
    layers: Callable[[list], object] | None = None


def decode_bundle(data: bytes, dyn_illust_id: str, dyn_entrance_id: str | None, shaders: dict, shared=None) -> Decoded:
    """Finds the illustration's skeleton, atlas and atlas page textures in a bundle, and its
    entrance's when the skin has one (`dyn_entrance_id`, the skin_table's dynEntranceId).

    Names come from the bundle. The skeleton is the one the illustration prefab
    (dyn/arts/dynchars/<id>.prefab) plays, and its SkeletonDataAsset links it to its atlas;
    without a prefab, SkeletonDataAssets and then TextAssets are matched by name. Portrait
    skeletons, particle textures and Unity effect masks are left out. The entrance is the
    skeleton its own prefab (dyn/arts/dyncharstart/<dynEntranceId>.prefab) plays, with its
    soundtrack (an AudioClip under .../dynentrance/<dynEntranceId>/); it is never picked by name.
    The illustration prefab's own mesh layers come out through layers.export_layers, with `shaders`
    (layers.shader_table of the client's shared shader bundle) naming the shaders their materials use.
    """
    UnityPy = _patch_unitypy()
    env = UnityPy.load(data)
    texts: dict[int, tuple[str, bytes]] = {}
    textures: list = []
    skeleton_assets: dict[int, dict] = {}
    atlas_assets: dict[int, dict] = {}
    behaviours: dict[int, dict] = {}
    objects = {}
    for obj in env.objects:
        objects[obj.path_id] = obj
        kind = obj.type.name
        if kind == 'TextAsset':
            asset = obj.read()
            texts[obj.path_id] = (asset.m_Name, script_bytes(asset.m_Script))
        elif kind == 'Texture2D':
            textures.append(obj.read())
        elif kind == 'MonoBehaviour':
            try:
                tree = obj.read_typetree()
            except Exception:  # noqa: BLE001 - unreadable scripts are not Spine assets
                continue
            behaviours[obj.path_id] = tree
            if 'skeletonJSON' in tree and 'atlasAssets' in tree:
                skeleton_assets[obj.path_id] = tree
            elif 'atlasFile' in tree and 'materials' in tree:
                atlas_assets[obj.path_id] = tree

    def text_of(ref) -> tuple[str, bytes] | None:
        if not isinstance(ref, dict) or ref.get('m_FileID', 0) != 0:
            return None
        return texts.get(ref.get('m_PathID'))

    def read(path_id):
        if path_id in behaviours:
            return 'MonoBehaviour', behaviours[path_id]
        obj = objects.get(path_id)
        if obj is None or obj.type.name not in ('GameObject', 'Transform', 'RectTransform'):
            return None
        try:
            return obj.type.name, obj.read_typetree()
        except Exception:  # noqa: BLE001 - an unreadable object is simply not followed
            return None

    trees: dict[int, tuple[str, dict] | None] = {}

    def read_any(path_id):
        """Any object in this bundle as (type name, typetree), or None (cached)."""
        if path_id in behaviours:
            return 'MonoBehaviour', behaviours[path_id]
        if path_id not in trees:
            obj = objects.get(path_id)
            try:
                trees[path_id] = (obj.type.name, obj.read_typetree()) if obj is not None else None
            except Exception:  # noqa: BLE001 - an unreadable object reads as missing
                trees[path_id] = None
        return trees[path_id]

    container = {}
    for path, ref in env.container.items():
        path_id = getattr(ref, 'path_id', None) or getattr(ref, 'm_PathID', None)
        if path_id:
            container[path] = path_id

    linked = [(path_id, text_of(tree.get('skeletonJSON'))) for path_id, tree in skeleton_assets.items()]
    linked = [(path_id, skeleton) for path_id, skeleton in linked if skeleton]

    def linked_parts(skeleton_id: int) -> tuple[tuple[str, bytes], tuple[str, bytes], list[dict]]:
        """The skeleton TextAsset, the one atlas and the crossfade table a SkeletonDataAsset links."""
        tree = skeleton_assets[skeleton_id]
        skeleton = dict(linked)[skeleton_id]
        atlas_refs = [atlas_assets.get(ref.get('m_PathID')) for ref in tree.get('atlasAssets', []) if isinstance(ref, dict)]
        atlas_texts = [text_of(a.get('atlasFile')) for a in atlas_refs if a]
        atlas_texts = [a for a in atlas_texts if a]
        if len(atlas_texts) != 1:
            raise SyncError(f'{skeleton[0]}: expected one atlas, found {len(atlas_texts)}')
        mixes = [{'from': str(source), 'to': str(target), 'duration': round(float(seconds), 3)}
                 for source, target, seconds in zip(tree.get('fromAnimation') or [], tree.get('toAnimation') or [], tree.get('duration') or [])]
        return skeleton, atlas_texts[0], mixes

    def find_texture(name: str, size) -> object | None:
        found = [t for t in textures if t.m_Name == name] or [t for t in textures if t.m_Name.lower() == name.lower()]
        if len(found) > 1 and size:
            found = [t for t in found if (t.m_Width, t.m_Height) == size] or found
        if len(found) > 1:
            raise SyncError(f'Several textures are named {name}')
        return found[0] if found else None

    def atlas_and_pages(skeleton: tuple[str, bytes], atlas: tuple[str, bytes]):
        """The atlas as text and its page images, prepared (prepare_page), in page order."""
        skeleton_name, skeleton_bytes = skeleton
        atlas_name, atlas_bytes = atlas
        try:
            atlas_text = atlas_bytes.decode('utf-8')
        except UnicodeDecodeError as error:
            raise SyncError(f'{atlas_name}: atlas is not UTF-8') from error
        if not skeleton_bytes:
            raise SyncError(f'{skeleton_name}: empty skeleton')
        page_names = atlas_page_names(atlas_text)
        sizes = atlas_page_sizes(atlas_text)
        if not page_names:
            raise SyncError(f'{atlas_name}: no pages')
        pages, page_info = [], []
        for page, size in zip(page_names, sizes):
            texture_name = texture_name_for_page(page)
            texture = find_texture(texture_name, size)
            if texture is None:
                raise SyncError(f'{atlas_name}: no texture for page {page}')
            mask = find_texture(f'{texture_name}[alpha]', size)
            try:
                image, info = prepare_page(texture.image, mask.image if mask is not None else None, size)
            except SyncError as error:
                raise SyncError(f'{atlas_name}: page {page}: {error}') from error
            pages.append(image)
            page_info.append({'page': page, **info})
        return atlas_text, pages, page_names, page_info

    illust_roots = illust_prefab_roots(container, dyn_illust_id)
    in_prefab = skeleton_data_in_prefabs(illust_roots, read)
    mixes: list[dict] = []
    choice = choose_illust_skeleton([(path_id, skeleton[0]) for path_id, skeleton in linked], in_prefab, dyn_illust_id)
    if choice:
        chosen_id, skeleton_choice = choice
        skeleton, atlas, mixes = linked_parts(chosen_id)
    else:
        skeletons = [t for t in texts.values() if is_main_illust_name(t[0]) and not t[0].lower().endswith('.atlas')
                     and (t[0].lower().endswith('.skel') or is_json_skeleton(t[1]))]
        skeleton = pick_one(skeletons, lambda c: c[0], dyn_illust_id, 'illustration skeleton')
        skeleton_choice = 'name'
        atlases = [t for t in texts.values() if t[0].lower().endswith('.atlas') and is_main_illust_name(t[0])]
        same = [a for a in atlases if strip_skeleton_ext(a[0]) == strip_skeleton_ext(skeleton[0])]
        atlas = same[0] if len(same) == 1 else pick_one(atlases, lambda c: c[0], dyn_illust_id, 'atlas')
    atlas_text, pages, page_names, page_info = atlas_and_pages(skeleton, atlas)

    entrance = None
    if dyn_entrance_id is not None:
        roots = entrance_prefab_roots(container, dyn_entrance_id)
        if not roots:
            raise SyncError(f'The skin has the entrance {dyn_entrance_id}, but the bundle has no prefab for it')
        played = skeleton_data_in_prefabs(roots, read) & set(dict(linked))
        if len(played) != 1:
            raise SyncError(f'Entrance prefab {dyn_entrance_id} plays {len(played)} skeletons, expected one')
        entrance_id = played.pop()
        if choice and entrance_id == choice[0]:
            raise SyncError(f'Entrance prefab {dyn_entrance_id} plays the illustration skeleton')
        entrance_skeleton, entrance_atlas, _ = linked_parts(entrance_id)
        e_atlas_text, e_pages, e_page_names, e_page_info = atlas_and_pages(entrance_skeleton, entrance_atlas)
        audio_wav, audio_name = None, None
        audio_path = entrance_audio_path(container, dyn_entrance_id)
        if audio_path is not None:
            clip = env.container[audio_path].read()
            samples = clip.samples
            if len(samples) != 1:
                raise SyncError(f'{audio_path}: expected one sample, found {len(samples)}')
            audio_name, audio_wav = next(iter(samples.items()))
        root_go = roots[0]

        def camera(duration: float, root_go=root_go, skeleton_data=entrance_id) -> dict | None:
            try:
                return entrance_camera.entrance_camera(root_go, skeleton_data, read_any, duration)
            except (entrance_camera.CameraError, KeyError, TypeError, ValueError, ArithmeticError) as error:
                raise SyncError(f'Entrance camera of {dyn_entrance_id}: {error}') from error

        entrance = DecodedEntrance(entrance_skeleton[1], entrance_skeleton[0], e_atlas_text, entrance_atlas[0],
                                   e_pages, e_page_names, e_page_info, audio_wav, audio_name, camera)

    export = None
    if illust_roots and skeleton_choice == 'prefab':
        mesh_of, texture_of, external_of = layers.bundle_readers(env, objects)

        def export(slots: list, root_go=illust_roots[0]):
            try:
                return layers.export_layers(root_go, read_any, mesh_of=mesh_of, texture_of=texture_of, classify_texture=classify_alpha,
                                            external_of=external_of, shaders=shaders, slots=slots, shared=shared)
            except (layers.LayerError, entrance_camera.CameraError, KeyError, TypeError, ValueError, ArithmeticError) as error:
                raise SyncError(f'Layers of {dyn_illust_id}: {error}') from error

    return Decoded(skeleton[1], skeleton[0], skeleton_choice, atlas_text, atlas[0], pages, page_names, page_info, mixes, entrance, export)
