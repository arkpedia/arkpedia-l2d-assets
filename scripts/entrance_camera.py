"""The camera moves and full-screen fades of an entrance sequence, read from its Unity objects.

The game plays an entrance through its own camera: the entrance prefab's controller (the
MonoBehaviour with `_mainCamera`) names a Camera somewhere in the prefab, and Animators move it
(position, rotation, scale of any transform on its chain) and zoom it (`orthographic size`, or
`field of view` for a perspective camera). Cuts are hidden under full-screen quads whose
material colour the same Animators fade (black or white `_TintColor`). This module decodes those
Mecanim clips (streamed, dense and constant curves, as AssetStudio reads them) and evaluates the
whole transform chain, so the camera comes out in the skeleton's own coordinates: centre x, y and
the visible height, over time. The site draws the entrance with that camera and the fades as
overlays. Particle systems and other effects are not reproduced.

Pure functions over typetrees (`read(path_id) -> (type name, typetree) | None`); unit tested in
test/test_camera.py. Anything that cannot be decoded raises CameraError, so the sync fails the
model instead of shipping an entrance without the camera the game gives it.
"""
from __future__ import annotations

import bisect
import math
import struct
import zlib

FPS = 30
# Decimation: a sample is dropped when the straight line between its kept neighbours stays this
# close to it (skeleton units for the camera, alpha for fades).
CAMERA_TOLERANCE = 0.5
ROLL_TOLERANCE = 0.1
ALPHA_TOLERANCE = 0.005
# A quad counts as full screen when it covers a view this many times as wide as it is high (wider
# than any screen the site draws on), at every moment it is visible.
COVER_ASPECT = 2.4
NEG_INFINITY = -3.0e38

TRANSFORM, GAMEOBJECT, CAMERA, RENDERER = 4, 1, 20, 23
POSITION, ROTATION, SCALE, EULER = 1, 2, 3, 4
CHANNELS = {4: 0, 5: 1, 6: 2, 7: 3}  # a material colour binding's top four bits: r, g, b, a


class CameraError(Exception):
    """The entrance has a camera this module cannot reproduce."""


def crc(text: str) -> int:
    return zlib.crc32(text.encode('utf-8')) & 0xFFFFFFFF


ORTHOGRAPHIC_SIZE = crc('orthographic size')
FIELD_OF_VIEW = crc('field of view')
IS_ACTIVE = crc('m_IsActive')
ENABLED = crc('m_Enabled')


def material_binding(attribute: int) -> tuple[int, int | None]:
    """A material property binding's attribute: (low 28 bits of crc32(property name), channel
    0-3 for r, g, b, a, or None for a plain float)."""
    return attribute & 0x0FFFFFFF, CHANNELS.get(attribute >> 28)


# ---------------------------------------------------------------------------
# Clips


def streamed_frames(words: list[int]) -> list[tuple[float, list[tuple[int, tuple[float, float, float, float]]]]]:
    """A streamed clip's frames: (time, [(curve index, cubic coefficients)]). The data is a list of
    uint32 words; each frame is a float time, an int key count, then per key an int curve index and
    four floats. The first frame sits at -FLT_MAX, the last at +inf."""
    data = struct.pack(f'<{len(words)}I', *words)
    pos, frames = 0, []
    while pos < len(data):
        if pos + 8 > len(data):
            raise CameraError('Streamed clip ends inside a frame header')
        time, count = struct.unpack_from('<fi', data, pos)
        pos += 8
        if count < 0 or pos + count * 20 > len(data):
            raise CameraError('Streamed clip ends inside a frame')
        keys = []
        for _ in range(count):
            index, = struct.unpack_from('<i', data, pos)
            keys.append((index, struct.unpack_from('<4f', data, pos + 4)))
            pos += 20
        frames.append((time, keys))
    return frames


