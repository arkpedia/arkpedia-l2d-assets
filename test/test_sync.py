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
import sync  # noqa: E402

GOOD = 'char_1044_hsgma2#2'
BROKEN = 'char_003_kalts@boc#6'


def write_model(planned, staging):
    """The two fields the run loop reads back from an existing folder."""
    (staging / 'model.json').write_text(json.dumps({'skinId': planned.skin_id, 'source': {'md5': planned.md5}}))


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
        }
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

    def fake_build(self, planned, bundle, res_version, staging):
        self.assertEqual(bundle, self.payloads[planned.skin_id])  # verified and unpacked first
        if planned.skin_id == BROKEN:
            raise l2d.SyncError('Expected one illustration skeleton, found 2')
        write_model(planned, staging)
        return {}, [{'page': 'p.png', 'mask': False, 'alpha': 'straight', 'transparentColour': 150.0,
                     'semiColourAboveAlpha': 0.9}]

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
        sync.build_model = lambda planned, bundle, res, staging: (write_model(planned, staging), ({}, []))[1]
        report = self.run_sync()
        self.assertEqual(report['added'], [BROKEN])
        self.assertEqual(self.failures(), {})

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
