"""Deterministic tests for the reference simulator, particle_oracle.py (run: python -I scripts/tests/test_particle_oracle.py,
standard library only; the Check workflow runs it).

The synthetic systems are built from the same dict shapes UnityPy returns for ParticleSystem typetrees.
The real-bundle test simulates every particle system of one outfit bundle when PARTICLE_ORACLE_BUNDLE names an
unpacked bundle (Hoshiguma's Elite 2, char_1044_hsgma2_2.ab, checks one more number) and UnityPy imports; it
is skipped otherwise.
"""
import copy
import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # python -I does not add the script's folder
import particle_oracle as up  # noqa: E402

BUNDLE = os.environ.get('PARTICLE_ORACLE_BUNDLE', '')
SCRIPTS = os.path.join(os.path.dirname(HERE))


def key(t, v, i=0.0, o=0.0):
    return {'time': t, 'value': v, 'inSlope': i, 'outSlope': o, 'weightedMode': 0, 'inWeight': 1 / 3, 'outWeight': 1 / 3}


def curve(keys=(), pre=2, post=2):
    return {'m_Curve': list(keys), 'm_PreInfinity': pre, 'm_PostInfinity': post, 'm_RotationOrder': 4}


def mmc(state=0, scalar=0.0, min_scalar=0.0, max_keys=(), min_keys=()):
    return {'minMaxState': state, 'scalar': scalar, 'minScalar': min_scalar, 'maxCurve': curve(max_keys), 'minCurve': curve(min_keys)}


def gradient(colors=((0.0, (1, 1, 1)), (1.0, (1, 1, 1))), alphas=((0.0, 1.0), (1.0, 1.0)), mode=0):
    g = {'m_Mode': mode, 'm_NumColorKeys': len(colors), 'm_NumAlphaKeys': len(alphas)}
    for i in range(8):
        r, gg, b = colors[i][1] if i < len(colors) else (0, 0, 0)
        a = alphas[i][1] if i < len(alphas) else 0.0
        g[f'key{i}'] = {'r': r, 'g': gg, 'b': b, 'a': a}
        g[f'ctime{i}'] = int(round(colors[i][0] * 65535)) if i < len(colors) else 0
        g[f'atime{i}'] = int(round(alphas[i][0] * 65535)) if i < len(alphas) else 0
    return g


def mmg(state=0, max_color=(1, 1, 1, 1), min_color=(1, 1, 1, 1), max_gradient=None, min_gradient=None):
    col = lambda c: {'r': c[0], 'g': c[1], 'b': c[2], 'a': c[3]}  # noqa: E731
    return {'minMaxState': state, 'minColor': col(min_color), 'maxColor': col(max_color),
            'maxGradient': max_gradient or gradient(), 'minGradient': min_gradient or gradient()}


def multi(value, mode=0, spread=0.0):
    return {'value': value, 'mode': mode, 'spread': spread, 'speed': mmc(0, 1.0, 1.0)}


def make_ps(**kw):
    """A minimal ParticleSystem typetree: no shape (emit at the origin along +Z), nothing over lifetime."""
    ps = {
        'lengthInSec': 5.0, 'simulationSpeed': 1.0, 'looping': False, 'prewarm': False, 'playOnAwake': True,
        'autoRandomSeed': True, 'randomSeed': 0, 'moveWithTransform': 0, 'scalingMode': 1, 'startDelay': mmc(0, 0.0),
        'InitialModule': {'enabled': True, 'startLifetime': mmc(0, 100.0), 'startSpeed': mmc(0, 0.0), 'startColor': mmg(),
                          'startSize': mmc(0, 1.0), 'startSizeY': mmc(0, 1.0), 'startSizeZ': mmc(0, 1.0),
                          'startRotationX': mmc(0, 0.0), 'startRotationY': mmc(0, 0.0), 'startRotation': mmc(0, 0.0),
                          'randomizeRotationDirection': 0.0, 'maxNumParticles': 1000, 'size3D': False, 'rotation3D': False,
                          'gravityModifier': mmc(0, 0.0)},
        'EmissionModule': {'enabled': True, 'rateOverTime': mmc(0, 0.0), 'rateOverDistance': mmc(0, 0.0), 'm_BurstCount': 0, 'm_Bursts': []},
        'ShapeModule': {'enabled': False, 'type': 4, 'angle': 25.0, 'length': 5.0, 'radius': multi(1.0), 'arc': multi(360.0),
                        'radiusThickness': 1.0, 'boxThickness': {'x': 0, 'y': 0, 'z': 0}, 'donutRadius': 0.2,
                        'm_Position': {'x': 0, 'y': 0, 'z': 0}, 'm_Rotation': {'x': 0, 'y': 0, 'z': 0}, 'm_Scale': {'x': 1, 'y': 1, 'z': 1},
                        'randomDirectionAmount': 0.0, 'sphericalDirectionAmount': 0.0, 'randomPositionAmount': 0.0},
    }
    for path, value in kw.items():
        node = ps
        parts = path.split('__')
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return ps


def burst(time=0.0, count=1, cycles=1, interval=0.01, probability=1.0, count_curve=None):
    return {'time': time, 'countCurve': count_curve or mmc(0, float(count)), 'cycleCount': cycles, 'repeatInterval': interval,
            'probability': probability}


def run(ps, seconds, dt=1 / 60, seed=1, options=None):
    sim = up.Simulation.from_trees(ps, None, seed=seed, options=options)
    sim.play()
    for _ in range(int(round(seconds / dt))):
        sim.step(dt)
    return sim


