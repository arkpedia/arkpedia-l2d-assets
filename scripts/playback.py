"""The illustration controller's concurrent tracks and voice choreography, without guessing names."""
import math

VERSION = 1


def controller_playback(roots, read):
    seen, stack, controllers = set(), list(roots), []
    while stack:
        pid = stack.pop()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        entry = read(pid)
        if not entry:
            continue
        kind, tree = entry
        local = lambda r: r.get('m_PathID') if isinstance(r, dict) and not r.get('m_FileID', 0) else None
        if kind == 'GameObject':
            stack.extend(local(c.get('component') or c.get('second')) for c in tree.get('m_Component', []))
        elif kind in ('Transform', 'RectTransform'):
            stack.extend(local(c) for c in tree.get('m_Children', []))
            stack.append(local(tree.get('m_GameObject')))
        elif kind == 'MonoBehaviour' and ('_extraTrackAnimations' in tree or ('_lipSyncDatas' in tree and '_voiceToActionIds' in tree)):
            controllers.append(tree)
    if len(controllers) > 1:
        raise ValueError('Several illustration playback controllers')
    if not controllers:
        return None
    tree = controllers[0]
    extra = [{'track': x['trackIndex'], 'name': x['animationName']} for x in tree.get('_extraTrackAnimations', [])]
    actions = {a['actionName']: a for a in tree.get('_lipSyncDatas', [])}
    voices = []
    for mapping in tree.get('_voiceToActionIds', []):
        a = actions[mapping['actionId']]
        voices.append({'id': mapping['voiceName'],
                       'body': [{'name': x['name'], 'time': x['time'], 'mix': x['blendInDuration']} for x in a['animationInfos']],
                       'mouth': [{'name': x['name'], 'time': x['time']} for x in a['eventInfos']]})
    out = {'extraTracks': extra, 'voiceActions': voices}
    validate_playback(out)
    return out if extra or voices else None


def validate_playback(out, animations=None):
    def name(n):
        return isinstance(n, str) and bool(n) and (animations is None or n in animations)
    def time(t):
        return isinstance(t, (int, float)) and math.isfinite(t) and t >= 0
    tracks = set()
    for x in out['extraTracks']:
        if not isinstance(x['track'], int) or not 2 <= x['track'] <= 16 or x['track'] in tracks or not name(x['name']):
            raise ValueError('Invalid extra animation track')
        tracks.add(x['track'])
    ids = set()
    for a in out['voiceActions']:
        if not isinstance(a['id'], str) or not a['id'].startswith('CN_') or a['id'] in ids or not a['body']:
            raise ValueError('Invalid voice action')
        ids.add(a['id'])
        for kind in ('body', 'mouth'):
            previous = -1
            for x in a[kind]:
                if not name(x['name']) or not time(x['time']) or x['time'] < previous or (kind == 'body' and not time(x['mix'])):
                    raise ValueError('Invalid voice action timeline')
                previous = x['time']


def bundle_playback(bundle, dyn_illust_id):
    import l2d
    env = l2d._patch_unitypy().load(bundle)
    objects = {o.path_id: o for o in env.objects}
    container = {p: r.path_id for p, r in env.container.items()}
    def read(pid):
        o = objects.get(pid)
        return (o.type.name, o.read_typetree()) if o else None
    return controller_playback(l2d.illust_prefab_roots(container, dyn_illust_id), read)
