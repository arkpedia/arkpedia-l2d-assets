"""Unit tests for scripts/l2d.py. Run: python -m unittest discover -s test -p 'test_*.py'"""
import hashlib
import importlib.util
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scripts'))
import l2d  # noqa: E402

FIXTURES = HERE / 'fixtures'


def row(image):
    """The pixels of a one-row image."""
    return [image.getpixel((x, 0)) for x in range(image.width)]
NAMES = json.loads((FIXTURES / 'names.json').read_text('utf-8'))


class Naming(unittest.TestCase):
    def test_shared_naming_table(self):
        for row in NAMES:
            self.assertEqual(l2d.slug_for(row['skinId']), row['slug'])
            self.assertEqual(l2d.folder_for(row['skinId'], row['md5']), row['folder'])
            self.assertEqual(l2d.bundle_name_for(row['dynIllustId']), row['bundle'])
            self.assertEqual(l2d.download_name(row['bundle']), row['download'])

    def test_bad_names_are_refused(self):
        for bad in ['', 'char_1/2#3', 'char_x@y', '../char#1', 'char x#1']:
            with self.assertRaises(l2d.SyncError):
                l2d.slug_for(bad)
        with self.assertRaises(l2d.SyncError):
            l2d.folder_for('char_1044_hsgma2#2', 'B1259EDB8FFF5A0A56F0B01FE29DC6D9')
        with self.assertRaises(l2d.SyncError):
            l2d.bundle_name_for('dyn_portrait_char_1044_hsgma2')

    def test_download_url(self):
        self.assertEqual(
            l2d.download_url('https://cdn.example/assetbundle/official/', 'Android', '26-09-23', 'arts/dynchars/char_113_cqbw_epoque#7.ab'),
            'https://cdn.example/assetbundle/official/Android/assets/26-09-23/arts_dynchars_char_113_cqbw_epoque__7.dat')


class Planning(unittest.TestCase):
    skin_table = {'charSkins': {
        'char_1044_hsgma2#2': {'dynIllustId': 'dyn_illust_char_1044_hsgma2_2'},
        'char_1046_sbell2@ambienceSynesthesia#8': {'dynIllustId': 'dyn_illust_char_1046_sbell2_ambienceSynesthesia#8'},
        'char_9999_new@sale#1': {'dynIllustId': 'dyn_illust_char_9999_new_sale#1'},
        'char_1044_hsgma2#1': {'dynIllustId': None},
    }}
    hot_update_list = {'abInfos': [
        {'name': 'arts/dynchars/char_1044_hsgma2_2.ab', 'md5': 'b1259edb8fff5a0a56f0b01fe29dc6d9', 'totalSize': 5387485, 'abSize': 5977591},
        {'name': 'arts/dynchars/char_1046_sbell2_ambiencesynesthesia#8.ab', 'md5': 'f' * 32, 'totalSize': 10, 'abSize': 20},
        {'name': 'arts/dynchars/effect.ab', 'md5': 'e' * 32, 'totalSize': 1, 'abSize': 1},
    ]}

    def test_picks_listed_dynamic_skins_only(self):
        plan = l2d.plan_models(self.skin_table, self.hot_update_list)
        self.assertEqual([m.skin_id for m in plan.models], ['char_1044_hsgma2#2', 'char_1046_sbell2@ambienceSynesthesia#8'])
        self.assertEqual(plan.unlisted, ['char_9999_new@sale#1'])
        self.assertEqual(plan.models[0].folder, 'models/char_1044_hsgma2_2/b1259edb8fff')
        self.assertEqual(plan.models[1].bundle, 'arts/dynchars/char_1046_sbell2_ambiencesynesthesia#8.ab')

    def test_folder_name_collisions_are_refused(self):
        table = {'charSkins': {'char_1@a#1': {'dynIllustId': 'dyn_illust_char_1_a#1'}, 'char_1_a#1': {'dynIllustId': 'dyn_illust_char_1_a_1'}}}
        hul = {'abInfos': [{'name': 'arts/dynchars/char_1_a#1.ab', 'md5': 'a' * 32}, {'name': 'arts/dynchars/char_1_a_1.ab', 'md5': 'b' * 32}]}
        with self.assertRaises(l2d.SyncError):
            l2d.plan_models(table, hul)