class Curves(unittest.TestCase):
    def test_hermite_flat_tangents(self):
        keys = [key(0, 0), key(1, 1)]
        self.assertAlmostEqual(up.evaluate_curve(keys, 0.5), 0.5)
        self.assertAlmostEqual(up.evaluate_curve(keys, 0.25), 0.15625)  # 3t^2 - 2t^3

    def test_hermite_linear_tangents(self):
        keys = [key(0, 0, 1, 1), key(2, 2, 1, 1)]
        self.assertAlmostEqual(up.evaluate_curve(keys, 0.5), 0.5)  # slope x segment length

    def test_stepped(self):
        keys = [key(0, 3, 0, up.INF), key(1, 7, 0, 0)]
        self.assertEqual(up.evaluate_curve(keys, 0.99), 3)

    def test_wrap_modes(self):
        keys = [key(0, 0, 2, 2), key(0.5, 1, 2, 2)]
        self.assertAlmostEqual(up.evaluate_curve(keys, 0.75, post=up.WRAP_CLAMP), 1.0)
        self.assertAlmostEqual(up.evaluate_curve(keys, 0.75, post=up.WRAP_REPEAT), up.evaluate_curve(keys, 0.25))
        self.assertAlmostEqual(up.evaluate_curve(keys, 0.75, post=up.WRAP_PINGPONG), up.evaluate_curve(keys, 0.25))
        self.assertAlmostEqual(up.evaluate_curve(keys, -0.1, pre=up.WRAP_CLAMP), 0.0)

    def test_minmaxcurve_modes(self):
        lin = [key(0, 0, 1, 1), key(1, 1, 1, 1)]
        self.assertEqual(up.MinMaxCurve.from_tree(mmc(0, 3.0, 9.0)).evaluate(0.3, 0.7), 3.0)
        self.assertAlmostEqual(up.MinMaxCurve.from_tree(mmc(3, 10.0, 2.0)).evaluate(0.3, 0.25), 4.0)  # lerp(min, max)
        self.assertAlmostEqual(up.MinMaxCurve.from_tree(mmc(1, 4.0, 99.0, lin)).evaluate(0.5), 2.0)
        flat0 = [key(0, 0), key(1, 0)]
        # TwoCurves: both curves times `scalar`, minScalar ignored
        self.assertAlmostEqual(up.MinMaxCurve.from_tree(mmc(2, 4.0, 99.0, lin, flat0)).evaluate(0.5, 0.5), 1.0)

    def test_gradient(self):
        g = up.Gradient.from_tree(gradient(colors=((0.0, (0, 0, 0)), (1.0, (1, 1, 1))), alphas=((0.0, 1.0), (1.0, 0.0))))
        r, _, _, a = g.evaluate(0.5)
        self.assertAlmostEqual(r, 0.5, places=4)
        self.assertAlmostEqual(a, 0.5, places=4)
        fixed = up.Gradient.from_tree(gradient(colors=((0.3, (1, 0, 0)), (1.0, (0, 0, 1))), mode=1))
        self.assertEqual(fixed.evaluate(0.2)[:3], (1, 0, 0))
        self.assertEqual(fixed.evaluate(0.5)[:3], (0, 0, 1))

    def test_minmaxgradient_modes(self):
        two = up.MinMaxGradient.from_tree(mmg(2, max_color=(1, 1, 1, 1), min_color=(0, 0, 0, 0)))
        self.assertEqual(two.evaluate(0.9, 0.25), (0.25, 0.25, 0.25, 0.25))
        rnd = up.MinMaxGradient.from_tree(mmg(4, max_gradient=gradient(colors=((0.0, (0, 0, 0)), (1.0, (1, 0, 0))))))
        self.assertAlmostEqual(rnd.evaluate(0.0, 0.5)[0], 0.5, places=4)  # sampled at the random value, not at t

    def test_color32(self):
        self.assertEqual(up.color32((0.5, 0.0, 1.0, 0.2)), (128 / 255, 0.0, 1.0, 51 / 255))


