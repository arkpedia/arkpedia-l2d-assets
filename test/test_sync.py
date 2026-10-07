"""Tests for scripts/sync.py's run loop, with the network and the bundle decoding replaced.

Run: python -m unittest discover -s test -p 'test_*.py'
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scripts'))
import l2d  # noqa: E402
import layers  # noqa: E402
import sync  # noqa: E402

GOOD = 'char_1044_hsgma2#2'
BROKEN = 'char_003_kalts@boc#6'
CN_ONLY = 'char_4179_monstr@boc#11'
GLOBAL_BASE = 'https://cdn.example/assetbundle/official'
CN_BASE = 'https://cn.example/assetbundle/official'


def write_model(planned, staging, layers_version=layers.LAYERS_VERSION):
    """The fields the run loop reads back from an existing folder."""
    (staging / 'model.json').write_text(json.dumps({'skinId': planned.skin_id, 'source': {'md5': planned.md5}, 'layersVersion': layers_version}))


def dat_for(payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('bundle.ab', payload)
    return buffer.getvalue()


class RunLoop(unittest.TestCase):
    """One good bundle and one that always fails to build, like Kal'tsit's boc#6 did."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='l2d-sync-test-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.payloads = {GOOD: b'good bundle', BROKEN: b'broken bundle'}
        self.code = 'abcdef012345'
        self.downloads = []
        self.download_error = None
        self.skin_table = {'charSkins': {
            GOOD: {'dynIllustId': 'dyn_illust_char_1044_hsgma2_2'},
            BROKEN: {'dynIllustId': 'dyn_illust_char_003_kalts_boc#6'},
        }}
        self.set_bundles()
        # The CN client: by default the same skins as Global, so it adds nothing.
        self.cn_skin_table = None
        self.cn_payloads = {}
        self.built_from = {}
        patches = {
            'ROOT': self.root,
            'code_version': lambda: self.code,
            'get_json': lambda url: (self.cn_skin_table or self.skin_table) if 'zh_CN' in url else self.skin_table,
            'client_list': self.fake_client_list,
            'get': self.fake_get,
            'build_model': self.fake_build,
            'load_shaders': self.fake_shaders,
        }
        self.shaders_error = None
        for name, value in patches.items():
            original = getattr(sync, name)
            setattr(sync, name, value)
            self.addCleanup(setattr, sync, name, original)
        summary = os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if summary is not None:
            self.addCleanup(os.environ.__setitem__, 'GITHUB_STEP_SUMMARY', summary)

    def set_bundles(self):
        self.dats = {skin: dat_for(payload) for skin, payload in self.payloads.items()}
        names = {skin: l2d.bundle_name_for(self.skin_table['charSkins'][skin]['dynIllustId']) for skin in self.payloads}
        self.hot_update_list = {'abInfos': [
            {'name': names[skin], 'md5': hashlib.md5(self.payloads[skin]).hexdigest(),
             'totalSize': len(self.dats[skin]), 'abSize': len(self.payloads[skin])}
            for skin in self.payloads
        ]}
        self.by_url = {l2d.download_name(names[skin]): skin for skin in self.payloads}

    def cn_list(self):
        """CN's list: Global's bundles plus the CN-only ones (other md5s where CN's payload differs)."""
        infos = [dict(info) for info in self.hot_update_list['abInfos']]
        for skin, payload in self.cn_payloads.items():
            name = self.cn_name(skin)
            infos = [i for i in infos if i['name'] != name]
            infos.append({'name': name, 'md5': hashlib.md5(payload).hexdigest(), 'totalSize': len(dat_for(payload)), 'abSize': len(payload)})
        return {'abInfos': infos}

    def cn_name(self, skin):
        table = (self.cn_skin_table or self.skin_table)['charSkins']
        return l2d.bundle_name_for(table[skin]['dynIllustId'])

    def fake_client_list(self, client):
        if client.server == 'cn':
            return CN_BASE, 'cn-res-1', self.cn_list()
        return GLOBAL_BASE, 'res-1', self.hot_update_list

    def fake_get(self, url):
        if self.download_error:
            raise self.download_error
        name = url.rsplit('/', 1)[-1]
        if url.startswith(CN_BASE):
            for skin, payload in self.cn_payloads.items():
                if l2d.download_name(self.cn_name(skin)) == name:
                    self.downloads.append(('cn', skin))
                    return dat_for(payload)
        skin = self.by_url[name]
        self.downloads.append(skin)
        return self.dats[skin]

    def fake_shaders(self, asset_base, res_version, hot_update_list, bundles):
        if self.shaders_error:
            raise self.shaders_error
        return {('CAB-shaders', 1): 'a shader'}

    def fake_build(self, planned, bundle, res_version, staging, shaders, shared=None, *, particles):
        self.assertFalse(particles, 'particles are exported only with --particles')
        expected = self.cn_payloads.get(planned.skin_id) if planned.server == 'cn' else self.payloads.get(planned.skin_id)
        self.assertEqual(bundle, expected)  # verified and unpacked first
        self.built_from[planned.skin_id] = (planned.server, res_version)
        self.assertEqual(shaders, {('CAB-shaders', 1): 'a shader'})
        if planned.skin_id == BROKEN:
            raise l2d.SyncError('Expected one illustration skeleton, found 2')
        write_model(planned, staging)
        return {}, [{'page': 'p.png', 'mask': False, 'alpha': 'straight', 'transparentColour': 150.0,
                     'semiColourAboveAlpha': 0.9}], None

    def run_sync(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(['--pause', '0', '--report', str(self.root / 'report.json'), *args]), 0)
        return json.loads((self.root / 'report.json').read_text())

    def failures(self):
        return json.loads((self.root / 'sync-failures.json').read_text())['failures']

    def test_a_bundle_that_failed_to_build_is_not_downloaded_again(self):
        report = self.run_sync()
        self.assertEqual(sorted(self.downloads), sorted([GOOD, BROKEN]))
        self.assertEqual(report['added'], [GOOD])
        self.assertEqual([f['skinId'] for f in report['failed']], [BROKEN])
        self.assertEqual(self.failures()[BROKEN]['md5'], hashlib.md5(self.payloads[BROKEN]).hexdigest())
        self.assertEqual(self.failures()[BROKEN]['code'], self.code)
        recorded = (self.root / 'sync-failures.json').read_bytes()

        # The next day: nothing is downloaded, the failure is reported as known, not as new.
        report = self.run_sync()
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(report['failed'], [])
        self.assertEqual([f['skinId'] for f in report['knownFailures']], [BROKEN])
        self.assertEqual((self.root / 'sync-failures.json').read_bytes(), recorded)

    def test_a_new_bundle_new_code_or_an_explicit_request_retries_it(self):
        self.run_sync()
        self.downloads.clear()
        self.run_sync('--only', BROKEN)
        self.assertEqual(self.downloads, [BROKEN])
        self.run_sync('--retry-failed')
        self.assertEqual(self.downloads, [BROKEN, BROKEN])
        self.code = '000000000000'
        self.run_sync()
        self.assertEqual(self.downloads, [BROKEN] * 3)
        self.run_sync()
        self.assertEqual(self.downloads, [BROKEN] * 3)  # recorded again under the new code
        self.payloads[BROKEN] = b'broken bundle, fixed by the game'
        self.set_bundles()
        self.run_sync()
        self.assertEqual(self.downloads, [BROKEN] * 4)

    def test_success_clears_the_record_and_download_errors_are_never_recorded(self):
        self.run_sync()
        self.downloads.clear()
        self.download_error = urllib.error.URLError('timed out')
        self.run_sync('--retry-failed')
        self.assertEqual(self.failures()[BROKEN]['error'], 'SyncError: Expected one illustration skeleton, found 2')
        self.payloads[BROKEN] = b'a bundle that builds'
        self.set_bundles()
        self.download_error = None
        sync.build_model = lambda planned, bundle, res, staging, shaders, shared=None, *, particles: (write_model(planned, staging), ({}, [], None))[1]
        report = self.run_sync()
        self.assertEqual(report['added'], [BROKEN])
        self.assertEqual(self.failures(), {})

    def test_without_the_shared_shaders_nothing_is_built_or_recorded(self):
        self.shaders_error = l2d.SyncError('The client list has no [uc]shaders.ab')
        report = self.run_sync()
        self.assertEqual(self.downloads, [])
        self.assertEqual(report['added'], [])
        self.assertEqual(sorted(f['skinId'] for f in report['failed']), sorted([GOOD, BROKEN]))
        self.assertTrue(all('shared shaders unavailable' in f['error'] for f in report['failed']))
        self.assertFalse((self.root / 'sync-failures.json').exists(), 'it may work tomorrow')

    def test_local_bundles_are_checked_against_the_list_and_never_downloaded(self):
        local = self.root / 'bundles'
        local.mkdir()
        (local / 'char_1044_hsgma2_2.ab').write_bytes(self.payloads[GOOD])
        (local / 'char_003_kalts_boc_6.ab').write_bytes(b'broken bundlf')  # same size, other bytes
        report = self.run_sync('--bundles', str(local))
        self.assertEqual(self.downloads, [])
        self.assertEqual(report['added'], [GOOD])
        self.assertEqual([f['skinId'] for f in report['failed']], [BROKEN])
        self.assertIn('md5 does not match', report['failed'][0]['error'])
        self.assertFalse((self.root / 'sync-failures.json').exists(), 'a bad local copy is not the bundle failing')

    def test_an_older_folder_gets_only_its_layers_exported_again(self):
        from PIL import Image
        self.payloads.pop(BROKEN)
        self.set_bundles()
        self.run_sync()
        folder = next((self.root / 'models' / 'char_1044_hsgma2_2').iterdir())
        # As layersVersion 1 wrote it: a skeleton, a page and two layer textures.
        model = {'schemaVersion': 1, 'skinId': GOOD, 'skeleton': {'file': 'skeleton.skel'}, 'layers': {'file': 'layers.json', 'bytes': 2, 'sha256': 'x'},
                 'source': {'md5': hashlib.md5(self.payloads[GOOD]).hexdigest()}}
        (folder / 'model.json').write_text(json.dumps(model))
        for name, data in (('skeleton.skel', b'SKEL'), ('page0.webp', b'PAGE'), ('layers.json', b'{}'), ('layer0.webp', b'old'), ('layer1.webp', b'old')):
            (folder / name).write_bytes(data)
        before = (self.root / 'manifest.json').read_bytes()
        export = layers.LayerExport(
            document={'schemaVersion': 1, 'textures': [{'file': 'layer0.webp', 'width': 2, 'height': 2, 'wrap': ['clamp', 'clamp'], 'opaque': [0, 0, 1, 1]}],
                      'effectTextures': [], 'bounds': None, 'effectBounds': None, 'separators': [], 'draw': [{'part': 0}],
                      'omitted': {'particles': 0, 'trails': 0, 'skinned': 0, 'hidden': 0, 'holders': 0, 'custom': [], 'externalTexture': [], 'other': []}},
            textures=[Image.new('RGBA', (2, 2), (255, 0, 0, 255))], texture_info=[],
            counts={k: 0 for k in ('layers', 'effects', 'exact', 'static', 'animated', 'follow', 'only', 'states', 'parts')})
        decoded = []

        def fake_decode(bundle, dyn_illust_id, dyn_entrance_id, shaders, shared=None, *, particles):
            self.assertFalse(particles)
            decoded.append(bundle)
            self.assertIsNotNone(shared, 'effects may need the shared textures')
            return type('Decoded', (), {'layers': staticmethod(lambda slots: export)})()

        def fake_inspect(folder_, name):
            self.assertEqual((folder_ / 'skeleton.skel').read_bytes(), b'SKEL', 'read from the copy')
            return {'slots': ['a']} if name == 'skeleton' else {'bounds': {'x': 0, 'y': 0, 'width': 1, 'height': 1}, 'effectBounds': {'x': 0, 'y': 0, 'width': 2, 'height': 1}}
        for name, value in (('inspect', fake_inspect),):
            original = getattr(sync, name)
            setattr(sync, name, value)
            self.addCleanup(setattr, sync, name, original)
        original = l2d.decode_bundle
        l2d.decode_bundle = fake_decode
        self.addCleanup(setattr, l2d, 'decode_bundle', original)
        self.downloads.clear()
        report = self.run_sync()
        self.assertEqual((self.downloads, decoded, report['relayered'], report['added']), ([GOOD], [self.payloads[GOOD]], [GOOD], []))
        # Only the layers changed: the skeleton and page byte for byte, the old second texture gone.
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['layer0.webp', 'layers.json', 'model.json', 'page0.webp', 'skeleton.skel'])
        self.assertEqual(((folder / 'skeleton.skel').read_bytes(), (folder / 'page0.webp').read_bytes()), (b'SKEL', b'PAGE'))
        written = json.loads((folder / 'model.json').read_text())
        self.assertEqual(list(written), ['schemaVersion', 'skinId', 'skeleton', 'layers', 'layersVersion', 'source'])
        self.assertEqual(written['layersVersion'], layers.LAYERS_VERSION)
        doc = json.loads((folder / 'layers.json').read_text())
        self.assertEqual((doc['bounds']['width'], doc['effectBounds']['width'], doc['textures'][0]['bytes']), (1, 2, (folder / 'layer0.webp').stat().st_size))
        self.assertEqual((self.root / 'manifest.json').read_bytes(), before, 'the folder and the manifest entry stay')
        # Current now: nothing more to fetch.
        self.downloads.clear()
        self.run_sync()
        self.assertEqual(self.downloads, [])

    # --- the CN client -------------------------------------------------------------------

    def with_cn_only(self):
        """CN carries one more animated outfit, CN_ONLY, and its own build of GOOD (another md5)."""
        self.cn_skin_table = {'charSkins': {**self.skin_table['charSkins'], CN_ONLY: {'dynIllustId': 'dyn_illust_char_4179_monstr_boc#11'}}}
        self.cn_payloads = {CN_ONLY: b'cn only bundle', GOOD: b'good bundle, CN build'}

    def manifest(self):
        return json.loads((self.root / 'manifest.json').read_text())

    def test_cn_fills_only_the_skins_global_does_not_have(self):
        self.with_cn_only()
        report = self.run_sync()
        self.assertEqual(sorted(report['added']), sorted([GOOD, CN_ONLY]))
        # GOOD from Global's bundle though CN has its own; CN_ONLY from CN's client, at CN's resVersion.
        self.assertEqual(self.built_from, {GOOD: ('en', 'res-1'), BROKEN: ('en', 'res-1'), CN_ONLY: ('cn', 'cn-res-1')})
        self.assertIn(('cn', CN_ONLY), self.downloads)
        self.assertNotIn(('cn', GOOD), self.downloads)
        self.assertEqual(report['otherClients'], {CN_ONLY: 'cn'})
        manifest = self.manifest()
        self.assertEqual(manifest['models'][CN_ONLY], f'{l2d.folder_for(CN_ONLY, hashlib.md5(b"cn only bundle").hexdigest())}/model.json')
        self.assertEqual((manifest['server'], manifest['resVersion'], manifest['resVersions']), ('en', 'res-1', {'en': 'res-1', 'cn': 'cn-res-1'}))
        # The next day: nothing new from either client.
        self.downloads.clear()
        self.assertEqual(self.run_sync()['added'], [])
        self.assertEqual(self.downloads, [])

    def test_global_takes_a_skin_over_the_day_its_client_has_it(self):
        self.with_cn_only()
        self.run_sync()
        cn_target = self.manifest()['models'][CN_ONLY]
        self.skin_table['charSkins'][CN_ONLY] = {'dynIllustId': 'dyn_illust_char_4179_monstr_boc#11'}
        self.payloads[CN_ONLY] = b'cn only bundle, Global build'
        self.set_bundles()
        self.downloads.clear()
        report = self.run_sync()
        self.assertEqual((report['added'], self.downloads, self.built_from[CN_ONLY]), ([CN_ONLY], [CN_ONLY], ('en', 'res-1')))
        self.assertEqual(report['otherClients'], {})
        target = self.manifest()['models'][CN_ONLY]
        self.assertEqual(target, f'{l2d.folder_for(CN_ONLY, hashlib.md5(self.payloads[CN_ONLY]).hexdigest())}/model.json')
        self.assertTrue((self.root / cn_target).exists(), 'the CN folder stays published')

    def test_a_cn_model_that_failed_keeps_its_record_and_is_not_downloaded_again(self):
        self.with_cn_only()
        build = self.fake_build

        def failing(planned, *rest, **named):
            if planned.skin_id == CN_ONLY:
                raise l2d.SyncError('Expected one illustration skeleton, found 2')
            return build(planned, *rest, **named)
        sync.build_model = failing
        self.run_sync()
        self.assertEqual(self.failures()[CN_ONLY]['resVersion'], 'cn-res-1')
        # Global's list does not carry it, but CN's does: the record is kept, nothing downloaded.
        self.downloads.clear()
        report = self.run_sync()
        self.assertEqual(self.downloads, [])
        self.assertIn(CN_ONLY, [f['skinId'] for f in report['knownFailures']])
        self.assertIn(CN_ONLY, self.failures())

    def test_without_cn_shaders_only_the_cn_models_wait(self):
        self.with_cn_only()

        def shaders(asset_base, res_version, hot_update_list, bundles):
            if asset_base == CN_BASE:
                raise l2d.SyncError('The client list has no [uc]shaders.ab')
            return {('CAB-shaders', 1): 'a shader'}
        sync.load_shaders = shaders
        report = self.run_sync()
        self.assertEqual(report['added'], [GOOD])
        self.assertEqual([f['skinId'] for f in report['failed'] if 'shared shaders' in f['error']], [CN_ONLY])
        self.assertNotIn(CN_ONLY, self.failures(), 'it may work tomorrow')

    def test_a_dry_run_writes_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sync.main(['--dry-run']), 0)
        text = out.getvalue()
        report = json.loads(text[text.index('\n{') + 1:])
        self.assertEqual(self.downloads, [])
        self.assertEqual(len(report['deferred']) + len(text.split('would fetch')) - 1, 2)
        self.assertEqual(report['added'], [])
        self.assertFalse((self.root / 'sync-failures.json').exists())
        self.assertFalse((self.root / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
