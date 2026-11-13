"""Player media integrity and native curve conversion regression checks."""
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from event_player_manifest import mp3_duration
from build_event_player import Builder


class EventPlayerTest(unittest.TestCase):
    def test_mp3_duration_counts_frames_and_rejects_truncation(self):
        # MPEG-1 Layer III, 192k, 48kHz: 576 bytes, 1152 samples.
        frame = bytes.fromhex('ff fb b4 00') + bytes(572)
        self.assertAlmostEqual(mp3_duration(frame * 250), 6)
        self.assertAlmostEqual(mp3_duration(b'ID3\x04\x00\x00\x00\x00\x00\x00' + frame), .024)
        for corrupt in (frame[:-1], b'not audio', b''):
            with self.assertRaises(AssertionError):
                mp3_duration(corrupt)

    def test_original_stepped_and_weighted_curve_keys_are_preserved(self):
        keys = [{'time': .25, 'value': 7, 'inSlope': math.inf, 'outSlope': -math.inf,
                 'weightedMode': 3, 'inWeight': .15, 'outWeight': .7}]
        self.assertEqual(Builder.keys(keys), [[.25, 7, None, None, 3, .15, .7]])
        self.assertEqual(Builder.keys([{'time': 0, 'value': {'x': 2}, 'inSlope': {'x': 3},
                                       'outSlope': {'x': 4}}], 'x'), [[0, 2, 3, 4, 0, 1/3, 1/3]])


if __name__ == '__main__':
    unittest.main()