class Emission(unittest.TestCase):
    def test_rate_over_duration(self):
        sim = run(make_ps(EmissionModule__rateOverTime=mmc(0, 10.0)), 6.0)
        self.assertLessEqual(abs(sim.emitted_total - 50), 1)

    def test_rate_is_frame_rate_independent(self):
        a = run(make_ps(EmissionModule__rateOverTime=mmc(0, 7.0), looping=True), 3.0, dt=1 / 30).emitted_total
        b = run(make_ps(EmissionModule__rateOverTime=mmc(0, 7.0), looping=True), 3.0, dt=1 / 144).emitted_total
        self.assertLessEqual(abs(a - b), 1)

    def test_the_accumulator_start_is_the_way_to_the_first_birth(self):
        ps = make_ps(EmissionModule__rateOverTime=mmc(0, 2.0))
        first = {}
        for start in (0.0, 0.5, 1.0):
            sim = up.Simulation.from_trees(ps, None, options=up.Options(emit_accumulator_start=start))
            sim.play()
            for n in range(1, 120):
                sim.step(1 / 64)  # exact in binary: the accumulator reaches 1 on its step
                if sim.emitted_total:
                    first[start] = (n, sim.emitted_total)
                    break
        self.assertEqual(first[0.0], (32, 1))   # 1 / rate: at 0.5 s
        self.assertEqual(first[0.5], (16, 1))   # half way there: 0.25 s
        self.assertEqual(first[1.0], (1, 1))    # one at once, on the first step
        sim = run(make_ps(EmissionModule__rateOverTime=mmc(0, 10.0)), 6.0, options=up.Options(emit_accumulator_start=1.0))
        self.assertEqual(sim.emitted_total, 51)  # the one at once, then one every 0.1 s over the 5 s loop

    def test_burst_at_zero_and_cycles(self):
        sim = run(make_ps(EmissionModule__m_Bursts=[burst(0.0, 7)]), 1 / 60)
        self.assertEqual(len(sim.particles), 7)
        sim = run(make_ps(EmissionModule__m_Bursts=[burst(0.0, 2, cycles=3, interval=0.1)]), 0.5)
        self.assertEqual(sim.emitted_total, 6)

    def test_burst_probability_zero(self):
        self.assertEqual(run(make_ps(EmissionModule__m_Bursts=[burst(0.0, 5, probability=0.0)]), 1.0).emitted_total, 0)

    def test_looping_bursts_repeat_each_loop(self):
        sim = run(make_ps(lengthInSec=1.0, looping=True, EmissionModule__m_Bursts=[burst(0.0, 5)]), 2.5)
        self.assertEqual(sim.emitted_total, 15)  # loops 0, 1, 2

    def test_infinite_cycles_until_loop_end(self):
        sim = run(make_ps(lengthInSec=1.0, EmissionModule__m_Bursts=[burst(0.0, 1, cycles=0, interval=0.25)]), 2.0)
        self.assertEqual(sim.emitted_total, 4)  # 0, 0.25, 0.5, 0.75

    def test_max_particles_drops(self):
        sim = run(make_ps(InitialModule__maxNumParticles=1, EmissionModule__rateOverTime=mmc(0, 100.0)), 1.0)
        self.assertEqual(len(sim.particles), 1)

    def test_max_one_respawns_when_free(self):
        ps = make_ps(looping=True, lengthInSec=1.0, InitialModule__maxNumParticles=1, InitialModule__startLifetime=mmc(0, 0.5),
                     EmissionModule__rateOverTime=mmc(0, 50.0))
        sim = up.Simulation.from_trees(ps)
        sim.play()
        counts = []
        for _ in range(180):
            sim.step(1 / 60)
            counts.append(len(sim.particles))
        self.assertEqual(min(counts[10:]), 1)  # never a visible gap

    def test_start_delay(self):
        ps = make_ps(startDelay=mmc(0, 0.5), EmissionModule__rateOverTime=mmc(0, 50.0))
        self.assertEqual(run(ps, 0.4).emitted_total, 0)
        self.assertGreater(run(ps, 0.7).emitted_total, 0)

    def test_prewarm_starts_full(self):
        ps = make_ps(looping=True, prewarm=True, lengthInSec=1.0, InitialModule__startLifetime=mmc(0, 2.0),
                     EmissionModule__rateOverTime=mmc(0, 10.0))
        sim = up.Simulation.from_trees(ps)
        sim.play()
        self.assertGreaterEqual(len(sim.particles), 9)  # one loop already simulated
        sim2 = up.Simulation.from_trees(make_ps(looping=True, prewarm=True, startDelay=mmc(0, 3.0), lengthInSec=1.0,
                                                EmissionModule__rateOverTime=mmc(0, 10.0)))
        self.assertEqual(sim2.delay, 0.0)  # start delay is ignored for a prewarmed looping system

    def test_determinism(self):
        ps = make_ps(looping=True, EmissionModule__rateOverTime=mmc(0, 30.0), InitialModule__startSpeed=mmc(3, 5.0, 1.0),
                     ShapeModule__enabled=True, ShapeModule__type=up.SHAPE['Cone'])
        a = run(ps, 1.0, seed=7).render_records()
        b = run(ps, 1.0, seed=7).render_records()
        c = run(ps, 1.0, seed=8).render_records()
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class Motion(unittest.TestCase):
    def test_constant_speed(self):
        sim = run(make_ps(InitialModule__startSpeed=mmc(0, 2.0), EmissionModule__m_Bursts=[burst(0.0, 1)]), 1.0)
        self.assertAlmostEqual(sim.particles[0].position[2], 2.0, places=6)

    def test_gravity(self):
        sim = run(make_ps(InitialModule__gravityModifier=mmc(0, 1.0), EmissionModule__m_Bursts=[burst(0.0, 1)]), 1.0, dt=1 / 600)
        self.assertAlmostEqual(sim.particles[0].position[1], -0.5 * 9.81, delta=0.5 * 9.81 * 0.01)

    def test_velocity_over_lifetime_is_not_accumulated(self):
        ps = make_ps(EmissionModule__m_Bursts=[burst(0.0, 1)])
        ps['VelocityModule'] = {'enabled': True, 'x': mmc(0, 1.0), 'y': mmc(0, 0.0), 'z': mmc(0, 0.0), 'orbitalX': mmc(), 'orbitalY': mmc(),
                                'orbitalZ': mmc(), 'orbitalOffsetX': mmc(), 'orbitalOffsetY': mmc(), 'orbitalOffsetZ': mmc(),
                                'radial': mmc(), 'speedModifier': mmc(0, 1.0), 'inWorldSpace': False}
        sim = run(ps, 2.0)
        self.assertAlmostEqual(sim.particles[0].position[0], 2.0, places=6)
        self.assertEqual(sim.particles[0].velocity, (0.0, 0.0, 0.0))

    def test_limit_velocity_dampens_towards_limit(self):
        ps = make_ps(InitialModule__startSpeed=mmc(0, 10.0), EmissionModule__m_Bursts=[burst(0.0, 1)])
        ps['ClampVelocityModule'] = {'enabled': True, 'x': mmc(0, 1), 'y': mmc(0, 1), 'z': mmc(0, 1), 'magnitude': mmc(0, 1.0),
                                     'separateAxis': False, 'inWorldSpace': False, 'dampen': 0.1, 'drag': mmc(0, 0.0)}
        sim = up.Simulation.from_trees(ps)
        sim.play()
        speeds = []
        for _ in range(120):
            sim.step(1 / 60)
            speeds.append(up.v_len(sim.particles[0].velocity))
        self.assertTrue(all(b <= a + 1e-9 for a, b in zip(speeds, speeds[1:])))
        self.assertGreaterEqual(min(speeds), 1.0 - 1e-9)
        self.assertLess(speeds[-1], 1.01)

    def test_orbital_velocity_turns_without_drifting_outward(self):
        # A particle 3 units from the centre orbiting about Z at up to 10 rad/s (Hoshiguma's fire_ring_ctrl): an exact
        # turn each step keeps the radius, at any step and speed modifier, and turns by the integral of the speed.
        ps = make_ps(EmissionModule__m_Bursts=[burst(0.0, 1)], ShapeModule__enabled=True, ShapeModule__type=up.SHAPE['CircleEdge'],
                     ShapeModule__radius=multi(3.0), ShapeModule__arc=multi(0.0001))
        orbital = mmc(1, 10.0, max_keys=(key(0.0, 0.0, 1.0, 1.0), key(1.0, 1.0, 1.0, 1.0)))
        for dt, speed_mod in ((1 / 60, 1.0), (1 / 30, 1.0), (1 / 60, 2.0)):
            ps['VelocityModule'] = {'enabled': True, 'x': mmc(), 'y': mmc(), 'z': mmc(), 'orbitalX': mmc(), 'orbitalY': mmc(),
                                    'orbitalZ': copy.deepcopy(orbital), 'orbitalOffsetX': mmc(), 'orbitalOffsetY': mmc(), 'orbitalOffsetZ': mmc(),
                                    'radial': mmc(), 'speedModifier': mmc(0, speed_mod), 'inWorldSpace': False}
            ps['InitialModule']['startLifetime'] = mmc(0, 3.0)
            sim = up.Simulation.from_trees(ps)
            sim.play()
            turned = 0.0
            last = math.atan2(sim.particles[0].position[1], sim.particles[0].position[0]) if sim.particles else None
            for _ in range(int(round(2.9 / dt))):
                sim.step(dt)
                x, y, _z = sim.particles[0].position
                self.assertAlmostEqual(math.hypot(x, y), 3.0, places=9)
                angle = math.atan2(y, x)
                if last is not None:
                    turned += (angle - last + math.pi) % (2 * math.pi) - math.pi
                last = angle
            # 10 rad/s x age / 3 integrated over the age (the speed modifier scales the step): 10 x a^2 / 6 rad.
            age = sim.particles[0].age
            self.assertAlmostEqual(turned, speed_mod * 10.0 * age * age / 6.0, delta=0.05 * speed_mod * 10.0 * age * age / 6.0)

    def test_rotation_over_lifetime(self):
        ps = make_ps(EmissionModule__m_Bursts=[burst(0.0, 1)])
        ps['RotationModule'] = {'enabled': True, 'x': mmc(), 'y': mmc(), 'curve': mmc(0, math.pi), 'separateAxes': False}
        sim = run(ps, 0.5)
        self.assertAlmostEqual(sim.particles[0].rotation[2], math.pi / 2, places=6)