class StreamedCurve:
    """One streamed curve: from each key's time to the next, value = ((c0 dt + c1) dt + c2) dt + c3."""

    def __init__(self):
        self.times: list[float] = []
        self.coefficients: list[tuple[float, float, float, float]] = []

    def value(self, t: float) -> float:
        if not self.times:
            raise CameraError('A streamed curve has no keys')
        i = max(0, bisect.bisect_right(self.times, t) - 1)
        dt = t - self.times[i] if self.times[i] > NEG_INFINITY else 0.0
        c0, c1, c2, c3 = self.coefficients[i]
        return ((c0 * dt + c1) * dt + c2) * dt + c3


class DenseCurve:
    def __init__(self, samples: list[float], rate: float, begin: float):
        self.samples, self.rate, self.begin = samples, rate, begin

    def value(self, t: float) -> float:
        position = (t - self.begin) * self.rate
        last = len(self.samples) - 1
        i = max(0, min(last, int(math.floor(position))))
        j = min(last, i + 1)
        a = min(max(position - i, 0.0), 1.0)
        return self.samples[i] * (1 - a) + self.samples[j] * a


class ConstantCurve:
    def __init__(self, value: float):
        self.constant = value

    def value(self, t: float) -> float:
        return self.constant


def curve_count(binding: dict) -> int:
    """How many curves a generic binding takes: a transform's position, scale or euler 3, its
    rotation 4 (a quaternion), anything else 1."""
    if binding.get('typeID') == TRANSFORM:
        return {POSITION: 3, ROTATION: 4, SCALE: 3, EULER: 3}.get(binding.get('attribute'), 1)
    return 1


class Clip:
    """A decoded generic (Mecanim) clip: [(binding, [curve, ...])] in binding order."""

    def __init__(self, tree: dict):
        self.name = tree.get('m_Name', '')
        if tree.get('m_Legacy'):
            raise CameraError(f'Clip {self.name} is a legacy clip, which this does not read')
        muscle = tree['m_MuscleClip']
        data = muscle['m_Clip']['data']
        streamed = data['m_StreamedClip']
        curves: list = [StreamedCurve() for _ in range(streamed['curveCount'])]
        for time, keys in streamed_frames(streamed['data']):
            if time == math.inf:
                continue
            for index, coefficients in keys:
                if not 0 <= index < len(curves):
                    raise CameraError(f'Clip {self.name}: key for curve {index} of {len(curves)}')
                curves[index].times.append(time)
                curves[index].coefficients.append(coefficients)
        dense = data['m_DenseClip']
        count, frames, samples = dense['m_CurveCount'], dense['m_FrameCount'], dense['m_SampleArray']
        if count and len(samples) < count * frames:
            raise CameraError(f'Clip {self.name}: dense samples missing')
        for c in range(count):
            curves.append(DenseCurve([samples[f * count + c] for f in range(frames)], dense['m_SampleRate'], dense['m_BeginTime']))
        curves += [ConstantCurve(v) for v in data['m_ConstantClip']['data']]
        self.bindings: list[tuple[dict, list]] = []
        used = 0
        for binding in tree['m_ClipBindingConstant']['genericBindings']:
            size = curve_count(binding)
            self.bindings.append((binding, curves[used:used + size]))
            used += size
        if used != len(curves):
            raise CameraError(f'Clip {self.name}: its bindings take {used} curves, it has {len(curves)}')
        self.start = muscle.get('m_StartTime', 0.0)
        self.stop = muscle.get('m_StopTime', 0.0)
        self.loop = bool(muscle.get('m_LoopTime'))

    def time(self, t: float) -> float:
        """Entrance time -> clip time: held at the end, or wrapped for a looping clip."""
        length = self.stop - self.start
        if self.loop and length > 0:
            return self.start + (t % length)
        return self.start + min(max(t, 0.0), max(length, 0.0))


# ---------------------------------------------------------------------------
# Transforms: quaternions (x, y, z, w) and affine matrices as 3x4 row lists


