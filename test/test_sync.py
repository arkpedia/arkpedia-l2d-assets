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
        patches = {
            'ROOT': self.root,
            'code_version': lambda: self.code,
            'get_json': lambda url: self.skin_table,
            'client_list': lambda: ('https://cdn.example/assetbundle/official', 'res-1', self.hot_update_list),
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
        names = {GOOD: 'arts/dynchars/char_1044_hsgma2_2.ab', BROKEN: 'arts/dynchars/char_003_kalts_boc#6.ab'}
        self.hot_update_list = {'abInfos': [
            {'name': names[skin], 'md5': hashlib.md5(self.payloads[skin]).hexdigest(),
             'totalSize': len(self.dats[skin]), 'abSize': len(self.payloads[skin])}
            for skin in self.payloads
        ]}
        self.by_url = {l2d.download_name(names[skin]): skin for skin in self.payloads}

    def fake_get(self, url):
        if self.download_error:
            raise self.download_error
        skin = self.by_url[url.rsplit('/', 1)[-1]]
        self.downloads.append(skin)
        return self.dats[skin]

    def fake_shaders(self, asset_base, res_version, hot_update_list, bundles):
        if self.shaders_error:
            raise self.shaders_error
        return {('CAB-shaders', 1): 'a shader'}

    def fake_build(self, planned, bundle, res_version, staging, shaders, shared=None):
        self.assertEqual(bundle, self.payloads[planned.skin_id])  # verified and unpacked first
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
        sync.build_model = lambda planned, bundle, res, staging, shaders, shared=None: (write_model(planned, staging), ({}, [], None))[1]
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

        def fake_decode(bundle, dyn_illust_id, dyn_entrance_id, shaders, shared=None):
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