class Shapes(unittest.TestCase):
    def sample(self, **shape):
        ps = make_ps(ShapeModule__enabled=True, **{f'ShapeModule__{k}': v for k, v in shape.items()})
        cfg = up.Config.from_trees(ps)
        rng = up.XorShift128(3)
        return [up.sample_shape(cfg.shape, rng, {}, 0.0) for _ in range(400)]

    def test_circle_arc(self):
        for pos, d in self.sample(type=up.SHAPE['Circle'], radius=multi(2.0), arc=multi(90.0)):
            self.assertLessEqual(math.hypot(pos[0], pos[1]), 2.0 + 1e-9)
            self.assertGreaterEqual(pos[0], -1e-9)  # the arc starts at +X and turns towards +Y
            self.assertGreaterEqual(pos[1], -1e-9)
            self.assertAlmostEqual(pos[2], 0.0)

    def test_circle_edge_thickness_zero(self):
        for pos, _ in self.sample(type=up.SHAPE['Circle'], radius=multi(2.0), radiusThickness=0.0):
            self.assertAlmostEqual(math.hypot(pos[0], pos[1]), 2.0, places=6)

    def test_cone_directions(self):
        for pos, d in self.sample(type=up.SHAPE['Cone'], angle=30.0, radius=multi(1.0)):
            self.assertLessEqual(math.degrees(math.acos(max(-1, min(1, d[2])))), 30.0 + 1e-6)
            self.assertAlmostEqual(pos[2], 0.0)

    def test_box_volume(self):
        for pos, d in self.sample(type=up.SHAPE['Box'], m_Scale={'x': 4, 'y': 2, 'z': 0}):
            self.assertLessEqual(abs(pos[0]), 2.0 + 1e-9)
            self.assertLessEqual(abs(pos[1]), 1.0 + 1e-9)
            self.assertEqual(d, (0.0, 0.0, 1.0))

    def test_edge(self):
        for pos, d in self.sample(type=up.SHAPE['SingleSidedEdge'], radius=multi(3.0)):
            self.assertLessEqual(abs(pos[0]), 3.0 + 1e-9)
            self.assertEqual(d, (0.0, 1.0, 0.0))

    def test_shape_rotation(self):
        for pos, d in self.sample(type=up.SHAPE['Box'], m_Rotation={'x': -90, 'y': 0, 'z': 0}, m_Scale={'x': 0, 'y': 0, 'z': 0}):
            self.assertAlmostEqual(d[1], 1.0, places=6)  # -90 about X turns +Z into +Y

    def test_burst_spread(self):
        ps = make_ps(ShapeModule__enabled=True, ShapeModule__type=up.SHAPE['Circle'], ShapeModule__radiusThickness=0.0,
                     ShapeModule__arc=multi(360.0, mode=up.MULTI_BURST_SPREAD), EmissionModule__m_Bursts=[burst(0.0, 4)])
        sim = run(ps, 1 / 60)
        angles = sorted(round(math.degrees(math.atan2(p.position[1], p.position[0])) % 360) for p in sim.particles)
        self.assertEqual(angles, [0, 90, 180, 270])