def quaternion_multiply(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def normalized(q):
    n = math.sqrt(sum(v * v for v in q))
    if not n:
        raise CameraError('A rotation curve gives a zero quaternion')
    return tuple(v / n for v in q)


def euler_degrees(angles):
    """Unity's euler angles (degrees), applied Z, then X, then Y."""
    x, y, z = (math.radians(v) / 2 for v in angles)
    qx, qy, qz = (math.sin(x), 0.0, 0.0, math.cos(x)), (0.0, math.sin(y), 0.0, math.cos(y)), (0.0, 0.0, math.sin(z), math.cos(z))
    return quaternion_multiply(quaternion_multiply(qy, qx), qz)


def trs(position, rotation, scale):
    x, y, z, w = rotation
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[i][0] * scale[0], r[i][1] * scale[1], r[i][2] * scale[2], position[i]] for i in range(3)]


IDENTITY = trs((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) + (a[i][3] if j == 3 else 0.0) for j in range(4)] for i in range(3)]


def inverse(m):
    r = [row[:3] for row in m]
    t = [row[3] for row in m]
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1]) - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    if abs(det) < 1e-12:
        raise CameraError('A transform on the camera chain has zero scale')
    inv = [[(r[(j + 1) % 3][(i + 1) % 3] * r[(j + 2) % 3][(i + 2) % 3] - r[(j + 1) % 3][(i + 2) % 3] * r[(j + 2) % 3][(i + 1) % 3]) / det
            for j in range(3)] for i in range(3)]
    return [inv[i] + [-sum(inv[i][k] * t[k] for k in range(3))] for i in range(3)]


def transform_point(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) + m[i][3] for i in range(3))


