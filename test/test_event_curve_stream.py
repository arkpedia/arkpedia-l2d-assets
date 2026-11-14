import math
import struct
import unittest
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from event_curve_stream import stream_channels


def clip(frames, constants=(), count=1):
    binary = b''.join(struct.pack('<fi', time, len(keys)) + b''.join(struct.pack('<i4f', *key) for key in keys) for time, keys in frames)
    return {'m_StreamedClip': {'curveCount': count, 'data': list(struct.unpack(f'<{len(binary)//4}I', binary))},
            'm_DenseClip': {'m_CurveCount': 0}, 'm_ConstantClip': {'data': list(constants)}}


def sample(a, b, time):
    t = (time - a[0]) / (b[0] - a[0])
    if a[3] is None or b[2] is None:
        return a[1]
    return (2*t**3-3*t*t+1)*a[1]+(t**3-2*t*t+t)*(b[0]-a[0])*a[3]+(-2*t**3+3*t*t)*b[1]+(t**3-t*t)*(b[0]-a[0])*b[2]


class StreamTests(unittest.TestCase):
    def test_archived_mecanim_polynomials_match_between_every_pair_of_keys(self):
        root = Path(__file__).resolve().parents[1] / 'event-transitions/events/cn'
        checked = 0
        for folder, name in [('act43sre/2e0aacd4fc75', 'act43sre_entry'), ('act40sre/f56b6151f4dc', 'panel_act40side_entry_in')]:
            archive = json.loads(gzip.decompress((root / folder / 'animations.json.gz').read_bytes()))
            source = next(c['data'] for c in archive['clips'] if c['data']['m_Name'] == name)
            data = source['m_MuscleClip']['m_Clip']['data']
            result = stream_channels(data)
            words = data['m_StreamedClip']['data']
            binary, cursor = struct.pack(f'<{len(words)}I', *words), 0
            originals = [dict() for _ in range(data['m_StreamedClip']['curveCount'])]
            while cursor < len(binary):
                time, size = struct.unpack_from('<fi', binary, cursor)
                cursor += 8
                for _ in range(size):
                    index, *coeff = struct.unpack_from('<i4f', binary, cursor)
                    cursor += 20
                    if time == -math.inf or math.isfinite(time):
                        originals[index][max(0, time)] = coeff
            for index, channel in enumerate(originals):
                points = sorted(channel.items())
                for key, ((start, (a, b, c, d)), (end, _)) in enumerate(zip(points, points[1:])):
                    for fraction in (.2, .5, .8):
                        dt = (end - start) * fraction
                        expected = ((a * dt + b) * dt + c) * dt + d
                        actual = sample(result[index][key], result[index][key + 1], start + dt)
                        self.assertTrue(math.isclose(actual, expected, rel_tol=1e-4, abs_tol=1e-4), (folder, index, start, actual, expected))
                        checked += 1
        self.assertGreater(checked, 1000)

    def test_native_polynomial_matches_hermite_between_keys(self):
        data = clip([(-math.inf, [(0, 0, 0, 0, 8)]), (0, [(0, 2, -3, 0, 1)]), (1, [(0, 0, 0, 0, 0)])], [7])
        channels = stream_channels(data)
        for i in range(11):
            t = i / 10
            self.assertAlmostEqual(sample(*channels[0], t), 2*t**3 - 3*t*t + 1)
        self.assertEqual(channels[1][0][1], 7)
        self.assertEqual(channels[0][0][1], 1)  # Real zero key overrides sentinel.

    def test_steps_hold_until_the_next_native_key(self):
        keys = stream_channels(clip([(0, [(0, 0, 0, 0, 1)]), (1, [(0, 0, 0, 0, 3)])]))[0]
        self.assertEqual(sample(*keys, .999), 1)
        self.assertEqual(keys[1][1], 3)

    def test_a_nonzero_clip_origin_preserves_the_source_polynomial(self):
        keys = stream_channels(clip([(0, [(0, 0, 1, 0, 0)]), (1, [(0, 0, 0, 0, 1)])]), start=.5)[0]
        self.assertAlmostEqual(keys[0][1], .25)
        self.assertAlmostEqual(sample(*keys, .25), .75**2)

    def test_negative_infinity_supplies_an_initial_value(self):
        keys = stream_channels(clip([(-math.inf, [(0, 0, 0, 0, 2)])]))[0]
        self.assertEqual(keys[0][:2], [0, 2])

    def test_unknown_dense_and_invalid_indices_fail_closed(self):
        data = clip([(0, [(1, 0, 0, 0, 2)])])
        with self.assertRaises(ValueError):
            stream_channels(data)
        data['m_DenseClip']['m_CurveCount'] = 1
        with self.assertRaises(ValueError):
            stream_channels(data)


if __name__ == '__main__':
    unittest.main()
