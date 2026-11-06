"""Domain tests for Nemo.MenuProvider discovery."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk

try:
    gi.require_version("Nemo", "3.0")
    from gi.repository import Nemo
except (ValueError, ImportError):
    Nemo = None

from miller import nemo_extensions
from miller.nemo_actions import MenuTarget
from miller.nemo_extensions import extension_items, menu_providers


class _OkProvider(GObject.GObject, Nemo.MenuProvider if Nemo else GObject.GObject):
    def __init__(self):
        super().__init__()
        self.file_calls = []
        self.background_calls = []

    def get_file_items(self, window, files):
        self.file_calls.append((window, list(files)))
        item = Nemo.MenuItem(name="Ok::file", label="File item", tip="", icon="")
        return [item]

    def get_background_items(self, window, folder):
        self.background_calls.append((window, folder))
        item = Nemo.MenuItem(name="Ok::bg", label="Background item", tip="", icon="")
        return [item]


class _BoomProvider(GObject.GObject, Nemo.MenuProvider if Nemo else GObject.GObject):
    def get_file_items(self, window, files):
        raise RuntimeError("provider exploded")

    def get_background_items(self, window, folder):
        raise RuntimeError("provider exploded")


@unittest.skipIf(Nemo is None, "Nemo typelib unavailable")
class NemoExtensionTests(unittest.TestCase):
    def setUp(self):
        nemo_extensions._PROVIDER_CACHE.clear()

    def tearDown(self):
        nemo_extensions._PROVIDER_CACHE.clear()

    def test_discovery_disabled_cache_and_skip_raise(self):
        providers = menu_providers(disabled=[])
        names = {type(item).__name__ for item in providers}
        self.assertIn("_OkProvider", names)
        self.assertIn("_BoomProvider", names)
        again = menu_providers(disabled=[])
        first = next(item for item in providers if type(item).__name__ == "_OkProvider")
        second = next(item for item in again if type(item).__name__ == "_OkProvider")
        self.assertIs(first, second)
        filtered = menu_providers(disabled=[type(first).__gtype__.name])
        self.assertFalse(any(type(item).__name__ == "_OkProvider" for item in filtered))

    def test_file_versus_background_and_lookup_stub(self):
        window = Gtk.Window()
        looked = []

        class _Info:
            def __init__(self, uri):
                self.uri = uri

        def lookup(uri):
            looked.append(uri)
            return _Info(uri)

        with tempfile.TemporaryDirectory() as tmp:
            note = os.path.join(tmp, "note.txt")
            with open(note, "w", encoding="utf-8") as handle:
                handle.write("x")
            target = MenuTarget(paths=[note], parent_dir=tmp)
            files = extension_items(window, target, False, lookup=lookup)
            self.assertTrue(any(item.get_property("label") == "File item" for item in files))
            self.assertTrue(looked)
            looked.clear()
            background = extension_items(window, MenuTarget(paths=[], parent_dir=tmp), True, lookup=lookup)
            self.assertTrue(any(item.get_property("label") == "Background item" for item in background))
            boom = next(
                item
                for item in menu_providers(disabled=[])
                if type(item).__name__ == "_BoomProvider"
            )
            self.assertTrue(boom)
            self.assertEqual(extension_items(None, target, False, lookup=lookup), [])
        window.destroy()


if __name__ == "__main__":
    unittest.main()