class Rendering(unittest.TestCase):
    def test_texture_sheet_frames(self):
        ps = make_ps(EmissionModule__m_Bursts=[burst(0.0, 1)], InitialModule__startLifetime=mmc(0, 1.0))
        ps['UVModule'] = {'enabled': True, 'mode': 0, 'timeMode': 0, 'tilesX': 2, 'tilesY': 2, 'animationType': 0, 'rowMode': 1,
                          'rowIndex': 0, 'cycles': 1.0, 'frameOverTime': mmc(1, 0.9999, 0.9999, [key(0, 0, 1, 1), key(1, 1, 1, 1)]),
                          'startFrame': mmc(0, 0.0)}
        sim = up.Simulation.from_trees(ps)
        sim.play()
        frames, steps = [], 0
        for target in (6, 18, 33, 48, 57):  # ages 0.1, 0.3, 0.55, 0.8, 0.95
            while steps < target:
                sim.step(1 / 60)
                steps += 1
            frames.append(sim.frame_of(sim.particles[0])[0])
        self.assertEqual(frames, [0, 1, 2, 3, 3])  # floor(age * 0.9999 * 4)
        sim2 = up.Simulation.from_trees(ps)
        sim2.play()
        sim2.step(1 / 60)
        self.assertEqual(sim2.frame_of(sim2.particles[0]), (0, (0.0, 0.5, 0.5, 1.0)))  # frame 0 = top-left tile

    def test_texcoord_layout(self):
        layout = up.texcoord_layout([0, 1, 3, 4, 34, 38])
        self.assertEqual(layout['UV'], ['TEXCOORD0.x', 'TEXCOORD0.y'])
        self.assertEqual(layout['Custom1XYZW'], ['TEXCOORD0.z', 'TEXCOORD0.w', 'TEXCOORD1.x', 'TEXCOORD1.y'])
        self.assertEqual(layout['Custom2XYZW'], ['TEXCOORD1.z', 'TEXCOORD1.w', 'TEXCOORD2.x', 'TEXCOORD2.y'])

    def test_custom_data(self):
        ps = make_ps(EmissionModule__m_Bursts=[burst(0.0, 1)], InitialModule__startLifetime=mmc(0, 1.0))
        ps['CustomDataModule'] = {'enabled': True, 'mode0': 1, 'vectorComponentCount0': 2,
                                  'vector0_0': mmc(1, 2.0, 2.0, [key(0, 0, 1, 1), key(1, 1, 1, 1)]), 'vector0_1': mmc(0, 5.0),
                                  'vector0_2': mmc(0, 9.0), 'vector0_3': mmc(0, 9.0), 'mode1': 0, 'vectorComponentCount1': 4,
                                  'color0': mmg(), 'color1': mmg()}
        sim = run(ps, 0.5)
        c1, c2 = sim.custom_data_of(sim.particles[0])
        self.assertAlmostEqual(c1[0], 1.0, places=6)
        self.assertEqual(c1[1:], (5.0, 0.0, 0.0))  # components past vectorComponentCount stay 0
        self.assertEqual(c2, (0.0, 0.0, 0.0, 0.0))

    def test_billboard_rotation_is_clockwise(self):
        corners = up.billboard_corners((0, 0, 0), (2, 2, 2), math.pi / 2)
        top_right = corners[3]
        self.assertAlmostEqual(top_right[0], 1.0)
        self.assertAlmostEqual(top_right[1], -1.0)


class RealBundle(unittest.TestCase):
    def test_every_system_of_one_bundle(self):
        if not BUNDLE or not os.path.exists(BUNDLE):
            self.skipTest('PARTICLE_ORACLE_BUNDLE names no bundle')
        try:
            sys.path.insert(0, SCRIPTS)
            import l2d  # noqa: F401
            unitypy = l2d._patch_unitypy()
        except Exception as e:  # noqa: BLE001
            self.skipTest(f'UnityPy / l2d not importable: {e}')
        with open(BUNDLE, 'rb') as fh:
            env = unitypy.load(fh.read())
        renderers = {}
        systems = []
        names = {}
        for o in env.objects:
            if o.type.name == 'GameObject':
                names[o.path_id] = o.read_typetree().get('m_Name')
            elif o.type.name == 'ParticleSystemRenderer':
                t = o.read_typetree()
                renderers[t['m_GameObject']['m_PathID']] = t
            elif o.type.name == 'ParticleSystem':
                systems.append(o.read_typetree())
        alive = 0
        bundle_is_hoshiguma = os.path.basename(BUNDLE) == 'char_1044_hsgma2_2.ab'
        checked_dust = False
        for ps in systems:
            sim = up.Simulation.from_trees(ps, renderers.get(ps['m_GameObject']['m_PathID']), seed=1)
            sim.play()
            for _ in range(60):
                sim.step(1 / 30)
            self.assertLessEqual(len(sim.particles), sim.c.max_particles)
            for rec in sim.render_records():
                self.assertTrue(all(math.isfinite(x) for x in rec['position']))
            alive += len(sim.particles)
            if bundle_is_hoshiguma and names.get(ps['m_GameObject']['m_PathID']) == 'dust_04':
                # looping + prewarm, rate 60/s, lifetime 1.0..1.5 s: steady state = rate x mean lifetime = 75,
                # present right after play() and kept while stepping; the whole first loop prewarmed, or only
                # its last 1.5 s (the window: every particle alive at its end was born in it)
                for prewarm in ('full', 'window'):
                    fresh = up.Simulation.from_trees(ps, None, seed=1, options=up.Options(prewarm=prewarm))
                    fresh.play()
                    self.assertTrue(60 <= len(fresh.particles) <= 90, (prewarm, len(fresh.particles)))
                self.assertEqual(fresh.c.prewarm_window, 1.5)
                self.assertTrue(60 <= len(sim.particles) <= 90, len(sim.particles))
                checked_dust = True
        self.assertGreater(alive, 0)
        if bundle_is_hoshiguma:
            self.assertTrue(checked_dust)


# Fixed vectors for the site's port (checked against a Math.imul implementation): mix32 of 0, 1 and 0xDEADBEEF,
# particle_random(12345, key) for keys 0-2, and play_seed(1, 2, 3).
MIX32 = [0, 1753845952, 3861431939]
PARTICLE_RANDOM = [0.9617756559581346, 0.30952230805424547, 0.27417782237265376]
PLAY_SEED = 1066372762


