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

DYN_PREFIX = 'dyn_illust_'
BUNDLE_DIR = 'arts/dynchars/'
SKIN_ID_RE = re.compile(r'^[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?#[0-9]+$')
MD5_RE = re.compile(r'^[0-9a-f]{32}$')
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
    return f'{asset_base.rstrip("/")}/{platform}/assets/{res_version}/{download_name(bundle_name)}'


# ---------------------------------------------------------------------------
# Choosing what to sync


@dataclass
class Planned:
    skin_id: str
    dyn_illust_id: str
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
        plan.models.append(Planned(skin_id, dyn, info['name'], md5, int(info.get('totalSize') or 0), int(info.get('abSize') or 0)))
    return plan


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


def rewrite_atlas(text: str) -> tuple[str, list[str]]:
    """Renames the pages to page0.webp, page1.webp, ... and leaves every other byte alone.

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
        lines[i] = f'{bom}page{n}.webp{ending}'
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


def is_main_illust_name(name: str) -> bool:
    """dyn_illust_* but not the _Start (entrance) skeleton; dyn_portrait_* is never the illustration."""
    base = strip_skeleton_ext(name)
    return base.startswith(DYN_PREFIX) and not base.endswith('_start')


def pick_one(candidates: list, name_of, dyn_illust_id: str, what: str):
    """Exactly one candidate, or the one named after the dynIllustId; anything else is an error."""
    if len(candidates) == 1:
        return candidates[0]
    exact = [c for c in candidates if strip_skeleton_ext(name_of(c)) == dyn_illust_id.lower()]
    if len(exact) == 1:
        return exact[0]
    names = sorted(name_of(c) for c in candidates)
    raise SyncError(f'Expected one {what} for {dyn_illust_id}, found {len(candidates)}: {names}')


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
    - A page without one: one straight-alpha RGBA texture with colour bleed, premultiplied here.

    The texture is classified first, and a page that does not look like what its path expects fails
    the model rather than being written wrong. Returns (image, info) for the run report.
    """
    from PIL import Image

    rgba = image.convert('RGBA')
    if mask is not None:
        rgba = join_alpha(rgba, mask)
    expected = 'premultiplied' if mask is not None else 'straight'
    info = {'mask': mask is not None, **classify_alpha(rgba)}
    if info['alpha'] != expected:
        how = 'RGB with a separate [alpha] mask' if mask is not None else 'one RGBA texture'
        raise SyncError(f'Page shipped as {how} should be {expected} alpha but looks {info["alpha"]} '
                        f'(transparentColour {info["transparentColour"]}, semiColourAboveAlpha {info["semiColourAboveAlpha"]})')
    page = rgba if mask is not None else premultiply(rgba)
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
class Decoded:
    skeleton: bytes
    skeleton_name: str
    atlas_text: str
    atlas_name: str
    pages: list  # PIL images, premultiplied, in atlas page order
    page_names: list[str]
    page_info: list[dict] = field(default_factory=list)  # per page: mask, alpha class and measurements
    mixes: list[dict] = field(default_factory=list)


def decode_bundle(data: bytes, dyn_illust_id: str) -> Decoded:
    """Finds the illustration's skeleton, atlas and atlas page textures in a bundle.

    Names come from the bundle: the game's SkeletonDataAsset links a skeleton to its atlas;
    without one, TextAssets are matched by name. Entrance (_Start) and portrait skeletons,
    particle textures and Unity effect masks are left out.
    """
    UnityPy = _patch_unitypy()
    env = UnityPy.load(data)
    texts: dict[int, tuple[str, bytes]] = {}
    textures: list = []
    skeleton_assets: list[dict] = []
    atlas_assets: dict[int, dict] = {}
    for obj in env.objects:
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
            if 'skeletonJSON' in tree and 'atlasAssets' in tree:
                skeleton_assets.append(tree)
            elif 'atlasFile' in tree and 'materials' in tree:
                atlas_assets[obj.path_id] = tree

    def text_of(ref) -> tuple[str, bytes] | None:
        if not isinstance(ref, dict) or ref.get('m_FileID', 0) != 0:
            return None
        return texts.get(ref.get('m_PathID'))

    mixes: list[dict] = []
    linked = []
    for tree in skeleton_assets:
        skeleton = text_of(tree.get('skeletonJSON'))
        if skeleton and is_main_illust_name(skeleton[0]):
            linked.append((tree, skeleton))
    if linked:
        tree, skeleton = pick_one(linked, lambda c: c[1][0], dyn_illust_id, 'illustration skeleton')
        atlas_refs = [atlas_assets.get(ref.get('m_PathID')) for ref in tree.get('atlasAssets', []) if isinstance(ref, dict)]
        atlas_texts = [text_of(a.get('atlasFile')) for a in atlas_refs if a]
        atlas_texts = [a for a in atlas_texts if a]
        if len(atlas_texts) != 1:
            raise SyncError(f'{skeleton[0]}: expected one atlas, found {len(atlas_texts)}')
        atlas = atlas_texts[0]
        for source, target, seconds in zip(tree.get('fromAnimation') or [], tree.get('toAnimation') or [], tree.get('duration') or []):
            mixes.append({'from': str(source), 'to': str(target), 'duration': round(float(seconds), 3)})
    else:
        skeletons = [t for t in texts.values() if is_main_illust_name(t[0]) and not t[0].lower().endswith('.atlas')
                     and (t[0].lower().endswith('.skel') or is_json_skeleton(t[1]))]
        skeleton = pick_one(skeletons, lambda c: c[0], dyn_illust_id, 'illustration skeleton')
        atlases = [t for t in texts.values() if t[0].lower().endswith('.atlas') and is_main_illust_name(t[0])]
        same = [a for a in atlases if strip_skeleton_ext(a[0]) == strip_skeleton_ext(skeleton[0])]
        atlas = same[0] if len(same) == 1 else pick_one(atlases, lambda c: c[0], dyn_illust_id, 'atlas')

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

    def find_texture(name: str, size) -> object | None:
        found = [t for t in textures if t.m_Name == name] or [t for t in textures if t.m_Name.lower() == name.lower()]
        if len(found) > 1 and size:
            found = [t for t in found if (t.m_Width, t.m_Height) == size] or found
        if len(found) > 1:
            raise SyncError(f'Several textures are named {name}')
        return found[0] if found else None

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
    return Decoded(skeleton_bytes, skeleton_name, atlas_text, atlas_name, pages, page_names, page_info, mixes)