def transform_direction(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def length(v):
    return math.sqrt(sum(x * x for x in v))


# ---------------------------------------------------------------------------
# Sampling


def decimate(samples: list[list[float]], tolerances: list[float]) -> list[list[float]]:
    """Drops samples a straight line between their kept neighbours reproduces within `tolerances`
    (one per value after the time). Keeps the first and the last."""
    if len(samples) <= 2:
        return samples
    kept = [samples[0]]
    anchor = 0
    end = 2
    while end < len(samples):
        a, b = samples[anchor], samples[end]
        span = b[0] - a[0]
        fits = True
        for mid in samples[anchor + 1:end]:
            f = (mid[0] - a[0]) / span if span else 0.0
            if any(abs(a[k] + (b[k] - a[k]) * f - mid[k]) > tolerances[k - 1] for k in range(1, len(mid))):
                fits = False
                break
        if fits:
            end += 1
        else:
            anchor = end - 1
            kept.append(samples[anchor])
            end = anchor + 2
    kept.append(samples[-1])
    return kept


# ---------------------------------------------------------------------------
# The entrance


class _Scene:
    """The entrance prefab's hierarchy and every Animator's clip bindings, evaluated over time."""

    def __init__(self, root_go: int, read):
        self.read = read
        self.parent: dict[int, int | None] = {}
        self.path: dict[int, str] = {}
        self.go_of: dict[int, int] = {}
        self.transform_of: dict[int, int] = {}
        root_transform = self.component(root_go, ('Transform', 'RectTransform'))
        if root_transform is None:
            raise CameraError('The entrance prefab has no root transform')
        self._walk(root_transform, '', None)
        # Every clip's curves for each animated property. Several clips of one Animator may animate
        # the same property (its states); which plays is not decided here, so using such a property
        # for the camera or a fade fails (curves()), and anything else is left alone.
        self.transform_curves: dict[tuple[int, int], list[tuple[Clip, list]]] = {}
        self.float_curves: dict[tuple[int, int, int], list[tuple[Clip, list]]] = {}
        self._bind_animators()

    def tree(self, path_id: int, *kinds: str) -> dict:
        entry = self.read(path_id)
        if not entry or (kinds and entry[0] not in kinds):
            raise CameraError(f'Object {path_id} is not a {"/".join(kinds) or "readable object"}')
        return entry[1]

    def components(self, go: int) -> list[tuple[str, int]]:
        out = []
        for component in self.tree(go, 'GameObject').get('m_Component') or []:
            ref = (component.get('component') or component.get('second') or {}) if isinstance(component, dict) else {}
            path_id = ref.get('m_PathID') if ref.get('m_FileID', 0) == 0 else None
            entry = self.read(path_id) if path_id else None
            if entry:
                out.append((entry[0], path_id))
        return out

    def component(self, go: int, kinds) -> int | None:
        return next((path_id for kind, path_id in self.components(go) if kind in kinds), None)

    def _walk(self, transform: int, path: str, parent: int | None):
        tree = self.tree(transform, 'Transform', 'RectTransform')
        go = tree['m_GameObject']['m_PathID']
        self.parent[transform] = parent
        self.path[transform] = path
        self.go_of[transform] = go
        self.transform_of[go] = transform
        for child in tree.get('m_Children') or []:
            child_tree = self.tree(child['m_PathID'], 'Transform', 'RectTransform')
            name = self.tree(child_tree['m_GameObject']['m_PathID'], 'GameObject')['m_Name']
            self._walk(child['m_PathID'], f'{path}/{name}' if path else name, transform)

    def _bind_animators(self):
        for transform, prefix in list(self.path.items()):
            animator = self.component(self.go_of[transform], ('Animator',))
            if animator is None:
                continue
            controller_ref = self.tree(animator).get('m_Controller') or {}
            if not controller_ref.get('m_PathID') or controller_ref.get('m_FileID', 0) != 0:
                continue
            controller = self.tree(controller_ref['m_PathID'])
            refs = controller.get('m_AnimationClips') or []
            # A controller with several clips (states) starts in its default state: only that
            # state's clips play while the entrance does. Unreadable: all, so a conflict fails.
            playing = default_state_clips(controller, len(refs)) if len(refs) > 1 else None
            clips = []
            for index, ref in enumerate(refs):
                if playing is not None and index not in playing:
                    continue
                if ref.get('m_FileID', 0) == 0 and ref.get('m_PathID') and ref['m_PathID'] not in [c[0] for c in clips]:
                    clips.append((ref['m_PathID'], Clip(self.tree(ref['m_PathID'], 'AnimationClip'))))
            under = {crc(''): transform}
            for other, path in self.path.items():
                if prefix == '' and other != transform:
                    under[crc(path)] = other
                elif path.startswith(prefix + '/'):
                    under[crc(path[len(prefix) + 1:])] = other
            for _, clip in clips:
                for binding, curves in clip.bindings:
                    target = under.get(binding['path'])
                    if target is None:
                        continue
                    if binding['typeID'] == TRANSFORM:
                        self.transform_curves.setdefault((target, binding['attribute']), []).append((clip, curves))
                    else:
                        self.float_curves.setdefault((target, binding['typeID'], binding['attribute']), []).append((clip, curves))

    def curves(self, table: dict, key) -> tuple[Clip, list] | None:
        found = table.get(key)
        if not found:
            return None
        if len(found) > 1:
            raise CameraError(f'Several clips ({", ".join(clip.name for clip, _ in found)}) animate '
                              f'{self.path[key[0]] or "the root"}, which the camera or a fade uses')
        return found[0]

    def float_value(self, transform: int, type_id: int, attribute: int, t: float, default: float) -> float:
        found = self.curves(self.float_curves, (transform, type_id, attribute))
        if not found:
            return default
        clip, curves = found
        return curves[0].value(clip.time(t))

    def local(self, transform: int, t: float):
        tree = self.tree(transform, 'Transform', 'RectTransform')
        position = tuple(tree['m_LocalPosition'][k] for k in 'xyz')
        rotation = tuple(tree['m_LocalRotation'][k] for k in 'xyzw')
        scale = tuple(tree['m_LocalScale'][k] for k in 'xyz')
        for attribute in (POSITION, ROTATION, EULER, SCALE):
            found = self.curves(self.transform_curves, (transform, attribute))
            if not found:
                continue
            clip, curves = found
            values = tuple(curve.value(clip.time(t)) for curve in curves)
            if attribute == POSITION:
                position = values
            elif attribute == ROTATION:
                rotation = normalized(values)
            elif attribute == EULER:
                rotation = euler_degrees(values)
            else:
                scale = values
        return trs(position, rotation, scale)

    def chain(self, transform: int) -> list[int]:
        links = []
        while transform is not None:
            links.append(transform)
            transform = self.parent[transform]
        return links[::-1]

    def world(self, transform: int, t: float):
        """The full matrix: where an object and its children are (parent scales included)."""
        m = IDENTITY
        for link in self.chain(transform):
            m = multiply(m, self.local(link, t))
        return m

    def rotation(self, transform: int, t: float):
        """The world rotation alone, as Unity's transform.rotation (scale left out): a camera
        looks along it whatever its scale, and exported rigs often carry a zero scale."""
        q = (0.0, 0.0, 0.0, 1.0)
        for link in self.chain(transform):
            tree = self.tree(link, 'Transform', 'RectTransform')
            local = tuple(tree['m_LocalRotation'][k] for k in 'xyzw')
            for attribute in (ROTATION, EULER):
                found = self.curves(self.transform_curves, (link, attribute))
                if found:
                    clip, curves = found
                    values = tuple(curve.value(clip.time(t)) for curve in curves)
                    local = normalized(values) if attribute == ROTATION else euler_degrees(values)
            q = quaternion_multiply(q, local)
        return q


def default_state_clips(controller: dict, clip_count: int) -> list[int] | None:
    """Indexes into m_AnimationClips of the clips the controller's first layer starts in (its
    state machine's default state), or None when the controller's structure cannot be read."""
    try:
        constant = controller['m_Controller']
        layer = constant['m_LayerArray'][0]['data']
        machine = constant['m_StateMachineArray'][layer['m_StateMachineIndex']]['data']
        state = machine['m_StateConstantArray'][machine['m_DefaultState']]['data']
        ids = [node['data']['m_ClipID'] for tree in state['m_BlendTreeConstantArray'] for node in tree['data']['m_NodeArray']]
    except (KeyError, IndexError, TypeError):
        return None
    ids = [i for i in ids if isinstance(i, int) and 0 <= i < clip_count]
    return ids or None


def _material_colour(scene: _Scene, renderer: int, property_hash: int) -> tuple[str, tuple[float, float, float, float]]:
    """The name and saved value of the colour property a binding hash names on a renderer's materials."""
    for ref in scene.tree(renderer).get('m_Materials') or []:
        if not ref.get('m_PathID') or ref.get('m_FileID', 0) != 0:
            continue
        material = scene.tree(ref['m_PathID'])
        for entry in material.get('m_SavedProperties', {}).get('m_Colors') or []:
            name, value = (entry[0], entry[1]) if isinstance(entry, (list, tuple)) else (entry.get('first'), entry.get('second'))
            if isinstance(name, str) and crc(name) & 0x0FFFFFFF == property_hash:
                return name, (value['r'], value['g'], value['b'], value['a'])
    raise CameraError(f'A faded renderer animates a colour its materials do not have ({property_hash})')


def entrance_camera(root_go: int, skeleton_data: int, read, duration: float) -> dict | None:
    """The camera (frames [t, centre x, centre y, visible height, roll] in skeleton units and
    degrees) and full-screen
    fades ([{color, keys: [[t, alpha]]}]) of an entrance, sampled at FPS over [0, duration].

    `root_go` is the entrance prefab's root GameObject, `skeleton_data` the SkeletonDataAsset its
    skeleton plays. None when the prefab's controller names no camera; CameraError when it names
    one this cannot reproduce (a tilted or rolled camera, a camera outside the prefab...).
    """
    scene = _Scene(root_go, read)
    controller = None
    for kind, path_id in scene.components(root_go):
        if kind == 'MonoBehaviour':
            tree = scene.tree(path_id)
            if '_mainCamera' in tree:
                controller = tree
    camera_ref = ((controller or {}).get('_mainCamera') or {}).get('camera') or {}
    if not camera_ref.get('m_PathID'):
        return None
    if camera_ref.get('m_FileID', 0) != 0:
        raise CameraError('The entrance camera is in another file')
    camera = scene.tree(camera_ref['m_PathID'], 'Camera')
    camera_transform = scene.transform_of.get(camera['m_GameObject']['m_PathID'])
    if camera_transform is None:
        raise CameraError('The entrance camera is not inside the entrance prefab')

    skeleton_go = None
    for transform, go in scene.go_of.items():
        for kind, path_id in scene.components(go):
            if kind == 'MonoBehaviour' and (scene.tree(path_id).get('skeletonDataAsset') or {}).get('m_PathID') == skeleton_data:
                skeleton_go = skeleton_go or go
    if skeleton_go is None:
        raise CameraError('No object in the entrance prefab plays its skeleton')
    skeleton_transform = scene.transform_of[skeleton_go]
    unit = scene.tree(skeleton_data).get('scale')
    if not isinstance(unit, (int, float)) or unit <= 0:
        raise CameraError('The entrance skeleton has no scale')

    orthographic = bool(camera.get('orthographic'))
    times = [round(i / FPS, 4) for i in range(int(math.floor(duration * FPS)) + 1)]
    if times[-1] < duration:
        times.append(round(duration, 4))

    def view(t: float):
        """The camera in the skeleton object's space at t: its position and visible height (that
        object's units) and its roll (degrees: the screen's up turned from the skeleton's +y)."""
        skeleton_world = scene.world(skeleton_transform, t)
        position = transform_point(inverse(skeleton_world), transform_point(scene.world(camera_transform, t), (0.0, 0.0, 0.0)))
        skeleton_rotation = scene.rotation(skeleton_transform, t)
        conjugate = (-skeleton_rotation[0], -skeleton_rotation[1], -skeleton_rotation[2], skeleton_rotation[3])
        relative = quaternion_multiply(conjugate, scene.rotation(camera_transform, t))
        axes = trs((0.0, 0.0, 0.0), relative, (1.0, 1.0, 1.0))
        forward = transform_direction(axes, (0.0, 0.0, 1.0))
        up = transform_direction(axes, (0.0, 1.0, 0.0))
        if forward[2] < 0.9999:
            raise CameraError(f'The entrance camera turns away from the skeleton at {t:.2f}s (forward {forward}); '
                              'only straight-on cameras are drawn')
        roll = math.degrees(math.atan2(-up[0], up[1]))
        if orthographic:
            # orthographic size is half the visible height in world units; the skeleton object's
            # own scale turns that into its units.
            size = scene.float_value(camera_transform, CAMERA, ORTHOGRAPHIC_SIZE, t, camera['orthographic size'])
            object_scale = length(transform_direction(skeleton_world, (0.0, 1.0, 0.0)))
            if not object_scale:
                raise CameraError('The entrance skeleton has zero scale')
            height = 2 * size / object_scale
        else:
            fov = scene.float_value(camera_transform, CAMERA, FIELD_OF_VIEW, t, camera['field of view'])
            distance = -position[2]
            if distance <= 0:
                raise CameraError(f'The perspective entrance camera is behind the skeleton at {t:.2f}s')
            height = 2 * distance * math.tan(math.radians(fov) / 2)
        if not height > 0:
            raise CameraError(f'The entrance camera shows nothing at {t:.2f}s')
        return position, height, roll

    frames, views = [], {}
    for t in times:
        position, height, roll = view(t)
        views[t] = (position, height)
        frames.append([t, position[0] / unit, position[1] / unit, height / unit, roll])
    frames = [[round(t, 3), round(cx, 1), round(cy, 1), round(h, 1), round(roll, 1)]
              for t, cx, cy, h, roll in decimate(frames, [CAMERA_TOLERANCE, CAMERA_TOLERANCE, CAMERA_TOLERANCE, ROLL_TOLERANCE])]

    # Fades are taken only from the clips that drive the camera: their time is the entrance's
    # (the camera shows the entrance from its first frame). Other effect objects are switched on by
    # the controller's script at moments the data does not record (Lappland's Fugue turns its
    # ending white-out on near the end; read from the start, it would whiten the whole entrance).
    camera_clips = {id(clip) for link in scene.chain(camera_transform) for attribute in (POSITION, ROTATION, EULER, SCALE)
                    for clip, _ in scene.transform_curves.get((link, attribute), [])}
    camera_clips |= {id(clip) for (target, type_id, _), entries in scene.float_curves.items()
                     if target == camera_transform and type_id == CAMERA for clip, _ in entries}
    fades = []
    for (transform, type_id, attribute), entries in sorted(scene.float_curves.items()):
        if type_id != RENDERER or not any(id(clip) in camera_clips for clip, _ in entries):
            continue
        property_hash, channel = material_binding(attribute)
        if channel != 3:
            continue
        go = scene.go_of[transform]
        renderer = scene.component(go, ('MeshRenderer',))
        mesh_filter = scene.component(go, ('MeshFilter',))
        if renderer is None or mesh_filter is None:
            continue
        mesh_ref = scene.tree(mesh_filter).get('m_Mesh') or {}
        if not mesh_ref.get('m_PathID') or mesh_ref.get('m_FileID', 0) != 0:
            continue
        aabb = scene.tree(mesh_ref['m_PathID'], 'Mesh')['m_LocalAABB']
        centre, extent = aabb['m_Center'], aabb['m_Extent']
        corners = [(centre['x'] + sx * extent['x'], centre['y'] + sy * extent['y'], centre['z'] + sz * extent['z'])
                   for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        name, saved = _material_colour(scene, renderer, property_hash)
        # The Particles shaders' _TintColor is doubled when drawn (0.5 draws the texture as is);
        # _Color is not. A black quad at _TintColor alpha 0.5 is fully black.
        gain = 2.0 if name == '_TintColor' else 1.0
        static_active = 1.0 if scene.tree(go, 'GameObject').get('m_IsActive', 1) else 0.0
        clip, curves = entries[0]

        def covers_view(t: float) -> bool:
            """In front of the camera, not behind the skeleton (a background), and over the whole
            view, turned or not: a full-screen fade. Anything else is an effect, not drawn."""
            position, height = views[t]
            to_skeleton = multiply(inverse(scene.world(skeleton_transform, t)), scene.world(transform, t))
            points = [transform_point(to_skeleton, corner) for corner in corners]
            xs, ys, zs = [p[0] for p in points], [p[1] for p in points], [p[2] for p in points]
            reach = math.hypot(height * COVER_ASPECT, height) / 2
            return (min(xs) <= position[0] - reach and max(xs) >= position[0] + reach
                    and min(ys) <= position[1] - reach and max(ys) >= position[1] + reach
                    and min(zs) > position[2] and max(zs) <= 1e-3)

        keys, covers, strongest = [], True, (0.0, times[0])
        for t in times:
            active = scene.float_value(transform, GAMEOBJECT, IS_ACTIVE, t, static_active) >= 0.5
            enabled = scene.float_value(transform, RENDERER, ENABLED, t, 1.0) >= 0.5
            alpha = min(1.0, max(0.0, gain * curves[0].value(clip.time(t)))) if active and enabled else 0.0
            if alpha > 0.01:
                if not covers_view(t):
                    covers = False
                    break
                if alpha > strongest[0]:
                    strongest = (alpha, t)
            keys.append([t, alpha])
        if not covers or strongest[0] <= 0.01:
            continue
        if len(entries) > 1:
            raise CameraError(f'Several clips fade {scene.path[transform]}; which plays is not decided here')
        t_colour = strongest[1]
        rgb = [min(1.0, max(0.0, gain * scene.float_value(transform, RENDERER, property_hash | ((4 + c) << 28), t_colour, saved[c])))
               for c in range(3)]
        fades.append({'color': [round(v, 3) for v in rgb],
                      'keys': [[round(t, 3), round(a, 3)] for t, a in decimate(keys, [ALPHA_TOLERANCE])]})
    # The colour the entrance's controller hands over to the illustration through (_params.fadeColor:
    # white for most, black for some); the quads above already reach it on screen where they exist.
    colour = ((controller or {}).get('_params') or {}).get('fadeColor')
    handover = [round(min(1.0, max(0.0, float(colour[k]))), 3) for k in 'rgb'] if isinstance(colour, dict) else None
    return {'frames': frames, 'fades': fades, 'handover': handover}
