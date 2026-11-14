"""Decode Unity's data-only streamed scalar polynomials into Hermite keys."""
import math
import struct


def stream_channels(data, start=0.0):
    stream = data['m_StreamedClip']
    if data['m_DenseClip']['m_CurveCount']:
        raise ValueError('Dense Mecanim channels need separate verification')
    count = stream['curveCount']
    channels = [[] for _ in range(count)]
    words = stream['data']
    binary = struct.pack(f'<{len(words)}I', *words)
    cursor = 0
    while cursor < len(binary):
        time, size = struct.unpack_from('<fi', binary, cursor)
        cursor += 8
        if size < 0 or cursor + size * 20 > len(binary):
            raise ValueError('Invalid streamed animation frame')
        for _ in range(size):
            index, *coeff = struct.unpack_from('<i4f', binary, cursor)
            cursor += 20
            if not 0 <= index < count:
                raise ValueError('Invalid streamed channel index')
            if time == -math.inf or math.isfinite(time):
                if math.isfinite(time) and time < start:
                    a, b, c, d = coeff
                    dt = start - time
                    coeff = [a, b + 3*a*dt, c + 2*b*dt + 3*a*dt*dt, ((a*dt + b)*dt + c)*dt + d]
                channels[index].append((max(start, time), coeff))
    output = []
    for channel in channels:
        # The -infinity frame establishes defaults; a real start key overrides it.
        points = dict(channel)
        if not points:
            raise ValueError('Empty streamed animation channel')
        points = sorted(points.items())
        keys = [[time - start, c[3], 0, c[2], 0, 1/3, 1/3] for time, c in points]
        for i, ((time, (a, b, c, d)), (following, next_coeff)) in enumerate(zip(points, points[1:])):
            dt = following - time
            predicted = ((a * dt + b) * dt + c) * dt + d
            if not math.isclose(predicted, next_coeff[3], rel_tol=1e-4, abs_tol=1e-4):
                # A discontinuity holds the outgoing value until the next key.
                if a or b or c:
                    raise ValueError('Nonconstant discontinuous streamed polynomial')
                keys[i][3] = keys[i + 1][2] = None
            else:
                keys[i + 1][2] = (3 * a * dt + 2 * b) * dt + c
        output.append(keys)
    for value in data['m_ConstantClip']['data']:
        output.append([[0, value, 0, 0, 0, 1/3, 1/3]])
    return output