class Atlas(unittest.TestCase):
    def test_two_page_atlas_is_rewritten_byte_for_byte(self):
        original = (FIXTURES / 'two-page.atlas').read_text('utf-8')
        rewritten, names = l2d.rewrite_atlas(original)
        self.assertEqual(rewritten, (FIXTURES / 'two-page.rewritten.atlas').read_text('utf-8'))
        self.assertEqual(names, ['dyn_illust_char_2014_nian.png', 'dyn_illust_char_2014_nian2.png'])
        self.assertEqual(l2d.atlas_page_sizes(original), [(2048, 2048), (2048, 1024)])

    def test_hash_in_page_names_crlf_and_bom_survive(self):
        text = '﻿dyn_illust_char_1012_skadi2_iteration#2.png\r\nsize: 4,4\r\nformat: RGBA8888\r\nfilter: Linear,Linear\r\nrepeat: none\r\nregion#1\r\n  rotate: false\r\n'
        rewritten, names = l2d.rewrite_atlas(text)
        self.assertEqual(names, ['dyn_illust_char_1012_skadi2_iteration#2.png'])
        self.assertEqual(rewritten, '﻿page0.webp\r\nsize: 4,4\r\nformat: RGBA8888\r\nfilter: Linear,Linear\r\nrepeat: none\r\nregion#1\r\n  rotate: false\r\n')

    def test_region_names_that_look_like_files_are_not_pages(self):
        text = 'a.png\nsize: 4,4\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\nhair.png\n  rotate: false\n  xy: 0, 0\n'
        self.assertEqual(l2d.atlas_page_names(text), ['a.png'])

    def test_repeated_or_missing_pages_are_refused(self):
        page = 'p.png\nsize: 1,1\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\n'
        with self.assertRaises(l2d.SyncError):
            l2d.rewrite_atlas(page + '\n' + page)
        with self.assertRaises(l2d.SyncError):
            l2d.rewrite_atlas('just text\nno header\n')

    def test_texture_name_for_page(self):
        self.assertEqual(l2d.texture_name_for_page('dyn_illust_char_2014_nian2.png'), 'dyn_illust_char_2014_nian2')
        self.assertEqual(l2d.texture_name_for_page('page'), 'page')


class Textures(unittest.TestCase):
    def test_premultiply_rounds_like_rgb_times_alpha_over_255(self):
        image = Image.new('RGBA', (4, 1))
        image.putdata([(255, 128, 0, 128), (200, 100, 50, 0), (10, 20, 30, 255), (255, 255, 255, 1)])
        self.assertEqual(row(l2d.premultiply(image)), [(128, 64, 0, 128), (0, 0, 0, 0), (10, 20, 30, 255), (1, 1, 1, 1)])

    def test_mask_join_uses_the_blue_channel_and_resizes(self):
        page = Image.new('RGBA', (4, 4), (100, 150, 200, 255))
        mask = Image.new('RGB', (2, 2), (0, 0, 77))
        joined = l2d.join_alpha(page, mask)
        self.assertEqual(joined.size, (4, 4))
        self.assertEqual(joined.getpixel((3, 3)), (100, 150, 200, 77))

    def test_alpha8_mask_uses_its_alpha(self):
        mask = Image.new('RGBA', (2, 1), (0, 0, 0, 0))
        mask.putpixel((1, 0), (0, 0, 0, 90))
        self.assertEqual(row(l2d.mask_channel(mask)), [0, 90])


    def test_webp_is_lossless(self):
        image = l2d.premultiply(Image.effect_noise((64, 48), 80).convert('RGBA'))
        data = l2d.encode_webp(image)
        self.assertEqual(data[12:16], b'VP8L')
        with Image.open(io.BytesIO(data)) as back:
            self.assertEqual(back.convert('RGBA').tobytes(), image.tobytes())


PAGES = FIXTURES / 'pages'


def fixture_image(name):
    with Image.open(PAGES / name) as image:
        return image.convert('RGBA') if image.mode == 'RGBA' else image.convert('RGB')


def semi_colour(image):
    """Mean max(r, g, b) of the texels with alpha 16-63."""
    data = image.convert('RGBA').tobytes()
    values = [max(data[i:i + 3]) for i in range(0, len(data), 4) if 16 <= data[i + 3] < 64]
    return sum(values) / len(values)