class Randoms(unittest.TestCase):
    """The site's random scheme: hashed per-particle values from one seed each (Options.randoms 'hashed')."""

    def test_the_hash_is_fixed(self):
        # The values the TypeScript port must reproduce (Math.imul arithmetic on uint32).
        self.assertEqual([up.mix32(x) for x in (0, 1, 0xDEADBEEF)], MIX32)
        self.assertEqual([up.particle_random(12345, k) for k in range(3)], PARTICLE_RANDOM)
        self.assertEqual(up.play_seed(1, 2, 3), PLAY_SEED)
        values = [up.particle_random(12345, k) for k in range(len(up.RANDOM_KEYS))]
        self.assertTrue(all(0.0 <= v <= 1.0 for v in values))
        self.assertEqual(len(set(values)), len(values), 'every key draws its own value')
        self.assertEqual(up.play_seed(1, 2, 3), up.mix32(up.mix32(up.mix32(1) ^ 2) ^ 3))
        self.assertNotEqual(up.play_seed(1, 2, 3), up.play_seed(1, 2, 4), 'a replayed press draws new particles')
        self.assertEqual(len(up.RANDOM_KEY), len(up.RANDOM_KEYS))

    def test_a_particle_keeps_its_values_whatever_else_is_drawn(self):
        # Two systems that differ in a module the first lacks: the particles they share (same seeds, same
        # order of system draws) start the same way, which a single drawn stream could not keep.
        base = make_ps(looping=True, EmissionModule__rateOverTime=mmc(0, 20.0), InitialModule__startSpeed=mmc(3, 1.0, 4.0),
                       ShapeModule__enabled=True, ShapeModule__type=up.SHAPE['Cone'])
        more = copy.deepcopy(base)
        more['RotationModule'] = {'enabled': True, 'x': mmc(), 'y': mmc(), 'curve': mmc(3, -1.0, 1.0), 'separateAxes': False}
        a, b = run(base, 0.5, seed=3), run(more, 0.5, seed=3)
        self.assertEqual([p.velocity for p in a.particles], [p.velocity for p in b.particles])
        stream = up.Options(randoms='stream')
        self.assertNotEqual(run(base, 0.5, seed=3).render_records(), run(base, 0.5, seed=3, options=stream).render_records())

    def test_a_fixed_seed_is_used_as_it_is_even_zero(self):
        ps = make_ps(autoRandomSeed=False, randomSeed=0, looping=True, EmissionModule__rateOverTime=mmc(0, 30.0), InitialModule__startSpeed=mmc(3, 1.0, 4.0))
        self.assertEqual(run(ps, 0.5, seed=1).render_records(), run(ps, 0.5, seed=99).render_records())
        signed = make_ps(autoRandomSeed=False, randomSeed=-334000898)
        self.assertEqual(up.Simulation.from_trees(signed).seed, -334000898 & 0xFFFFFFFF)


class Prewarm(unittest.TestCase):
    def test_the_window_is_the_longest_lifetime_or_the_loop(self):
        lin = [key(0, 0.5, 0, 0), key(0.5, 2.0, 0, 0), key(1, 0.5, 0, 0)]
        ps = make_ps(looping=True, prewarm=True, lengthInSec=100.0, InitialModule__startLifetime=mmc(1, 1.0, 0.0, lin),
                     EmissionModule__rateOverTime=mmc(0, 5.0))
        self.assertEqual(up.Config.from_trees(ps).prewarm_window, 2.0)
        # maxParticles could bind: the dropped particles depend on what came before, so up to 30 s of the loop.
        capped = copy.deepcopy(ps)
        capped['InitialModule']['maxNumParticles'] = 3
        self.assertEqual(up.Config.from_trees(capped).prewarm_window, 30.0)
        self.assertIsNone(up.Config.from_trees(make_ps(prewarm=True, looping=False)).prewarm_window)

    def test_a_windowed_prewarm_leaves_the_same_live_particles(self):
        # The window holds every particle alive at the loop's end; the rate's accumulator starts afresh at the
        # window's start, so birth times may shift by less than one interval (1 / rate), never the count.
        ps = make_ps(looping=True, prewarm=True, lengthInSec=40.0, InitialModule__startLifetime=mmc(0, 2.05),
                     EmissionModule__rateOverTime=mmc(0, 10.0), InitialModule__startSpeed=mmc(0, 1.0))
        full = up.Simulation.from_trees(ps, options=up.Options(prewarm='full'))
        full.play()
        window = up.Simulation.from_trees(ps)
        window.play()
        self.assertEqual(len(full.particles), len(window.particles))
        for a, b in zip(sorted(p.age for p in full.particles), sorted(p.age for p in window.particles)):
            self.assertLess(abs(a - b), 0.1)

    def test_curve_bounds_include_the_overshoot_between_keys(self):
        keys = [key(0, 0, 3, 3), key(1, 0, 3, 3)]
        low, high = up.curve_bounds(keys)
        sampled = max(up.evaluate_curve(keys, i / 1000) for i in range(1001))
        self.assertGreaterEqual(high, sampled)
        self.assertAlmostEqual(high, math.sqrt(3) / 6, places=12)  # 3 (2s^3 - 3s^2 + s) at s = 1/2 - sqrt(3)/6
        self.assertAlmostEqual(low, -math.sqrt(3) / 6, places=12)


