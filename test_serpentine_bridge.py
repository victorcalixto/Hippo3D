"""Tests for the Hippo3D <-> Serpentine3D .serp bridge."""

import json
import os
import sys
import tempfile
import unittest
import types

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Minimal Blender/BPY mocks so Hippo3D.main can import outside Blender.
class _FakeAppHandlers(types.ModuleType):
    __path__ = []
    def __init__(self):
        super().__init__('bpy.app.handlers')
    def persistent(self, f):
        return f

class _FakeApp(types.ModuleType):
    __path__ = []
    def __init__(self):
        super().__init__('bpy.app')
        self.version = (4,2,0)
        self.handlers = _FakeAppHandlers()

class _FakeBpyProps(types.ModuleType):
    __path__ = []
    def __init__(self):
        super().__init__('bpy.props')
    def __getattr__(self, name):
        return lambda *a, **k: None

class _FakeBpyTypes(types.ModuleType):
    __path__ = []
    def __init__(self):
        super().__init__('bpy.types')
    def __getattr__(self, name):
        return type(name, (), {})

class _FakeBpy(types.ModuleType):
    __path__ = []
    def __init__(self):
        super().__init__('bpy')
        self.app = _FakeApp()
        self.types = _FakeBpyTypes()
        self.props = _FakeBpyProps()
        self.context = types.SimpleNamespace(
            selected_objects=[],
            scene=types.SimpleNamespace(cursor_location=(0,0,0)),
            view_layer=types.SimpleNamespace(active=None),
        )
        self.ops = types.SimpleNamespace(mesh=lambda **k: None, object=lambda **k: None)
        self.data = types.SimpleNamespace(meshes={}, objects={}, __iter__=lambda self: iter(()))
        self.utils = types.SimpleNamespace(
            register_class=lambda c: None, unregister_class=lambda c: None,
            previews=types.SimpleNamespace(new=lambda *a, **k: {}),
        )

sys.modules['bpy'] = _FakeBpy()
sys.modules['bpy.app'] = sys.modules['bpy'].app
sys.modules['bpy.app.handlers'] = sys.modules['bpy'].app.handlers
sys.modules['bpy.types'] = sys.modules['bpy'].types
sys.modules['bpy.props'] = sys.modules['bpy'].props

mathutils = types.ModuleType('mathutils')
mathutils.Vector = lambda *a: list(a)
mathutils.Matrix = lambda *a: list(a)
mathutils.Quaternion = lambda *a: list(a)
mathutils.Euler = lambda *a: list(a)
mathutils.geometry = types.SimpleNamespace()
sys.modules['mathutils'] = mathutils

gpu = types.ModuleType('gpu')
gpu.state = types.SimpleNamespace(blend=lambda *a, **k: None)
sys.modules['gpu'] = gpu

gpu_extras = types.ModuleType('gpu_extras')
gpu_extras.__path__ = []
gpu_extras.batch = types.ModuleType('gpu_extras.batch')
gpu_extras.batch.batch_for_shader = lambda *a, **k: None
sys.modules['gpu_extras'] = gpu_extras
sys.modules['gpu_extras.batch'] = gpu_extras.batch

for mod_name in ['blf', 'bgl', 'bmesh', 'addon_utils', 'nodeitems_utils']:
    sys.modules[mod_name] = types.ModuleType(mod_name)
    sys.modules[mod_name].__path__ = []

blf = sys.modules['blf']
blf.load = lambda *a, **k: 0
blf.position = lambda *a, **k: None
blf.size = lambda *a, **k: None
blf.draw = lambda *a, **k: None

bgl = sys.modules['bgl']
bgl.glEnable = lambda *a: None
bgl.glDisable = lambda *a: None
bgl.glBlendFunc = lambda *a: None
bgl.GL_BLEND = 0

bmesh = sys.modules['bmesh']
bmesh.new = lambda: types.SimpleNamespace(faces=[], edges=[], verts=[], to_mesh=lambda *a: None, free=lambda: None, from_mesh=lambda *a: None)
bmesh.from_edit_mesh = lambda *a: bmesh.new()

bpy_extras = types.ModuleType('bpy_extras')
bpy_extras.__path__ = []
bpy_extras.view3d_utils = types.SimpleNamespace()
bpy_extras.io_utils = types.SimpleNamespace(ExportHelper=object, ImportHelper=object, orientation_helper_factory=lambda *a, **k: type('OH', (), {}))
sys.modules['bpy_extras'] = bpy_extras

addon_utils = sys.modules['addon_utils']
addon_utils.enable = lambda *a, **k: None
addon_utils.disable = lambda *a, **k: None

nodeitems_utils = sys.modules['nodeitems_utils']
nodeitems_utils.NodeCategory = object

from Hippo3D import main as _main_mod


class SerpentineBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.occ = _main_mod.hippo_load_occ_core()

    def _make_box_shape(self):
        sid = self.occ.make_box_mesh(1.0, 2.0, 3.0, 0.1)["shape_id"]
        self.addCleanup(self.occ.delete_shape, sid)
        return sid

    def test_native_serp_round_trip(self):
        sid = self._make_box_shape()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "box.serp")
            ok, msg = self.occ.export_serp(sid, path)
            self.assertTrue(ok, msg)
            self.assertTrue(os.path.isfile(path))

            import zipfile
            with zipfile.ZipFile(path, 'r') as z:
                doc = json.loads(z.read('document.json').decode('utf-8'))
            self.assertIn("version", doc)
            self.assertIn("objects", doc)

            imported = self.occ.import_serp(path)
            self.assertEqual(len(imported), 1)
            for imported_id in imported:
                self.occ.delete_shape(imported_id)

    def test_native_serp_multi_export(self):
        s1 = self._make_box_shape()
        s2 = self.occ.make_sphere_mesh(1.0, 0.1)["shape_id"]
        self.addCleanup(self.occ.delete_shape, s2)

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "multi.serp")
            ok, msg = self.occ.export_serp_multi([s1, s2], path)
            self.assertTrue(ok, msg)

            imported = self.occ.import_serp(path)
            self.assertEqual(len(imported), 2)
            for imported_id in imported:
                self.occ.delete_shape(imported_id)

    def test_serp_out_missing_shape_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.serp")
            ok, msg = self.occ.export_serp(-1, path)
            self.assertFalse(ok)

    def test_serp_in_missing_file_reports_error(self):
        with self.assertRaises(Exception):
            self.occ.import_serp("/nonexistent/path/file.serp")


if __name__ == "__main__":
    unittest.main()