class PageAlpha(unittest.TestCase):
    """64x64 crops of real Global page textures (test/fixtures/pages, see README there):
    masked-rgb.png + masked-alpha.png from chen2#2 (ETC RGB plus a separate [alpha] mask, RGB already
    premultiplied by the game) and straight-rgba.png from hsgma2#2 (one ASTC RGBA texture, straight
    alpha with colour bleed). Each crop holds alpha-0 texels and texels with alpha 16-63."""

    def setUp(self):
        self.masked_rgb = fixture_image('masked-rgb.png')
        self.masked_alpha = fixture_image('masked-alpha.png')
        self.straight = fixture_image('straight-rgba.png')
        self.joined = l2d.join_alpha(self.masked_rgb, self.masked_alpha)

    def test_fixtures_hold_both_alpha_bands(self):
        for image in (self.joined, self.straight):
            histogram = image.getchannel('A').histogram()
            self.assertGreaterEqual(histogram[0], 1000)
            self.assertGreaterEqual(sum(histogram[16:64]), 500)

    def test_classifies_the_real_crops(self):
        masked = l2d.classify_alpha(self.joined)
        self.assertEqual(masked['alpha'], 'premultiplied', masked)
        self.assertLess(masked['transparentColour'], 1)
        straight = l2d.classify_alpha(self.straight)
        self.assertEqual(straight['alpha'], 'straight', straight)
        self.assertGreater(straight['transparentColour'], 50)
        self.assertEqual(l2d.classify_alpha(l2d.premultiply(self.straight))['alpha'], 'premultiplied')

    def test_masked_page_keeps_the_shipped_colour(self):
        page, info = l2d.prepare_page(self.masked_rgb, self.masked_alpha)
        self.assertEqual(page.tobytes(), self.joined.tobytes())
        self.assertTrue(info['mask'])
        self.assertEqual(info['alpha'], 'premultiplied')
        # The bug this guards against: premultiplying the already premultiplied RGB again
        # darkens the semi-transparent texels (glows, soft edges) to a fraction of their colour.
        self.assertNotEqual(page.tobytes(), l2d.premultiply(self.joined).tobytes())
        self.assertGreater(semi_colour(page), 10)
        self.assertLess(semi_colour(l2d.premultiply(self.joined)), semi_colour(page) / 4)

    def test_straight_page_is_premultiplied(self):
        page, info = l2d.prepare_page(self.straight)
        self.assertEqual(page.tobytes(), l2d.premultiply(self.straight).tobytes())
        self.assertFalse(info['mask'])
        self.assertEqual(info['alpha'], 'straight')

    def test_a_page_that_does_not_match_its_path_fails(self):
        # An already premultiplied texture without a mask, and a straight one with a mask.
        with self.assertRaisesRegex(l2d.SyncError, 'should be straight alpha but looks premultiplied'):
            l2d.prepare_page(self.joined)
        with self.assertRaisesRegex(l2d.SyncError, 'should be premultiplied alpha but looks straight'):
            l2d.prepare_page(self.straight, self.straight.getchannel('A').convert('RGB'))
        # Nothing to measure (fully opaque, as an ETC RGB page is before its mask joins): unclear.
        with self.assertRaisesRegex(l2d.SyncError, 'looks unclear'):
            l2d.prepare_page(self.masked_rgb)

    def test_measurements_that_disagree_are_unclear(self):
        # Black under alpha 0 (reads premultiplied) but bright colour at alpha 16-63 (reads straight).
        image = Image.new('RGBA', (32, 8), (0, 0, 0, 0))
        for x in range(32):
            for y in range(4, 8):
                image.putpixel((x, y), (200, 200, 200, 30))
        self.assertEqual(l2d.classify_alpha(image)['alpha'], 'unclear')

    def test_page_is_resized_to_the_atlas_size(self):
        page, info = l2d.prepare_page(self.straight, None, (32, 32))
        self.assertEqual(page.size, (32, 32))
        self.assertEqual(info['resizedFrom'], [64, 64])
        _, same = l2d.prepare_page(self.straight, None, (64, 64))
        self.assertNotIn('resizedFrom', same)