class Export(unittest.TestCase):
    """The layerParticles.json encodings, read back."""

    def test_curve_values(self):
        self.assertEqual(up.MinMaxCurve.from_export(2).evaluate(0.3, 0.9), 2.0)
        self.assertEqual(up.MinMaxCurve.from_export(['r', 1, 3]).evaluate(0.3, 0.5), 2.0)
        c = up.MinMaxCurve.from_export(['c', 2, [0, 0, 1, 1, 1, 1, 1, 1]])
        self.assertAlmostEqual(c.evaluate(0.5), 1.0)
        stepped = up.MinMaxCurve.from_export(['c', 1, [0, 3, 0, None, 1, 7, 0, 0]])
        self.assertEqual(stepped.evaluate(0.9), 3.0)
        wrapped = up.MinMaxCurve.from_export(['c', 1, [0, 0, 2, 2, 0.5, 1, 2, 2], ['w', 'clamp', 'repeat']])
        self.assertAlmostEqual(wrapped.evaluate(0.75), wrapped.evaluate(0.25))
        two = up.MinMaxCurve.from_export(['cc', 2, [0, 0, 0, 0, 1, 0, 0, 0], [0, 1, 0, 0, 1, 1, 0, 0]])
        self.assertAlmostEqual(two.evaluate(0.5, 0.25), 0.5)
        self.assertEqual(up.MinMaxCurve.from_export(0.1).scalar, up.f32(0.1), 'read back to float32')

    def test_colour_values(self):
        self.assertEqual(up.MinMaxGradient.from_export([1, 0, 0, 1]).evaluate(0.5), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(up.MinMaxGradient.from_export(['r', [0, 0, 0, 0], [1, 1, 1, 1]]).evaluate(0, 0.5), (0.5, 0.5, 0.5, 0.5))
        g = {'c': [0, 0, 0, 0, 1, 1, 1, 1], 'a': [0, 1, 1, 0]}
        self.assertAlmostEqual(up.MinMaxGradient.from_export(['g', g]).evaluate(0.5)[3], 0.5)
        self.assertEqual(up.MinMaxGradient.from_export(['g', {**g, 'fixed': True}]).max_gradient.mode, 1)
        self.assertAlmostEqual(up.MinMaxGradient.from_export(['rg', g]).evaluate(0.0, 0.25)[0], 0.25)

    def test_a_system_reads_back_with_the_defaults(self):
        system = {'name': 's', 'requires': [], 'only': None, 'delay': 0, 'active': True, 'follow': None, 'child': False,
                  'emitter': {'matrix': [1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]},
                  'clock': {'maxAlive': 50}, 'emission': {}, 'start': {}, 'render': {'mode': 'billboard'}, 'material': 0}
        c = up.Config.from_export(system, {})
        self.assertEqual((c.duration, c.looping, c.max_particles, c.rate_over_time.scalar, c.start_lifetime.scalar, c.start_speed.scalar),
                         (5.0, True, 1000, 10.0, 5.0, 5.0))
        self.assertEqual((c.space, c.scaling_mode, c.auto_random_seed, c.shape, c.modules), (up.SPACE_LOCAL, up.SCALING_LOCAL, True, None, {}))
        pose, size = up.emitter_from_export(system)
        self.assertEqual((pose.point((0, 0, 0)), size), ((1.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
        with self.assertRaises(ValueError):
            up.Config.from_export({**system, 'clock': {'maxAlive': 1, 'prewarm': True}}, {})  # a prewarmed loop needs its window
        self.assertEqual(json.loads(json.dumps(up.EXPORT_DEFAULTS)), up.EXPORT_DEFAULTS)

    def test_a_skewed_emitter_moves_points_through_its_matrix(self):
        pose = up.Pose.from_export({'matrix': [1, 0.5, 0, 0, 0, 1, 0, 2, 0, 0, 1, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]})
        self.assertEqual(pose.point((0, 2, 0)), (1.0, 4.0, 0.0))
        self.assertEqual(pose.inverse_vector(pose.vector((0.3, -1, 2))), (0.3, -1.0, 2.0))



MESH = {'vertices': [(0, 0, 0), (2, 0, 0), (0, 1, 0), (2, 1, 0)], 'normals': [(0, 0, -1)] * 4, 'submeshes': [[0, 1, 2, 2, 1, 3, 0, 2, 2]],
        'colors': [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1), (1, 1, 1, 1)]}


def mesh_ps(placement, **kw):
    return make_ps(**{'ShapeModule__enabled': True, 'ShapeModule__type': 6, 'ShapeModule__m_Mesh': {'m_FileID': 0, 'm_PathID': 1},
                      'ShapeModule__placementMode': placement, 'ShapeModule__m_MeshSpawn': multi(0.0), 'ShapeModule__m_UseMeshColors': True,
                      'ShapeModule__m_MeshNormalOffset': 0.0, 'InitialModule__startSpeed': mmc(0, 1.0), 'EmissionModule__m_Bursts': [burst(0.0, 400)],
                      **kw})


class MeshShapes(unittest.TestCase):
    def sim(self, placement, **kw):
        sim = up.Simulation(up.Config.from_trees(mesh_ps(placement, **kw), mesh_of=lambda ref: MESH))
        sim.play()
        sim.step(1 / 60)
        return sim

    def test_triangles_by_area_with_their_normal_and_colours(self):
        sim = self.sim(2, ShapeModule__m_MeshNormalOffset=0.5)
        cdf = sim.c.shape_mesh['cdf']
        self.assertEqual(cdf, [0.5, 1.0, 1.0], 'two triangles of equal area and a degenerate one')
        for p in sim.particles:
            x, y, z = p.position
            self.assertTrue(-1e-9 <= x <= 2 + 1e-9 and -1e-9 <= y <= 1 + 1e-9, p.position)
            self.assertAlmostEqual(z, -0.5 + p.velocity[2] * p.age, places=6)  # offset along the normal, moving along it
            self.assertAlmostEqual(p.velocity[2], -1.0)
        # Uniform over the area: about half on each side of the diagonal x / 2 + y = 1.
        above = sum(1 for p in sim.particles if p.position[0] / 2 + p.position[1] > 1)
        self.assertTrue(150 < above < 250, above)
        # The mesh's vertex colours tint the start colour (Color32).
        self.assertTrue(any(p.start_color[0] < 0.9 for p in sim.particles) and all(abs(sum(p.start_color[:3]) - 1) < 0.02 or p.start_color[:3] != (1, 1, 1)
                                                                                    for p in sim.particles))

    def test_vertices_in_order_with_loop_spawn(self):
        sim = self.sim(0, ShapeModule__m_MeshSpawn=multi(0.0, mode=1))
        self.assertEqual({p.position[:2] for p in sim.particles}, {(0.0, 0.0)}, 'a loop that has not moved stays on the first vertex')
        random = self.sim(0)
        self.assertEqual({p.position[:2] for p in random.particles}, {tuple(map(float, v[:2])) for v in MESH['vertices']})

    def test_a_mesh_shape_needs_its_mesh(self):
        with self.assertRaises(ValueError):
            up.Config.from_trees(mesh_ps(2))


def parent_ps(**kw):
    return make_ps(**{'lengthInSec': 1.0, 'looping': True, 'EmissionModule__rateOverTime': mmc(0, 10.0), 'InitialModule__startLifetime': mmc(0, 0.5),
                      'InitialModule__startSpeed': mmc(0, 2.0), **kw})


class SubEmitters(unittest.TestCase):
    def family(self, child_kw, kind='birth', probability=1.0, parent_kw=None, seed=1):
        parent = up.Simulation.from_trees(parent_ps(**(parent_kw or {})), seed=seed)
        child = up.Simulation.from_trees(make_ps(**{'InitialModule__startLifetime': mmc(0, 0.2), **child_kw}), seed=seed + 1)
        parent.link(child, kind, probability)
        parent.play()
        return parent, child

    def test_birth_children_follow_the_rate_and_bursts_of_each_parent_particle(self):
        parent, child = self.family({'EmissionModule__rateOverTime': mmc(0, 20.0), 'EmissionModule__m_Bursts': [burst(0.0, 3)], 'lengthInSec': 5.0})
        for _ in range(120):
            parent.step(1 / 60)
        # 2 s at 10/s: 19 parents (the 20th is due at 2 s). Each emits a burst of 3 at its birth and, at 20/s from a
        # rate accumulator of its own, children at ages 0.05, 0.1, ... 0.45 (it dies at 0.5): 12 for the 15 that have
        # lived out, 10, 8, 6 and 4 for the last four, born 1.6 to 1.9.
        self.assertEqual(parent.emitted_total, 19)
        self.assertEqual(child.emitted_total, 15 * 12 + 10 + 8 + 6 + 4)
        self.assertFalse(child._emission_events(0.0, 1.0, 1.0), 'a child does not emit on its own')

    def test_death_children_burst_where_a_particle_dies(self):
        parent, child = self.family({'EmissionModule__m_Bursts': [burst(0.0, 4)], 'InitialModule__startLifetime': mmc(0, 5.0)}, kind='death')
        steps = 0
        while parent.dead_total == 0:
            parent.step(1 / 60)
            steps += 1
        self.assertEqual(child.emitted_total, 4 * parent.dead_total)
        # Born where the parent died: 2 u/s along +Z for its 0.5 s (the first particle, about 1 u out).
        for p in child.particles:
            self.assertAlmostEqual(p.position[2], 1.0, delta=0.05)

    def test_a_link_fires_by_its_probability(self):
        none, child = self.family({'EmissionModule__m_Bursts': [burst(0.0, 1)]}, probability=0.0)
        for _ in range(60):
            none.step(1 / 60)
        self.assertEqual(child.emitted_total, 0)
        half, child = self.family({'EmissionModule__m_Bursts': [burst(0.0, 1)]}, probability=0.5, parent_kw={'EmissionModule__rateOverTime': mmc(0, 200.0)})
        for _ in range(60):
            half.step(1 / 60)
        self.assertTrue(0.4 < child.emitted_total / half.emitted_total < 0.6, (child.emitted_total, half.emitted_total))

    def test_a_childs_particles_do_not_depend_on_other_parents_particles(self):
        """Each parent particle's link draws from its own generator: the children of the first parent
        particle are the same whatever the parent emits after it."""
        def first_children(rate):
            parent, child = self.family({'EmissionModule__rateOverTime': mmc(3, 10.0, 30.0), 'InitialModule__startSpeed': mmc(3, 0.0, 1.0)},
                                        parent_kw={'EmissionModule__rateOverTime': mmc(0, rate), 'EmissionModule__m_Bursts': [burst(0.0, 1)]})
            for _ in range(20):
                parent.step(1 / 60)
            return sorted((p.velocity, p.age, p.position) for p in child.particles if p.parent == 0)
        self.assertEqual(first_children(1.0), first_children(50.0))

    def test_a_child_is_placed_in_its_own_space(self):
        # A local child at x 5: its particles are the parent particles' positions seen from it.
        parent, child = self.family({'EmissionModule__m_Bursts': [burst(0.0, 1)], 'InitialModule__startLifetime': mmc(0, 9.0)},
                                    parent_kw={'EmissionModule__m_Bursts': [burst(0.0, 1)]})
        child.pose = child.prev_pose = up.Pose((5.0, 0.0, 0.0))
        parent.step(1 / 60)
        parent.step(1 / 60)
        self.assertTrue(child.particles)
        for p in child.particles:
            self.assertAlmostEqual(p.position[0], -5.0)


class Fields(unittest.TestCase):
    def test_timeline_fields_replace_the_serialized_ones(self):
        base = up.Simulation.from_trees(make_ps(looping=True, EmissionModule__rateOverTime=mmc(0, 10.0)))
        base.play()
        fast = up.Simulation.from_trees(make_ps(looping=True, EmissionModule__rateOverTime=mmc(0, 10.0)))
        fast.play()
        fast.set_fields({'speed': 2.0})
        for _ in range(60):
            base.step(1 / 60)
            fast.step(1 / 60)
        self.assertAlmostEqual(fast.time, 2 * base.time)
        rate = up.Simulation.from_trees(make_ps(looping=True, EmissionModule__rateOverTime=mmc(3, 5.0, 10.0)))
        rate.set_fields({'emission.rate': 30.0})
        self.assertEqual((rate.c.rate_over_time.min_scalar, rate.c.rate_over_time.scalar), (10.0, 30.0), 'the max (scalar) of two constants')
        rate.set_fields({'emission.enabled': 0})
        rate.play()
        for _ in range(60):
            rate.step(1 / 60)
        self.assertEqual(rate.emitted_total, 0)
        tree = make_ps(NoiseModule={'enabled': True, 'strength': mmc(0, 1.0)}, ShapeModule__enabled=True, ShapeModule__type=10)
        sim = up.Simulation.from_trees(tree)
        sim.set_fields({'noise.strength': 3.0, 'shape.radius': 2.0, 'main.startSize': 0.5})
        self.assertEqual((sim.mm(sim.c.modules['NoiseModule']['strength']).scalar, sim.c.shape['radius']['value'], sim.c.start_size[0].scalar), (3.0, 2.0, 0.5))
        self.assertEqual(tree['NoiseModule']['strength']['scalar'], 1.0, 'the trees are left as they were')

    def test_a_timeline_is_read_by_column_name(self):
        timeline = {'columns': ['t', 'matrix', 'rotation', 'scale', 'active', 'speed', 'dissolve.0.amount'], 'length': 2, 'loop': True, 'loopFrom': 1,
                    'frames': [[0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0],
                               [1, 1, 0, 0, 4, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0, 3, 1],
                               [2, 1, 0, 0, 8, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0]]}
        v = up.timeline_values(timeline, 0.5)
        self.assertEqual((v['matrix'][3], v['active'], v['speed'], v['dissolve.0.amount']), (2.0, 1.0, 2.0, 0.5))
        self.assertEqual(up.timeline_values(timeline, 1.5)['active'], 0.0, 'stepped: held from the frame at 1')
        self.assertEqual(up.timeline_values(timeline, 2.5)['matrix'][3], up.timeline_values(timeline, 1.5)['matrix'][3], 'loops from loopFrom')
        pose = up.emitter_pose({'emitter': {'timeline': timeline}, 'follow': None, 'name': 'x'}, 0.5)
        self.assertEqual(pose.point((0, 0, 0)), (2.0, 0.0, 0.0))


if __name__ == '__main__':
    unittest.main(verbosity=1)
