"""Instantiate the client's shared illustration FX under their authored holder transforms.

Each instance gets a distinct object namespace: Unity path IDs are local to a bundle,
and a prefab can be instantiated more than once. Materials, meshes, clips and external
texture references retain their source bundle; action groups retain the illustration's clock.
"""
from copy import deepcopy


def graft_holders(root_go, read, mesh_of, texture_of, external_of, shared):
    patches, origins = {}, {}
    next_id = -(1 << 80)

    def entry(pid):
        if pid in patches:
            return patches[pid]
        origin = origins.get(pid)
        if origin:
            reader, source_id, remap, source_external, _, _ = origin
            source = reader(source_id)
            if source:
                patches[pid] = (source[0], clone(source[1], remap, source_external))
                return patches[pid]
            return None
        return read(pid)

    def clone(value, remap, source_external):
        if isinstance(value, (list, tuple)):
            values = [clone(v, remap, source_external) for v in value]
            return tuple(values) if isinstance(value, tuple) else values
        if isinstance(value, dict):
            if 'm_PathID' in value and 'm_FileID' in value:
                out = deepcopy(value)
                if out['m_PathID']:
                    if out['m_FileID'] == 0:
                        out['m_PathID'] = remap(out['m_PathID'])
                    else:
                        out['_externalCAB'] = source_external(value)
                return out
            return {k: clone(v, remap, source_external) for k, v in value.items()}
        return value

    go = entry(root_go)[1]
    for component in go.get('m_Component', []):
        controller_id = component['component']['m_PathID']
        found = entry(controller_id)
        if not found or '_holders' not in found[1]:
            continue
        controller = deepcopy(found[1])
        remaining = []
        for ref in controller.get('_holders') or []:
            holder = entry(ref.get('m_PathID'))
            if not holder:
                remaining.append(ref)
                continue
            holder = holder[1]
            prefab = getattr(shared, 'prefab', lambda _: None)(holder.get('_effectPath', ''))
            if not prefab:
                remaining.append(ref)
                continue
            source_root, reader, source_mesh, source_texture, source_external = prefab
            def instance_mapper(reader, source_external, source_mesh, source_texture):
                ids = {}
                def remap(pid):
                    nonlocal next_id
                    if pid not in ids:
                        ids[pid] = next_id
                        next_id -= 1
                        origins[ids[pid]] = (reader, pid, remap, source_external, source_mesh, source_texture)
                    return ids[pid]
                return remap
            remap = instance_mapper(reader, source_external, source_mesh, source_texture)

            holder_go = holder['m_GameObject']['m_PathID']
            kind, holder_tree = entry(holder_go)
            holder_tree = deepcopy(holder_tree)
            # The holder's only script has now been executed by the importer; it must not
            # be mistaken for an unknown runtime script by the layer safety checks.
            holder_tree['m_Component'] = [c for c in holder_tree['m_Component']
                                          if c['component']['m_PathID'] != ref['m_PathID']]
            patches[holder_go] = (kind, holder_tree)
            holder_transform = next(c['component']['m_PathID'] for c in entry(holder_go)[1]['m_Component']
                                    if entry(c['component']['m_PathID'])[0] in ('Transform', 'RectTransform'))
            instantiated = remap(source_root)
            root_transform = next(c['component']['m_PathID'] for c in entry(instantiated)[1]['m_Component']
                                  if entry(c['component']['m_PathID'])[0] in ('Transform', 'RectTransform'))
            kind, transform = entry(holder_transform)
            transform = deepcopy(transform)
            transform.setdefault('m_Children', []).append({'m_FileID': 0, 'm_PathID': root_transform})
            patches[holder_transform] = (kind, transform)
            kind, transform = entry(root_transform)
            transform = deepcopy(transform)
            transform['m_Father'] = {'m_FileID': 0, 'm_PathID': holder_transform}
            patches[root_transform] = (kind, transform)
            controller.setdefault('_particles', []).append({'particle': {'m_FileID': 0, 'm_PathID': holder_go}, 'action': holder['_action']})
        controller['_holders'] = remaining
        patches[controller_id] = (found[0], controller)

    def mesh(pid):
        source = origins.get(pid)
        return source[4](source[1]) if source else mesh_of(pid)

    def texture(pid):
        source = origins.get(pid)
        return source[5](source[1]) if source else texture_of(pid)

    def external(ref):
        return ref.get('_externalCAB') or external_of(ref)

    return entry, mesh, texture, external