class Skeletons(unittest.TestCase):
    def test_format_and_version(self):
        binary = bytes([5]) + b'hash' + bytes([7]) + b'3.8.99' + b'\x00rest'
        self.assertFalse(l2d.is_json_skeleton(binary))
        self.assertEqual(l2d.spine_version(binary), '3.8.99')
        json_skeleton = b'\xef\xbb\xbf  {"skeleton":{"hash":"x","spine":"3.8.99"}}'
        self.assertTrue(l2d.is_json_skeleton(json_skeleton))
        self.assertEqual(l2d.spine_version(json_skeleton), '3.8.99')
        self.assertEqual(l2d.spine_version((FIXTURES / 'tiny' / 'skeleton.json').read_bytes()), '3.8.99')
        with self.assertRaises(l2d.SyncError):
            l2d.spine_version(bytes([5]) + b'hash' + bytes([4]) + b'bad')

    def test_textasset_bytes_survive_surrogateescape(self):
        raw = bytes(range(256))
        self.assertEqual(l2d.script_bytes(raw.decode('utf-8', 'surrogateescape')), raw)
        self.assertEqual(l2d.script_bytes(memoryview(raw)), raw)

    def test_main_skeleton_choice(self):
        names = ['dyn_illust_char_1012_skadi2_iteration#2.skel', 'dyn_illust_char_1012_skadi2_iteration#2_Start.skel',
                 'dyn_portrait_char_1012_skadi2_iteration#2.skel']
        kept = [n for n in names if l2d.is_main_illust_name(n)]
        self.assertEqual(kept, ['dyn_illust_char_1012_skadi2_iteration#2.skel'])
        # Nian's Elite 2 files are not named after the id; a single candidate is taken as is.
        self.assertEqual(l2d.pick_one(['dyn_illust_char_2014_nian2.skel'], str, 'dyn_illust_char_2014_nian_2', 'skeleton'), 'dyn_illust_char_2014_nian2.skel')
        two = ['dyn_illust_char_4087_ines_ambienceSynesthesia#5', 'dyn_illust_char_4087_ines_other']
        self.assertEqual(l2d.pick_one(two, str, 'dyn_illust_char_4087_ines_ambiencesynesthesia#5', 'skeleton'), two[0])
        with self.assertRaises(l2d.SyncError):
            l2d.pick_one(['dyn_illust_a.skel', 'dyn_illust_b.skel'], str, 'dyn_illust_c', 'skeleton')


class Bundles(unittest.TestCase):
    def dat(self, payload: bytes) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('arts/dynchars/x.ab', payload)
        return buffer.getvalue()

    def test_sizes_and_md5_are_checked(self):
        payload = b'UnityFS bundle bytes'
        dat = self.dat(payload)
        planned = l2d.Planned('char_1_x#1', 'dyn_illust_char_1_x_1', 'arts/dynchars/x.ab', hashlib.md5(payload).hexdigest(), len(dat), len(payload))
        self.assertEqual(l2d.unpack_dat(dat, planned), payload)
        for broken in [
            l2d.Planned(planned.skin_id, planned.dyn_illust_id, planned.bundle, '0' * 32, len(dat), len(payload)),
            l2d.Planned(planned.skin_id, planned.dyn_illust_id, planned.bundle, planned.md5, len(dat) + 1, len(payload)),
            l2d.Planned(planned.skin_id, planned.dyn_illust_id, planned.bundle, planned.md5, len(dat), len(payload) + 1),
        ]:
            with self.assertRaises(l2d.SyncError):
                l2d.unpack_dat(dat, broken)
        with self.assertRaises(l2d.SyncError):
            l2d.unpack_dat(b'not a zip', l2d.Planned('a#1', 'dyn_illust_a_1', 'arts/dynchars/a.ab', 'a' * 32, 0, 0))

    @unittest.skipUnless(importlib.util.find_spec('UnityPy') and importlib.util.find_spec('lz4'), 'UnityPy/lz4 not installed')
    def test_unitypy_lz4ak_patch_installs(self):
        from UnityPy.enums.BundleFile import CompressionFlags
        from UnityPy.helpers import CompressionHelper
        l2d._patch_unitypy()
        self.assertEqual(CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM].__name__, 'decompress_lz4ak')


if __name__ == '__main__':
    unittest.main()
