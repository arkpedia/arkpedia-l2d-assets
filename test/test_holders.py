"""Shared FX retain per-instance identities, material externals and action clocks."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from holders import graft_holders


def ref(pid, file=0):
    return {'m_PathID': pid, 'm_FileID': file}


class Holders(unittest.TestCase):
    def test_duplicate_prefabs_and_external_material_tuples(self):
        source = {
            1: ('GameObject', {'m_Component': [{'component': ref(2)}, {'component': ref(3)}]}),
            2: ('Transform', {'m_Father': ref(0), 'm_Children': []}),
            3: ('Material', {'m_SavedProperties': {'m_TexEnvs': [('main', {'m_Texture': ref(4, 1)})]}}),
        }
        objects = {
            1: ('GameObject', {'m_Component': [{'component': ref(2)}]}),
            2: ('MonoBehaviour', {'_holders': [ref(10), ref(20), ref(30)], '_particles': []}),
        }
        for i, action in [(10, 1), (20, 2), (30, 3)]:
            objects[i] = ('MonoBehaviour', {'m_GameObject': ref(i+1), '_effectPath': 'missing' if i == 30 else 'fx', '_action': action})
            objects[i+1] = ('GameObject', {'m_Component': [{'component': ref(i)}, {'component': ref(i+2)}]})
            objects[i+2] = ('Transform', {'m_Children': [], 'm_Father': ref(0)})
        class Shared:
            def prefab(self, name):
                return None if name == 'missing' else (1, source.get, lambda pid: ('mesh', pid), lambda pid: ('texture', pid), lambda r: 'CAB-source')
        read, mesh, texture, external = graft_holders(1, objects.get, lambda p: None, lambda p: None, lambda r: 'CAB-outfit', Shared())
        controller = read(2)[1]
        self.assertEqual(controller['_holders'], [ref(30)])
        self.assertEqual([p['action'] for p in controller['_particles']], [1, 2])
        roots = []
        for i in (10, 20):
            tr = read(i+2)[1]['m_Children'][0]['m_PathID']
            roots.append(tr)
            self.assertEqual(read(tr)[1]['m_Father'], ref(i+2))
            self.assertEqual(read(i+1)[1]['m_Component'], [{'component': ref(i+2)}])
            # The transform's sibling material shares the same instance mapper.
            material = tr - 1
            texref = read(material)[1]['m_SavedProperties']['m_TexEnvs'][0][1]['m_Texture']
            self.assertEqual(external(texref), 'CAB-source')
            self.assertEqual(mesh(tr), ('mesh', 2))
            self.assertEqual(texture(tr), ('texture', 2))
        self.assertNotEqual(roots[0], roots[1])
        self.assertEqual(objects[12][1]['m_Children'], [])
        self.assertEqual(read(31)[1], objects[31][1])
