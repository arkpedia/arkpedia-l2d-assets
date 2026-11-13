"""Checks for archival fidelity, release discovery and corrupt-pack rejection."""
import gzip
import hashlib
import io
import json
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import event_transitions as et


class EventTransitionsTest(unittest.TestCase):
    def test_unity_pointers_and_infinite_tangents_survive_json(self):
        source = {'m_PathID': 9007199254740993, 'm_FileID': 0,
                  'curve': [{'inSlope': math.inf, 'outSlope': -math.inf}], 'raw': b'\x00\xff'}
        result = json.loads(json.dumps(et.portable(source), allow_nan=False))
        self.assertEqual(result['m_PathID'], '9007199254740993')
        self.assertEqual(result['m_FileID'], 0)
        self.assertEqual(result['curve'][0]['inSlope'], {'$float': 'inf'})
        self.assertEqual(result['curve'][0]['outSlope'], {'$float': '-inf'})
        self.assertEqual(result['raw'], {'$bytesBase64': 'AP8='})

    def test_test_upload_cannot_override_a_released_bundle(self):
        sources = [{'server': 'cn', 'resVersion': v} for v in ('24-02-20-01-00-00_a', 'TEST-24-02-20-01-00-00_a', '26-11-06-01-00-00_a')]
        with patch.object(et, 'manifest_sources', return_value=sources), patch.object(et, 'load_manifest',
                side_effect=lambda s: {'abInfos': [{'name': 'activity/[uc]act31side.ab', 'md5': s['resVersion']}]}):
            plans, errors = et.discover()
        self.assertEqual(errors, [])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]['source']['resVersion'], '26-11-06-01-00-00_a')

    def test_candidate_is_not_a_playability_claim_or_an_idle_loop(self):
        self.assertTrue(et.candidate('act42side_entry_stage_anim_entry'))
        self.assertTrue(et.candidate('act21side_anim_enter'))
        self.assertFalse(et.candidate('act42side_entry_stage_trans_loop'))
        self.assertFalse(et.candidate('act42side_entry_stage_exit'))
        self.assertFalse(et.candidate('act6fun_home_entry_out'))

    def test_streamed_clip_duration_uses_its_authored_stop_time(self):
        record = et.animation_info('123', {'m_Name': 'act31sre_stage_entry',
                                         'm_MuscleClip': {'m_StopTime': 2.5}})
        self.assertEqual(record['durationSeconds'], 2.5)

    def test_validate_checks_the_original_bundle_and_exported_curve_hash(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(et, 'OUT', Path(tmp)):
            folder = Path(tmp) / 'events/en/act42side/hash'
            folder.mkdir(parents=True)
            original = b'preserved Unity bundle bytes'
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                archive.writestr('activity.ab', original)
            (folder / 'bundle.dat').write_bytes(data.getvalue())
            et.write_json(folder / 'animations.json.gz', {'clips': [{'id': '123', 'data': {}}]})
            (folder / 'scene.json.gz').write_bytes(gzip.compress(json.dumps({'objects': [{'id': '123', 'type': 'AnimationClip'}]}).encode()))
            files = [{'file': p.name, 'bytes': p.stat().st_size, 'sha256': et.digest(p.read_bytes())} for p in folder.iterdir()]
            et.write_json(folder / 'asset.json', {'browserPlayable': False, 'status': 'source-recovered', 'server': 'en',
                'activityId': 'act42side', 'animations': [{'id': '123', 'name': 'entry', 'candidate': True}], 'textures': [],
                'source': {'archiveSha256': et.digest(data.getvalue()), 'bundleMd5': hashlib.md5(original).hexdigest(),
                           'bundleSha256': et.digest(original)}, 'files': files})
            et.write_json(Path(tmp) / 'manifest.json', {'schemaVersion': 1, 'browserPlayable': False, 'assets': [{
                'server': 'en', 'activityId': 'act42side', 'candidates': ['entry'], 'asset': 'events/en/act42side/hash/asset.json'}]})
            et.validate()
            (folder / 'animations.json.gz').write_bytes(b'corrupt export')
            with self.assertRaises(AssertionError):
                et.validate()


if __name__ == '__main__':
    unittest.main()
