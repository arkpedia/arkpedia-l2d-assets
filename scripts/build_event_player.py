#!/usr/bin/env python3
"""Build a focused browser UI scene from verified original event sources.

This keeps the game's GLES3 shader maths and authored curves. It does not run game
scripts. A pack is not enabled on the site until its rendered entrance is reviewed.
"""
import argparse
import gzip
import hashlib
import json
import math
import re
import subprocess
import zlib
from pathlib import Path

import l2d
import event_transitions as recovery
from event_curve_stream import stream_channels
from UnityPy.export.ShaderConverter import ShaderProgram
from UnityPy.streams import EndianBinaryReader
from UnityPy.helpers import CompressionHelper

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'event-transitions'


def read(path):
    p = Path(path)
    return json.loads(gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def untag(v):
    if isinstance(v, dict):
        if '$float' in v:
            return float(v['$float'])
        return {k: untag(x) for k, x in v.items()}
    if isinstance(v, list):
        return [untag(x) for x in v]
    return v


class Scene:
    def __init__(self, folder):
        self.folder = folder
        self.asset = read(folder / 'asset.json')
        source = self.asset['source']
        self.env = l2d._patch_unitypy().load(l2d.unpack_dat((folder / 'bundle.dat').read_bytes(),
            l2d.SharedBundle(source['bundle'], source['bundleMd5'], 0, 0)))
        self.readers = {str(o.path_id): o for o in self.env.objects}
        self.objects = {k: (o.type.name, o.read_typetree()) for k, o in self.readers.items()}
        self.external = next(iter(self.readers.values())).assets_file.externals


class Builder:
    def __init__(self, folder, output):
        self.home = Scene(folder)
        self.output = output
        self.scenes = {}
        self.shader_ids = {}
        self.texture_ids = {}
        self.material_ids = {}
        self.textures, self.materials, self.shaders = [], [], []
        deps = read(ARCHIVE / 'dependencies.json')
        entry = next(p for p in deps['packs'] if p['asset'] == str((folder / 'asset.json').relative_to(ARCHIVE)))
        for dep in entry['dependencies']:
            for pack in dep['recoveredSharedPacks']:
                self.scenes[dep['external'].split('/')[-1].lower()] = Scene((ARCHIVE / pack).parent)
        # Known shared UI bundles: recover the exact bytes named by this event's
        # archived manifest, never a substitute from today's client.
        available = {entry['name'] for entry in recovery.load_manifest(self.home.asset['source'])['abInfos']}
        for name in ('arts/ui/[uc]fxcommon.ab', 'arts/ui/[uc]common.ab'):
            if name in available:
                self.shared(name)

    def shared(self, name):
        source = self.home.asset['source']
        manifest = recovery.load_manifest(source)
        info = next(x for x in manifest['abInfos'] if x['name'] == name)
        plan = {'info': info, 'source': {k: source[k] for k in ('server', 'resVersion', 'assetBase', 'manifestUrl')}, 'role': 'shared'}
        result = recovery.recover_bundle(plan)
        if result['status'] != 'recovered':
            raise ValueError(result)
        scene = Scene((ARCHIVE / result['asset']).parent)
        for obj in scene.readers.values():
            self.scenes[obj.assets_file.name.lower()] = scene
        index = read(ARCHIVE / 'shared-manifest.json')
        if not any(a['asset'] == result['asset'] for a in index['assets']):
            a = scene.asset
            index['assets'].append({'server': a['server'], 'activityId': a['activityId'], 'bundle': name,
                                    'asset': result['asset'], 'externalFiles': list(a['externalFiles'])})
            index['assets'].sort(key=lambda a: a['asset'])
            index['coverage'].append(result)
            recovery.write_json(ARCHIVE / 'shared-manifest.json', index)
        return scene

    def resolve(self, scene, ref):
        if not ref or str(ref.get('m_PathID', 0)) == '0':
            return None
        if ref['m_FileID']:
            cab = scene.external[ref['m_FileID'] - 1].path.split('/')[-1].lower()
            if cab not in self.scenes:
                raise ValueError(f'Unrecovered graphic dependency {cab}: {ref["m_PathID"]}')
            scene = self.scenes[cab]
        return scene, str(ref['m_PathID'])

    def texture(self, scene, ref, alpha=None, data=False):
        resolved = self.resolve(scene, ref)
        if resolved is None:
            return None
        scene, key = resolved
        alpha_ref = self.resolve(scene, alpha)
        cache_key = id(scene), key, str(alpha_ref[1]) if alpha_ref else None
        if cache_key not in self.texture_ids:
            reader = scene.readers[key]
            image = reader.read().image.convert('RGBA')
            if alpha_ref:
                alpha_scene, alpha_id = alpha_ref
                mask = alpha_scene.readers[alpha_id].read().image.convert('RGB').getchannel('R')
                if mask.size != image.size:
                    raise ValueError('Alpha plane dimensions differ')
                image.putalpha(mask)
            i = len(self.textures)
            filename = f'texture-{i}.webp'
            image.save(self.output / filename, format='WEBP', lossless=True, exact=True)
            settings = scene.objects[key][1]['m_TextureSettings']
            self.textures.append({'file': filename, 'width': image.width, 'height': image.height,
                                  'wrapU': settings['m_WrapU'], 'wrapV': settings['m_WrapV'], 'dataTexture': data})
            self.texture_ids[cache_key] = i
        if data:
            self.textures[self.texture_ids[cache_key]]['dataTexture'] = True
        return self.texture_ids[cache_key]

    def shader(self, scene, ref, keywords=()):
        scene, key = self.resolve(scene, ref)
        cache_key = id(scene), key, tuple(sorted(keywords))
        if cache_key in self.shader_ids:
            return self.shader_ids[cache_key]
        shader = scene.readers[key].read()
        platform = shader.platforms.index(9)  # GLES3, not Vulkan/SMOL-V.
        def entry(values):
            x = values[platform]
            return x[0] if isinstance(x, list) else x
        start, size = entry(shader.offsets), entry(shader.compressedLengths)
        code = CompressionHelper.decompress_lz4(bytes(shader.compressedBlob)[start:start + size], entry(shader.decompressedLengths))
        program = ShaderProgram(EndianBinaryReader(code, endian='<'), scene.readers[key].version)
        form = scene.objects[key][1]['m_ParsedForm']
        p = form['m_SubShaders'][0]['m_Passes'][0]
        entries = p['progVertex'].get('m_SubPrograms') or p['progVertex']['m_PlayerSubPrograms'][0]
        variants = [x for x in entries if x['m_GpuProgramType'] == 4]
        def active(variant):
            subprogram = program.m_SubPrograms[variant['m_BlobIndex']]
            return set(subprogram.m_Keywords) | set(subprogram.m_LocalKeywords or [])
        supported = set().union(*(active(v) for v in variants))
        requested = set(keywords) & supported
        variant = next((v for v in variants if active(v) == requested), None)
        if variant is None:
            raise ValueError(f'No original GLES3 shader variant for {form["m_Name"]}: {sorted(requested)}')
        glsl = program.m_SubPrograms[variant['m_BlobIndex']].m_ProgramCode.decode('utf8')
        def part(stage):
            text = glsl.split(f'#ifdef {stage}\n', 1)[1]
            if stage == 'VERTEX':
                text = text.split('#ifdef FRAGMENT', 1)[0]
            # Drop only the outer stage guard. Disable explicit uniform locations,
            # unsupported by WebGL2; shader expressions remain the game's bytes.
            return text.rsplit('#endif', 1)[0].strip().replace('#define UNITY_SUPPORTS_UNIFORM_LOCATION 1', '#define UNITY_SUPPORTS_UNIFORM_LOCATION 0')
        state = p['m_State']
        props = form['m_PropInfo']['m_Props']
        defaults = {x['m_Name']: [x.get(f'm_DefValue[{i}]', 0) for i in range(4)] for x in props}
        samplers = {x['m_Name']: x['m_DefTexture']['m_DefaultName'] for x in props if x['m_Type'] == 4}
        index = len(self.shaders)
        self.shaders.append({'name': form['m_Name'], 'keywords': sorted(requested), 'vertex': part('VERTEX'), 'fragment': part('FRAGMENT'),
                             'defaults': defaults, 'samplers': samplers, 'blend': [state['rtBlend0']['srcBlend'], state['rtBlend0']['destBlend']]})
        self.shader_ids[cache_key] = index
        return index

    def material(self, scene, ref):
        resolved = self.resolve(scene, ref)
        if resolved is None:
            return None
        scene, key = resolved
        cache_key = id(scene), key
        if cache_key not in self.material_ids:
            m = scene.objects[key][1]
            props = m['m_SavedProperties']
            values = dict(props.get('m_Floats', []))
            values.update({k: [v[c] for c in 'rgba'] for k, v in props.get('m_Colors', [])})
            maps = {}
            for k, v in props.get('m_TexEnvs', []):
                maps[k] = self.texture(scene, v['m_Texture'], data=k != '_MainTex')
                values[k + '_ST'] = [v['m_Scale']['x'], v['m_Scale']['y'], v['m_Offset']['x'], v['m_Offset']['y']]
            index = len(self.materials)
            keywords = m.get('m_ValidKeywords', m.get('m_ShaderKeywords', '').split())
            self.materials.append({'name': m['m_Name'], 'shader': self.shader(scene, m['m_Shader'], keywords), 'values': values, 'textures': maps})
            self.material_ids[cache_key] = index
        return self.material_ids[cache_key]

    def build(self, clip_name, sound_event=None, sound_name=None, sound_bank=None, omit_paths=()):
        home = self.home
        clip_id, clip = next((k, d) for k, (kind, d) in home.objects.items() if kind == 'AnimationClip' and d['m_Name'] == clip_name)
        if clip['m_Legacy']:
            component = next(d for kind, d in home.objects.values() if kind == 'Animation' and any(str(r['m_PathID']) == clip_id for r in d['m_Animations']))
        else:
            controllers = {key for key, (kind, d) in home.objects.items() if kind == 'AnimatorController' and any(str(r['m_PathID']) == clip_id for r in d['m_AnimationClips'])}
            owners = [d for kind, d in home.objects.values() if kind == 'Animator' and str(d['m_Controller']['m_PathID']) in controllers]
            if len(owners) != 1:
                raise ValueError('Mecanim clip needs one unambiguous original Animator owner')
            component = owners[0]
        root_id = str(component['m_GameObject']['m_PathID'])
        transforms = {str(d['m_GameObject']['m_PathID']): d for kind, d in home.objects.values() if kind == 'RectTransform'}
        nodes, paths, bindings, local_positions = [], {}, {}, {}
        def visit(go_id, parent, path):
            go = home.objects[go_id][1]
            if path:
                path += '/' + go['m_Name']
            elif parent is not None:
                path = go['m_Name']
            if any(path == omitted or path.startswith(omitted + '/') for omitted in omit_paths):
                return
            if path.startswith('panel_main/group_ui') and not path.startswith('panel_main/group_ui/root_logo') and path != 'panel_main/group_ui':
                return
            tr = transforms.get(go_id)
            if tr is None:
                return
            n = {'name': go['m_Name'], 'parent': parent, 'active': bool(go['m_IsActive']),
                 'position': [tr['m_AnchoredPosition'][c] for c in 'xy'], 'z': tr['m_LocalPosition']['z'],
                 'size': [tr['m_SizeDelta'][c] for c in 'xy'], 'pivot': [tr['m_Pivot'][c] for c in 'xy'],
                 'anchorMin': [tr['m_AnchorMin'][c] for c in 'xy'], 'anchorMax': [tr['m_AnchorMax'][c] for c in 'xy'],
                 'scale': [tr['m_LocalScale'][c] for c in 'xyz'], 'rotation': [tr['m_LocalRotation'][c] for c in 'xyzw'], 'alpha': 1}
            index = len(nodes)
            nodes.append(n)
            local_positions[index] = [tr['m_LocalPosition'][c] for c in 'xyz']
            paths[path] = index
            for c in go['m_Component']:
                kind, data = home.objects[str(c['component']['m_PathID'])]
                if kind == 'MonoBehaviour':
                    for field, prop in data.items():
                        if not field.startswith(('_textureProperty', '_floatProperty', '_colorProperty')) or not isinstance(prop, dict):
                            continue
                        if not prop.get('enable') or not prop.get('name'):
                            continue
                        uniforms = n.setdefault('uniforms', {})
                        name = prop['name']
                        if field.startswith('_textureProperty'):
                            name += '_ST'
                            uniforms[name] = [prop['tiling']['x'], prop['tiling']['y'], prop['offset']['x'], prop['offset']['y']]
                            for attribute, channel in [('tiling.x', 0), ('tiling.y', 1), ('offset.x', 2), ('offset.y', 3)]:
                                bindings[path, field + '.' + attribute] = f'uniforms.{name}.{channel}'
                        elif field.startswith('_floatProperty'):
                            uniforms[name] = prop['floatValue']
                            bindings[path, field + '.floatValue'] = f'uniforms.{name}'
                        else:
                            uniforms[name] = [prop['color'][channel] for channel in 'rgba']
                            for i, channel in enumerate('rgba'):
                                bindings[path, field + '.color.' + channel] = f'uniforms.{name}.{i}'
                if kind == 'CanvasGroup':
                    n['alpha'] = data['m_Alpha']
                    n['ignoreParentAlpha'] = bool(data['m_IgnoreParentGroups'])
                if kind == 'MonoBehaviour' and 'm_Softness' in data:
                    n['rectMask'] = True
                    n['maskSoftness'] = [data['m_Softness'][c] for c in 'xy']
                    n['maskPadding'] = [data.get('m_Padding', {}).get(c, 0) for c in 'xyzw']
                if kind == 'MonoBehaviour' and data.get('m_Enabled') and ('m_Sprite' in data or '_initAtlas' in data or 'm_UVRect' in data):
                    sprite = None
                    if '_initAtlas' in data:
                        atlas_scene, atlas_id = self.resolve(home, data['_initAtlas'])
                        atlas = atlas_scene.objects[atlas_id][1]
                        sprite = next(s for s in atlas['_sprites'] if s['guid'] == data['_initSpriteId'])
                        plane = atlas['_atlases'][sprite['atlas']]
                        r = sprite['rect']
                        tex = self.texture(atlas_scene, plane['texture'], plane['alpha'])
                        rect = {'x': r['x'], 'y': r['y'], 'width': r['w'], 'height': r['h']}
                        offset, sprite_size, border = {'x': 0, 'y': 0}, [r['w'], r['h']], [0, 0, 0, 0]
                    elif 'm_UVRect' in data:
                        tex = self.texture(home, data['m_Texture'])
                        texture = self.textures[tex] if tex is not None else {'width': 1, 'height': 1}
                        rect = {'x': 0, 'y': 0, 'width': texture['width'], 'height': texture['height']}
                        offset, sprite_size, border = {'x': 0, 'y': 0}, [rect['width'], rect['height']], [0, 0, 0, 0]
                    else:
                        sprite_ref = self.resolve(home, data['m_Sprite'])
                        if sprite_ref:
                            s_scene, s_id = sprite_ref
                            s = s_scene.objects[s_id][1]
                            rd = s['m_RD']
                            if rd['settingsRaw'] & 0x3C:
                                raise ValueError(f'Rotated packed sprite {s["m_Name"]}')
                            tex = self.texture(s_scene, rd['texture'], rd['alphaTexture'])
                            rect, offset = rd['textureRect'], rd['textureRectOffset']
                            sprite_size = [s['m_Rect'][c] for c in ('width', 'height')]
                            border = [s['m_Border'][c] for c in 'xyzw']
                        else:
                            tex, rect, offset, sprite_size, border = None, {'x': 0, 'y': 0, 'width': 1, 'height': 1}, {'x': 0, 'y': 0}, [1, 1], [0, 0, 0, 0]
                    n['image'] = {'texture': tex, 'rect': rect,
                                  'offset': offset, 'spriteSize': sprite_size,
                                  'border': border, 'color': [data['m_Color'][c] for c in 'rgba'],
                                  'material': self.material(home, data['m_Material']),
                                  'type': data.get('m_Type', 0), 'preserveAspect': bool(data.get('m_PreserveAspect', False)),
                                  'fill': data.get('m_FillAmount', 1), 'fillMethod': data.get('m_FillMethod', 4), 'clockwise': bool(data.get('m_FillClockwise', False)), 'origin': data.get('m_FillOrigin', 0),
                                  'rotated': bool(sprite and sprite['rotate'])}
                    if 'm_UVRect' in data:
                        n['image']['uvRect'] = [data['m_UVRect'][key] for key in ('x', 'y', 'width', 'height')]
                    if '_angleSpan' in data:
                        n['image']['arc'] = [data['_startAngle'], data['_angleSpan']]
            for child in tr['m_Children']:
                child_go = str(home.objects[str(child['m_PathID'])][1]['m_GameObject']['m_PathID'])
                visit(child_go, index, path)
        visit(root_id, None, '')
        curves = []
        fields = {'m_AnchoredPosition.x': 'position.0', 'm_AnchoredPosition.y': 'position.1', 'm_SizeDelta.x': 'size.0', 'm_SizeDelta.y': 'size.1',
                  **{f'm_Anchor{edge}.{c}': f'anchor{edge}.{i}' for edge in ('Min', 'Max') for i, c in enumerate('xy')},
                  **{f'm_Pivot.{c}': f'pivot.{i}' for i, c in enumerate('xy')},
                  **{f'm_UVRect.{c}': f'image.uvRect.{i}' for i, c in enumerate(('x', 'y', 'width', 'height'))},
                  'm_FillAmount': 'image.fill',
                  'm_FillOrigin': 'image.origin', 'm_FillMethod': 'image.fillMethod', 'm_FillClockwise': 'image.clockwise',
                  **{f'm_Padding.{c}': f'maskPadding.{i}' for i, c in enumerate('xyzw')},
                  'm_Alpha': 'alpha', 'm_IsActive': 'active', '_angleSpan': 'image.arc.1', **{f'm_Color.{c}': f'image.color.{i}' for i, c in enumerate('rgba')}}
        if not clip['m_Legacy']:
            channels = stream_channels(clip['m_MuscleClip']['m_Clip']['data'], clip['m_MuscleClip']['m_StartTime'])
            hashed_paths = {}
            for path, node in paths.items():
                hashed = zlib.crc32(path.encode())
                if hashed in hashed_paths:
                    raise ValueError('Ambiguous native animation path hash')
                hashed_paths[hashed] = (path, node)
            offset = 0
            for binding in clip['m_ClipBindingConstant']['genericBindings']:
                kind, attribute = binding['typeID'], binding['attribute']
                dimension = {1: 3, 2: 4, 3: 3}.get(attribute, 0) if kind == 4 else 1
                if not dimension:
                    raise ValueError('Unsupported Mecanim transform binding')
                values = channels[offset:offset + dimension]
                offset += dimension
                if len(values) != dimension:
                    raise ValueError('Truncated Mecanim channel bindings')
                if binding['path'] not in hashed_paths:
                    continue  # Game-only non-RectTransform objects are omitted.
                path, node = hashed_paths[binding['path']]
                if kind == 4:
                    prop = {1: 'localPosition', 2: 'rotation', 3: 'scale'}[attribute]
                    if prop == 'localPosition':
                        nodes[node][prop] = local_positions[node]
                    properties = [f'{prop}.{i}' for i in range(dimension)]
                elif kind in (1, 114, 224, 225):
                    if attribute in {zlib.crc32(name.encode()) for name in ('m_BlocksRaycasts', 'm_Interactable', 'm_RaycastTarget')}:
                        continue
                    candidates = {**fields, **{attribute: prop for (bound_path, attribute), prop in bindings.items() if bound_path == path}}
                    matches = [prop for name, prop in candidates.items() if zlib.crc32(name.encode()) == attribute]
                    if len(matches) != 1:
                        raise ValueError(f'Unresolved Mecanim UI property {kind}/{attribute} at {path}')
                    properties = matches
                else:
                    continue  # Mesh/particle effects are outside the UI renderer.
                for prop, keys in zip(properties, values):
                    if prop.startswith('image.') and 'image' not in nodes[node]:
                        continue
                    if prop.startswith('maskPadding.') and not nodes[node].get('rectMask'):
                        continue
                    if prop == 'active':
                        keys = [[k[0], k[1], None, None, *k[4:]] for k in keys]
                    curves.append({'node': node, 'property': prop, 'keys': keys})
            if offset != len(channels):
                raise ValueError('Unbound Mecanim channels')
        for c in clip['m_FloatCurves']:
            if c['path'] not in paths or c['attribute'] in ('m_BlocksRaycasts', 'm_Interactable', 'm_RaycastTarget'):
                continue
            prop = bindings.get((c['path'], c['attribute']), fields.get(c['attribute']))
            if prop is None:
                raise ValueError(f'Unknown curve property {c["attribute"]}')
            # Disabled Graphics and game-only text components have no rendered
            # image. Their colour curves must not create a phantom drawable.
            if prop.startswith('image.') and 'image' not in nodes[paths[c['path']]]:
                continue
            if prop.startswith('maskPadding.') and not nodes[paths[c['path']]].get('rectMask'):
                continue
            keys = self.keys(c['curve']['m_Curve'])
            if prop in ('active', 'image.origin', 'image.fillMethod', 'image.clockwise'):
                keys = [[k[0], k[1], None, None, *k[4:]] for k in keys]
            curves.append({'node': paths[c['path']], 'property': prop, 'keys': keys})
        if any(c['path'] in paths for c in clip.get('m_EulerCurves', [])):
            raise ValueError('Legacy Euler rotation conversion needs separate verification')
        for field, prop, channels in [('m_PositionCurves', 'localPosition', 'xyz'), ('m_ScaleCurves', 'scale', 'xyz'), ('m_RotationCurves', 'rotation', 'xyzw')]:
            for c in clip[field]:
                if c['path'] in paths:
                    if prop == 'localPosition':
                        nodes[paths[c['path']]][prop] = local_positions[paths[c['path']]]
                    for i, channel in enumerate(channels):
                        curves.append({'node': paths[c['path']], 'property': f'{prop}.{i}', 'keys': self.keys(c['curve']['m_Curve'], channel)})
        result = {'schemaVersion': 1, 'source': str((home.folder / 'asset.json').relative_to(ARCHIVE)),
                'omittedPaths': sorted(omit_paths),
                'clip': clip_name, 'duration': home.asset['animations'][next(i for i, a in enumerate(home.asset['animations']) if a['id'] == clip_id)]['durationSeconds'],
                'width': 1280, 'height': 720, 'nodes': nodes, 'curves': curves, 'textures': self.textures, 'materials': self.materials, 'shaders': self.shaders}
        # Original archives remain lossless. Display-only RGB can use ordinary
        # image compression; alpha and shader masks/data must retain their values.
        from PIL import Image
        for texture in self.textures:
            if not texture['dataTexture']:
                path = self.output / texture['file']
                with Image.open(path) as image:
                    image.save(path, format='WEBP', quality=92, alpha_quality=100, method=6, exact=True)
        if sound_event:
            cue = next(e for e in clip['m_Events'] if e['functionName'] == 'EventPlayUI' and e['data'] == sound_event)
            bank = self.shared(sound_bank)
            audio_id, audio = next((key, d) for key, (kind, d) in bank.objects.items() if kind == 'AudioClip' and d['m_Name'] == sound_name)
            samples = bank.readers[audio_id].read().samples
            if len(samples) != 1:
                raise ValueError('Expected one reviewed sound sample')
            waveform = next(iter(samples.values()))
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'wav', '-i', 'pipe:0',
                            '-c:a', 'libmp3lame', '-b:a', '192k', '-map_metadata', '-1', '-write_xing', '1',
                            '-y', str(self.output / 'entrance.mp3')], input=waveform, check=True)
            (self.output / 'entrance.wav').unlink(missing_ok=True)
            result['sound'] = {'file': 'entrance.mp3', 'encoding': 'mp3-192k', 'sourceDecodedSha256': hashlib.sha256(waveform).hexdigest(), 'offset': cue['time'], 'duration': audio['m_Length'],
                               'originalName': sound_name, 'originalCue': sound_event, 'source': str((bank.folder / 'asset.json').relative_to(ARCHIVE))}
        return result

    @staticmethod
    def keys(keys, channel=None):
        def value(key, prop):
            v = key[prop][channel] if channel else key[prop]
            return v if math.isfinite(v) else None
        return [[k['time'], value(k, 'value'), value(k, 'inSlope'), value(k, 'outSlope'), k.get('weightedMode', 0),
                 (k.get('inWeight', {}).get(channel, 1/3) if channel else k.get('inWeight', 1/3)),
                 (k.get('outWeight', {}).get(channel, 1/3) if channel else k.get('outWeight', 1/3))] for k in keys]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--asset', required=True)
    p.add_argument('--clip', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--sound-event')
    p.add_argument('--sound-name')
    p.add_argument('--sound-bank')
    p.add_argument('--omit-path', action='append', default=[])
    args = p.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    b = Builder((ARCHIVE / args.asset).parent, output)
    pack = b.build(args.clip, args.sound_event, args.sound_name, args.sound_bank, args.omit_path)
    write(output / 'scene.json', pack)
    recovery.dependency_report()
    print(len(pack['nodes']), 'nodes;', len(pack['textures']), 'textures;', len(pack['shaders']), 'original shaders')


if __name__ == '__main__':
    main()
