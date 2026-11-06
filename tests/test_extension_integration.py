"""Integration tests for the Nemo Miller Columns extension."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Startup install() reads LANGUAGE first. C keeps the English msgid.
os.environ["LANGUAGE"] = "C"

# Wayland can block inside the Gtk import. Switch before gi loads. Widgets
# do not choose a GDK backend.
if os.environ.get("GDK_BACKEND") == "wayland":
    os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Nemo", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

import miller.data as data
from miller.ui.board import MillerColumnsView
from nemo_miller_columns import MillerColumnsExtension

GTK_READY = False
_checked = Gtk.init_check([])
_ok = _checked[0] if isinstance(_checked, tuple) else bool(_checked)
if _ok:
    Gtk.init([])
    GTK_READY = True


def _existing_pytype(type_name):
    """Return the Python class already registered for ``type_name``."""
    try:
        gtype = GObject.type_from_name(type_name)
    except Exception:
        return None
    if not gtype:
        return None
    pytype = getattr(gtype, "pytype", None)
    if pytype is None:
        raise RuntimeError(
            f"ESCALATE: GObject type {type_name} is already registered "
            "and has no Python class to reuse"
        )
    return pytype


if GTK_READY:
    try:
        class FakeNemoWindow(Gtk.Window):
            __gtype_name__ = "NemoWindow"
    except Exception as error:
        FakeNemoWindow = _existing_pytype("NemoWindow")
        if FakeNemoWindow is None:
            raise RuntimeError(
                "ESCALATE: cannot register or reuse GObject type NemoWindow"
            ) from error

    try:
        class FakeSlot(Gtk.Box):
            __gtype_name__ = "NemoWindowSlot"
            __gsignals__ = {
                "location-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
                "active": (GObject.SignalFlags.RUN_FIRST, None, ()),
                "inactive": (GObject.SignalFlags.RUN_FIRST, None, ()),
            }
    except Exception as error:
        FakeSlot = _existing_pytype("NemoWindowSlot")
        if FakeSlot is None:
            raise RuntimeError(
                "ESCALATE: cannot register or reuse GObject type NemoWindowSlot"
            ) from error
        try:
            signal_names = set(GObject.signal_list_names(FakeSlot.__gtype__))
        except Exception as signal_error:
            raise RuntimeError(
                "ESCALATE: FakeSlot cannot register "
                "location-changed / active / inactive"
            ) from signal_error
        if not {"location-changed", "active", "inactive"}.issubset(signal_names):
            raise RuntimeError(
                "ESCALATE: FakeSlot cannot register "
                "location-changed / active / inactive"
            ) from error


def pump():
    """Drain idle and pending sources without blocking on an empty queue."""
    context = GLib.main_context_default()
    for _ in range(12):
        while context.pending():
            context.iteration(False)
        context.iteration(False)


def wait_loaded(column, seconds=5.0):
    """Pump until a column's first listing has finished."""
    context = GLib.main_context_default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if not getattr(column, "_loading", False):
            return
        time.sleep(0.01)
    raise AssertionError("column load timed out: %s" % column.directory_path)


def _install_column_load_wait():
    """Existing cases read rows immediately, so construction waits."""
    if getattr(MillerColumnsView, "_load_wait_installed", False):
        return
    original = MillerColumnsView._make_column

    def wrapped(self, path):
        column = original(self, path)
        if getattr(MillerColumnsView, "_wait_for_column_load", False):
            wait_loaded(column)
        return column

    MillerColumnsView._make_column = wrapped
    MillerColumnsView._load_wait_installed = True
    MillerColumnsView._wait_for_column_load = True


_install_column_load_wait()


def _control_key(keyval):
    return SimpleNamespace(keyval=keyval, state=Gdk.ModifierType.CONTROL_MASK)



def _dispatch_key(toplevel, keyval, state=0):
    """Send a real key through GTK with a keyboard device."""
    display = Gdk.Display.get_default()
    seat = display.get_default_seat() if display is not None else None
    device = seat.get_keyboard() if seat is not None else None
    if device is None:
        raise unittest.SkipTest("no keyboard device")
    gdk_window = toplevel.get_window()
    if gdk_window is None:
        raise unittest.SkipTest("window is not realized")
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.window = gdk_window
    event.send_event = True
    event.keyval = keyval
    event.state = state
    event.time = Gdk.CURRENT_TIME
    keymap = Gdk.Keymap.get_for_display(display)
    found, keys = keymap.get_entries_for_keyval(keyval)
    if found and keys:
        event.hardware_keycode = keys[0].keycode
    event.set_device(device)
    Gtk.main_do_event(event)
    pump()


class ExtensionIntegrationTests(unittest.TestCase):
    """Slot packing, toolbar, keys, and in-window navigation."""

    def setUp(self):
        if not GTK_READY:
            self.skipTest("GTK cannot init")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_store = data.DEFAULT_STORE_PATH
        self._old_open = data.open_in_same_window
        self._old_flags = data._preference_flags
        data.DEFAULT_STORE_PATH = os.path.join(self.tmp.name, "folder_views.json")
        self.nav_calls = []

        def record(path_or_uri, widget=None):
            self.nav_calls.append((path_or_uri, widget))

        data.open_in_same_window = record
        data._preference_flags = lambda ignore_metadata, inherit_viewer: (False, False)
        self._settings = None
        self._hidden = None
        try:
            self._settings = Gio.Settings.new("org.nemo.preferences")
            self._hidden = self._settings.get_boolean("show-hidden-files")
        except Exception:
            self._settings = None
            self._hidden = None
        self.window = None
        self._build_window()
        self.ext = MillerColumnsExtension()

    def tearDown(self):
        window = getattr(self, "window", None)
        self.window = None
        if window is not None:
            window.destroy()
            pump()
        if hasattr(self, "_old_store"):
            data.DEFAULT_STORE_PATH = self._old_store
            data.open_in_same_window = self._old_open
            data._preference_flags = self._old_flags
        settings = getattr(self, "_settings", None)
        hidden = getattr(self, "_hidden", None)
        if settings is not None and hidden is not None:
            try:
                if settings.get_boolean("show-hidden-files") != hidden:
                    settings.set_boolean("show-hidden-files", hidden)
            except Exception:
                pass

    def _make_slot(self):
        slot = FakeSlot(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        extra_child = Gtk.Label(label="extra")
        extra.pack_start(extra_child, False, False, 0)
        overlay = Gtk.Overlay()
        overlay.add(Gtk.Label(label="directory"))
        slot.pack_start(extra, False, False, 0)
        slot.pack_start(overlay, True, True, 0)
        slot._test_extra = extra
        slot._test_extra_child = extra_child
        slot._test_overlay = overlay
        return slot

    def _build_window(self):
        window = FakeNemoWindow(title="nemo")
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toolbar = Gtk.Toolbar()
        # Nemo 6 nests the view toggles in a box inside a tool item.
        # A non-view button sits in front so Miller is not "the last
        # toolbar item" by accident.
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        search = Gtk.ToggleButton()
        search.set_image(
            Gtk.Image.new_from_icon_name(
                "edit-find-symbolic",
                Gtk.IconSize.BUTTON,
            )
        )
        box.pack_start(search, False, False, 0)
        icons = (
            "view-grid-symbolic",
            "view-list-symbolic",
            "view-compact-symbolic",
        )
        native = []
        for icon in icons:
            button = Gtk.ToggleButton()
            button.set_image(
                Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            )
            button.get_style_context().add_class(Gtk.STYLE_CLASS_FLAT)
            box.pack_start(button, False, False, 0)
            native.append(button)
        tool_item = Gtk.ToolItem()
        tool_item.add(box)
        toolbar.insert(tool_item, -1)
        # The location entry's get_icon_name needs a position. It sits in
        # the same toolbar and must not abort the Miller button.
        entry_item = Gtk.ToolItem()
        entry_item.add(Gtk.Entry())
        toolbar.insert(entry_item, 0)
        notebook = Gtk.Notebook()
        slot = self._make_slot()
        notebook.append_page(slot, None)
        root.pack_start(toolbar, False, False, 0)
        root.pack_start(notebook, True, True, 0)
        window.add(root)
        window.set_default_size(900, 600)
        window.show_all()
        pump()
        self.window = window
        self.notebook = notebook
        self.slot = slot
        self.overlay = slot._test_overlay
        self.extra = slot._test_extra
        self.extra_child = slot._test_extra_child
        self.native = native

    def _add_tab(self):
        slot = self._make_slot()
        self.notebook.append_page(slot, None)
        slot.show_all()
        return slot

    def _switch_to(self, slot):
        index = None
        for page in range(self.notebook.get_n_pages()):
            if self.notebook.get_nth_page(page) is slot:
                index = page
                break
        if index is None:
            raise AssertionError("slot is not a notebook page")
        current = self.notebook.get_current_page()
        previous = self.notebook.get_nth_page(current) if current >= 0 else None
        self.notebook.set_current_page(index)
        if previous is not None and previous is not slot:
            previous.emit("inactive")
        slot.emit("active")

    def _open(self, slot, path):
        slot.emit("location-changed", "", self._uri(path))
        return self.ext.get_widget(self._uri(path), self.window)

    def _focus_inside(self, widget):
        focus = self.window.get_focus()
        while focus is not None:
            if focus is widget:
                return True
            focus = focus.get_parent()
        return False

    def _make_dir(self, name):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(path, exist_ok=True)
        return path

    def _uri(self, path):
        return "file://" + path

    def _store(self):
        with open(data.DEFAULT_STORE_PATH, encoding="utf-8") as handle:
            return json.load(handle)

    def test_preferred_folder_packs_view_and_activates_toggle(self):
        folder = self._make_dir("preferred")
        data.set_miller_view_preferred(folder, True)
        result = self.ext.get_widget(self._uri(folder), self.window)
        toggle = self.window._miller_toggle
        self.assertIsNone(result)
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)
        self.assertFalse(self.overlay.get_visible())
        self.assertIsNotNone(toggle)
        compact = self.native[-1]
        siblings = compact.get_parent().get_children()
        self.assertIs(toggle.get_parent(), compact.get_parent())
        self.assertIs(siblings[siblings.index(compact) + 1], toggle)
        self.assertTrue(
            toggle.get_style_context().has_class(Gtk.STYLE_CLASS_FLAT)
        )
        self.assertEqual(toggle.get_tooltip_text(), "Miller Columns (Ctrl+4)")
        self.assertTrue(toggle.get_active())

    def test_unpreferred_folder_deactivates_and_return_restores_same_slot(self):
        first = self._make_dir("first")
        second = self._make_dir("second")
        data.set_miller_view_preferred(first, True)
        data.set_miller_view_preferred(second, False)
        self.ext.get_widget(self._uri(first), self.window)
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)
        self.ext.get_widget(self._uri(second), self.window)
        self.assertTrue(self.overlay.get_visible())
        self.assertIsNone(self.slot._miller_view)
        self.assertFalse(self.window._miller_toggle.get_active())
        self.ext.get_widget(self._uri(first), self.window)
        self.assertIs(self.slot._miller_view.get_parent(), self.slot)
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)
        self.assertFalse(self.overlay.get_visible())

    def test_reserved_location_is_left_alone(self):
        reserved = self._make_dir("reserved")
        Gio.File.new_for_path(reserved).set_attribute_string(
            data.RESERVED_VIEW_KEY,
            "true",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
        reserved_uri = self._uri(reserved)
        self.assertTrue(data.is_reserved_location(reserved_uri))
        children = list(self.extra.get_children())
        self.assertIsNone(self.ext.get_widget(reserved_uri, self.window))
        self.assertIsNone(self.ext.get_widget(reserved + "/", self.window))
        self.assertEqual(self.slot._miller_uri, os.path.abspath(reserved))
        self.assertIsNone(getattr(self.slot, "_miller_view", None))
        self.assertTrue(self.overlay.get_visible())
        self.assertEqual(list(self.extra.get_children()), children)
        self.assertIs(self.extra.get_children()[0], self.extra_child)
        self.assertTrue(
            self.ext.handle_window_key(self.window, _control_key(Gdk.KEY_4))
        )
        self.assertIsNone(getattr(self.slot, "_miller_view", None))
        self.window._miller_toggle.set_active(True)
        self.assertFalse(self.window._miller_toggle.get_active())
        self.assertIsNone(getattr(self.slot, "_miller_view", None))
        if os.path.exists(data.DEFAULT_STORE_PATH):
            self.assertNotIn(os.path.abspath(reserved), self._store())
        child = self._make_dir("reserved/child")
        data.set_miller_view_preferred(child, True)
        self.assertIsNone(self.ext.get_widget(self._uri(child), self.window))
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)

    def test_show_guard_keeps_overlay_hidden(self):
        folder = self._make_dir("guarded")
        data.set_miller_view_preferred(folder, True)
        self.ext.get_widget(self._uri(folder), self.window)
        view = self.slot._miller_view
        self.assertIsInstance(view, MillerColumnsView)
        self.assertFalse(self.overlay.get_visible())
        self.overlay.show()
        pump()
        self.assertFalse(self.overlay.get_visible())
        self.assertTrue(view.get_visible())
        self.assertIs(self.slot._miller_view, view)

    def test_native_view_button_turns_miller_off(self):
        folder = self._make_dir("native-off")
        data.set_miller_view_preferred(folder, True)
        self.ext.get_widget(self._uri(folder), self.window)
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)
        target = next(button for button in self.native if not button.get_active())
        target.set_active(True)
        self.assertIsNone(self.slot._miller_view)
        self.assertTrue(self.overlay.get_visible())
        self.assertFalse(self.window._miller_toggle.get_active())
        self.assertFalse(data.resolve_miller_view_preferred(folder))
        self.assertIs(self._store()[os.path.abspath(folder)], False)

    def test_miller_clears_previous_view_button_without_switching_it(self):
        folder = self._make_dir("exclusive")
        group = Gtk.ActionGroup.new("views")
        entries = (
            ("IconView", None, "Icon", None, "Icon", 0),
            ("ListView", None, "List", None, "List", 1),
            ("CompactView", None, "Compact", None, "Compact", 2),
        )
        changed = []
        group.add_radio_actions(
            entries,
            0,
            lambda _action, current: changed.append(current.get_current_value()),
        )
        names = (
            ("IconView", "view-grid-symbolic"),
            ("ListView", "view-list-symbolic"),
            ("CompactView", "view-compact-symbolic"),
        )
        for button, (name, icon) in zip(self.native, names):
            action = group.get_action(name)
            button.set_related_action(action)
            # A radio entry with no stock id clears the image. Nemo puts
            # the symbolic name on the action, which paints it back.
            action.set_icon_name(icon)
        self.assertTrue(self.native[0].get_active())
        self.ext.get_widget(self._uri(folder), self.window)
        changed.clear()
        self.window._miller_toggle.set_active(True)
        self.assertTrue(self.window._miller_toggle.get_active())
        self.assertFalse(any(button.get_active() for button in self.native))
        self.assertEqual(changed, [])
        self.assertTrue(group.get_action("IconView").get_active())
        self.native[1].set_active(True)
        self.assertFalse(self.window._miller_toggle.get_active())
        self.assertTrue(self.native[1].get_active())
        self.assertFalse(self.native[0].get_active())
        self.assertTrue(group.get_action("ListView").get_active())
        self.assertEqual(changed, [1])
        for button, icon in zip(
            self.native,
            ("view-grid-symbolic", "view-list-symbolic", "view-compact-symbolic"),
        ):
            self.assertFalse(button.get_label())
            self.assertTrue(button.get_always_show_image())
            self.assertEqual(button.get_image().get_icon_name().icon_name, icon)

    def test_ctrl_4_and_ctrl_1_persist_preference(self):
        folder = self._make_dir("keys")
        self.ext.get_widget(self._uri(folder), self.window)
        self.assertIsNone(getattr(self.slot, "_miller_view", None))
        self.assertFalse(data.resolve_miller_view_preferred(folder))
        self.assertTrue(
            self.ext.handle_window_key(self.window, _control_key(Gdk.KEY_4))
        )
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)
        self.assertTrue(self.window._miller_toggle.get_active())
        self.assertTrue(data.resolve_miller_view_preferred(folder))
        self.assertIs(self._store()[os.path.abspath(folder)], True)
        self.assertTrue(
            self.ext.handle_window_key(self.window, _control_key(Gdk.KEY_1))
        )
        self.assertIsNone(self.slot._miller_view)
        self.assertFalse(self.window._miller_toggle.get_active())
        self.assertFalse(data.resolve_miller_view_preferred(folder))
        self.assertIs(self._store()[os.path.abspath(folder)], False)

    def test_child_navigation_reuses_view(self):
        # The child has no record of its own. Inheritance is what keeps
        # Miller on when Nemo asks again for the child URI.
        data._preference_flags = lambda ignore_metadata, inherit_viewer: (False, True)
        parent = self._make_dir("parent")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        with open(os.path.join(child, "note.txt"), "w", encoding="utf-8") as handle:
            handle.write("n")
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self.nav_calls.clear()
        view.columns[0].select_name("child")
        view.flush_selection()
        self.assertTrue(view.columns[0].handle_key(Gdk.KEY_Right))
        self.assertEqual(self.nav_calls, [])
        self.ext.flush_navigation(self.slot)
        self.assertEqual(len(self.nav_calls), 1)
        self.assertEqual(os.path.abspath(self.nav_calls[-1][0]), os.path.abspath(child))
        self.ext.get_widget(self._uri(child), self.window)
        self.assertIs(self.slot._miller_view, view)
        self.assertGreaterEqual(len(view.columns), 2)

    def test_overlay_guard_hides_before_redraw(self):
        import nemo_miller_columns as extension_module

        folder = self._make_dir("priority")
        data.set_miller_view_preferred(folder, True)
        self.ext.get_widget(self._uri(folder), self.window)
        self.assertFalse(self.overlay.get_visible())
        captured = []
        original = extension_module.GLib.idle_add

        def wrapped(function, *args, priority=GLib.PRIORITY_DEFAULT_IDLE, **kwargs):
            captured.append(priority)
            return original(function, *args, priority=priority, **kwargs)

        extension_module.GLib.idle_add = wrapped
        try:
            self.overlay.show()
        finally:
            extension_module.GLib.idle_add = original
        self.assertTrue(captured)
        self.assertLess(captured[0], Gdk.PRIORITY_REDRAW)
        pump()
        self.assertFalse(self.overlay.get_visible())

    def test_rapid_right_navigates_once_to_the_last_path(self):
        data._preference_flags = lambda ignore_metadata, inherit_viewer: (False, True)
        parent = self._make_dir("rapid")
        child = os.path.join(parent, "child")
        grand = os.path.join(child, "grand")
        os.makedirs(grand)
        self._touch(child)
        self._touch(grand)
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self.nav_calls.clear()
        self._step_into(view, "child")
        self._step_into(view, "grand")
        self.assertEqual(self.nav_calls, [])
        self.ext.flush_navigation(self.slot)
        self.assertEqual(len(self.nav_calls), 1)
        self.assertEqual(os.path.abspath(self.nav_calls[0][0]), os.path.abspath(grand))

    def test_folder_clicks_navigate_once_and_echo_keeps_the_chain(self):
        data._preference_flags = lambda ignore_metadata, inherit_viewer: (False, True)
        parent = self._make_dir("clicked")
        child = os.path.join(parent, "child")
        grand = os.path.join(child, "grand")
        os.makedirs(grand)
        self._touch(child)
        self._touch(grand)
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self.nav_calls.clear()
        view.columns[0].select_name("child")
        view.flush_selection()
        view.columns[1].select_name("grand")
        view.flush_selection()
        self.assertEqual(len(view.columns), 3)
        self.assertEqual(self.nav_calls, [])
        self.ext.flush_navigation(self.slot)
        self.assertEqual(len(self.nav_calls), 1)
        self.assertEqual(os.path.abspath(self.nav_calls[0][0]), os.path.abspath(grand))
        chain = list(view.columns)
        roots = []
        original = view.set_root_path

        def spy(path):
            roots.append(path)
            return original(path)

        view.set_root_path = spy
        self.ext.get_widget(self._uri(grand), self.window)
        self.assertEqual(roots, [])
        self.assertIs(self.slot._miller_view, view)
        self.assertEqual(view.columns, chain)

    def test_file_click_before_flush_cancels_navigation(self):
        parent = self._make_dir("cancel-click")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        with open(os.path.join(parent, "note.txt"), "w", encoding="utf-8") as handle:
            handle.write("n")
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self.nav_calls.clear()
        view.columns[0].select_name("child")
        view.flush_selection()
        view.columns[0].select_name("note.txt")
        view.flush_selection()
        self.ext.flush_navigation(self.slot)
        self.assertEqual(self.nav_calls, [])
        self.assertEqual(len(view.columns), 1)
        self.assertEqual(view.preview_pane.get_title(), "note.txt")

    def test_right_then_left_within_delay_does_not_navigate(self):
        parent = self._make_dir("roundtrip")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self.nav_calls.clear()
        self._step_into(view, "child")
        focused = view.columns[view.focused_index]
        self.assertTrue(focused.handle_key(Gdk.KEY_Left))
        self.ext.flush_navigation(self.slot)
        self.assertEqual(self.nav_calls, [])

    def test_stale_echo_does_not_pull_the_board_back(self):
        data._preference_flags = lambda ignore_metadata, inherit_viewer: (False, True)
        parent = self._make_dir("echo")
        child = os.path.join(parent, "child")
        grand = os.path.join(child, "grand")
        os.makedirs(grand)
        self._touch(child)
        self._touch(grand)
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self._step_into(view, "child")
        self.ext.flush_navigation(self.slot)
        self.nav_calls.clear()
        self._step_into(view, "grand")
        focused = view.focused_index
        column_count = len(view.columns)
        pending = self.slot._miller_nav_target
        self.ext.get_widget(self._uri(child), self.window)
        self.assertEqual(view.focused_index, focused)
        self.assertEqual(len(view.columns), column_count)
        self.assertEqual(os.path.abspath(self.slot._miller_uri), os.path.abspath(grand))
        self.assertEqual(os.path.abspath(self.slot._miller_nav_target), os.path.abspath(pending))
        self.assertIs(self.slot._miller_view, view)

    def test_external_navigation_cancels_pending_and_repositions(self):
        parent = self._make_dir("external-from")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        other = self._make_dir("external-to")
        data.set_miller_view_preferred(parent, True)
        data.set_miller_view_preferred(other, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self.nav_calls.clear()
        self._step_into(view, "child")
        self.assertIsNotNone(self.slot._miller_nav_target)
        self.ext.get_widget(self._uri(other), self.window)
        self.assertIs(self.slot._miller_view, view)
        self.assertEqual(os.path.abspath(view.get_active_path()), os.path.abspath(other))
        self.assertIsNone(self.slot._miller_nav_target)
        self.ext.flush_navigation(self.slot)
        self.assertEqual(self.nav_calls, [])

    def test_ctrl_1_flushes_pending_target_before_destroy(self):
        parent = self._make_dir("toggle-off")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        view = self.slot._miller_view
        self._step_into(view, "child")
        seen = []

        def record(path_or_uri, widget=None):
            seen.append((os.path.abspath(path_or_uri), self.slot._miller_view is not None))

        data.open_in_same_window = record
        self.assertTrue(self.ext.handle_window_key(self.window, _control_key(Gdk.KEY_1)))
        self.assertEqual(seen, [(os.path.abspath(child), True)])
        self.assertIsNone(self.slot._miller_view)

    def test_window_destroy_clears_navigation_source(self):
        parent = self._make_dir("destroy-nav")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        self._step_into(self.slot._miller_view, "child")
        self.assertGreater(self.slot._miller_nav_source, 0)
        window = self.window
        slot = self.slot
        self.window = None
        window.destroy()
        pump()
        self.assertEqual(slot._miller_nav_source, 0)

    def test_navigation_timeout_fires_once(self):
        parent = self._make_dir("timeout")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        data.set_miller_view_preferred(parent, True)
        self.ext.get_widget(self._uri(parent), self.window)
        self.nav_calls.clear()
        self._step_into(self.slot._miller_view, "child")
        deadline = time.monotonic() + 0.5
        while not self.nav_calls and time.monotonic() < deadline:
            pump()
            time.sleep(0.02)
        self.assertEqual(len(self.nav_calls), 1)
        self.assertEqual(os.path.abspath(self.nav_calls[0][0]), os.path.abspath(child))

    def test_new_tab_load_leaves_the_other_board_intact(self):
        parent = self._make_dir("keep")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        data.set_miller_view_preferred(parent, True)
        self._open(self.slot, parent)
        view = self.slot._miller_view
        self._step_into(view, "child")
        columns = len(view.columns)
        uri = self.slot._miller_uri
        other = self._add_tab()
        other_folder = self._make_dir("other-tab")
        data.set_miller_view_preferred(other_folder, True)
        self._open(other, other_folder)
        self.assertIs(self.slot._miller_view, view)
        self.assertEqual(len(view.columns), columns)
        self.assertEqual(os.path.abspath(self.slot._miller_uri), os.path.abspath(uri))
        self.assertIsInstance(other._miller_view, MillerColumnsView)
        self.assertIsNot(other._miller_view, view)

    def test_tab_switch_native_radio_does_not_deactivate_or_write_preference(self):
        folder = self._make_dir("radio-a")
        other_folder = self._make_dir("radio-b")
        data.set_miller_view_preferred(folder, True)
        self._open(self.slot, folder)
        view = self.slot._miller_view
        before = self._store()
        other = self._add_tab()
        self._switch_to(other)
        self._open(other, other_folder)
        target = next(button for button in self.native if not button.get_active())
        target.set_active(True)
        self.assertIs(self.slot._miller_view, view)
        self.assertTrue(self.slot._miller_active)
        self.assertEqual(self._store(), before)
        self.assertIs(self._store()[os.path.abspath(folder)], True)

    def test_switching_back_restores_toolbar_and_focus(self):
        folder = self._make_dir("focus-a")
        self._touch(folder)
        data.set_miller_view_preferred(folder, True)
        self._open(self.slot, folder)
        view = self.slot._miller_view
        self.assertTrue(self.window._miller_toggle.get_active())
        other = self._add_tab()
        plain = self._make_dir("focus-b")
        self._switch_to(other)
        self._open(other, plain)
        self.assertFalse(self.window._miller_toggle.get_active())
        other._test_overlay.grab_focus()
        self.assertFalse(self._focus_inside(view.columns[view.focused_index]))
        self._switch_to(self.slot)
        self.assertTrue(self.window._miller_toggle.get_active())
        pump()
        self.assertTrue(self._focus_inside(view.columns[view.focused_index]))

    def test_focus_returns_to_the_selected_row_after_nemo_steals_it(self):
        parent = self._make_dir("steal")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        for name in ("a.txt", "b.txt", "c.txt"):
            with open(os.path.join(child, name), "w", encoding="utf-8") as handle:
                handle.write(name)
        data.set_miller_view_preferred(parent, True)
        self._open(self.slot, parent)
        view = self.slot._miller_view
        self._step_into(view, "child")
        column = view.columns[view.focused_index]
        column.handle_key(Gdk.KEY_Down)
        view.flush_selection()
        self.assertEqual(column.get_selected().name, "b.txt")
        columns = len(view.columns)
        other = self._add_tab()
        self._switch_to(other)
        self._open(other, self._make_dir("steal-b"))
        other._test_overlay.grab_focus()
        self._switch_to(self.slot)
        # Nemo focuses the native view and GtkNotebook restores the page's
        # last focus child after the ``active`` handlers return.
        self.slot._test_overlay.set_can_focus(True)
        self.slot._test_overlay.grab_focus()
        self.assertFalse(self._focus_inside(column))
        pump()
        focus = self.window.get_focus()
        self.assertTrue(self._focus_inside(column))
        self.assertEqual(getattr(focus, "file_entry", None).name, "b.txt")
        self.assertTrue(column.handle_key(Gdk.KEY_Down))
        view.flush_selection()
        self.assertEqual(column.get_selected().name, "c.txt")
        self.assertEqual(len(view.columns), columns)

    def test_preferred_folder_in_another_tab_activates_on_its_own(self):
        folder_a = self._make_dir("pref-a")
        folder_b = self._make_dir("pref-b")
        data.set_miller_view_preferred(folder_a, False)
        data.set_miller_view_preferred(folder_b, True)
        self._open(self.slot, folder_a)
        self.assertIsNone(getattr(self.slot, "_miller_view", None))
        other = self._add_tab()
        self._switch_to(other)
        self._open(other, folder_b)
        self.assertIsNone(getattr(self.slot, "_miller_view", None))
        self.assertIsInstance(other._miller_view, MillerColumnsView)
        self.assertTrue(other._miller_active)
        self.assertTrue(self.window._miller_toggle.get_active())

    def test_unpreferred_tab_stays_native(self):
        folder_a = self._make_dir("nat-a")
        folder_b = self._make_dir("nat-b")
        data.set_miller_view_preferred(folder_a, True)
        data.set_miller_view_preferred(folder_b, False)
        self._open(self.slot, folder_a)
        view = self.slot._miller_view
        other = self._add_tab()
        self._switch_to(other)
        self._open(other, folder_b)
        self.assertIs(self.slot._miller_view, view)
        self.assertTrue(self.slot._miller_active)
        self.assertIsNone(getattr(other, "_miller_view", None))
        self.assertFalse(getattr(other, "_miller_active", False))
        self.assertTrue(other._test_overlay.get_visible())
        self.assertFalse(self.window._miller_toggle.get_active())

    def test_pending_navigation_waits_until_the_tab_is_active(self):
        data._preference_flags = lambda ignore_metadata, inherit_viewer: (False, True)
        parent = self._make_dir("paused")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        data.set_miller_view_preferred(parent, True)
        self._open(self.slot, parent)
        view = self.slot._miller_view
        self.nav_calls.clear()
        self._step_into(view, "child")
        self.assertEqual(
            os.path.abspath(self.slot._miller_nav_target),
            os.path.abspath(child),
        )
        other = self._add_tab()
        self._switch_to(other)
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            pump()
            time.sleep(0.02)
        self.assertEqual(self.nav_calls, [])
        self.assertEqual(
            os.path.abspath(self.slot._miller_nav_target),
            os.path.abspath(child),
        )
        self._switch_to(self.slot)
        deadline = time.monotonic() + 0.5
        while not self.nav_calls and time.monotonic() < deadline:
            pump()
            time.sleep(0.02)
        self.assertEqual(len(self.nav_calls), 1)
        self.assertEqual(os.path.abspath(self.nav_calls[0][0]), os.path.abspath(child))

    def test_ctrl_4_with_ignore_metadata_does_not_enable_the_other_tab(self):
        data._preference_flags = lambda ignore_metadata, inherit_viewer: (True, False)
        folder = self._make_dir("session-a")
        other_folder = self._make_dir("session-b")
        self._open(self.slot, folder)
        self.assertTrue(
            self.ext.handle_window_key(self.window, _control_key(Gdk.KEY_4))
        )
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)
        self.assertTrue(self.slot._miller_session_enabled)
        other = self._add_tab()
        self._switch_to(other)
        self._open(other, other_folder)
        self.assertIsNone(getattr(other, "_miller_view", None))
        self.assertFalse(getattr(other, "_miller_active", False))
        self.assertIsNone(getattr(other, "_miller_session_enabled", None))
        self.assertIsInstance(self.slot._miller_view, MillerColumnsView)

    def test_columns_receive_file_click_policy(self):
        parent = self._make_dir("policy")
        child = os.path.join(parent, "child")
        os.makedirs(child)
        self._touch(child)
        data.set_miller_view_preferred(parent, True)
        self._open(self.slot, parent)
        view = self.slot._miller_view
        self._step_into(view, "child")
        self.assertGreaterEqual(len(view.columns), 2)
        for column in view.columns:
            self.assertIs(column.file_click_policy.__func__, MillerColumnsExtension._click_policy)
            self.assertIs(column.file_click_policy.__self__, self.ext)
        self.assertIn(self.ext._click_policy(), ("single", "double"))
        original = Gio.Settings.new

        def boom(_schema):
            raise RuntimeError("settings unavailable")

        Gio.Settings.new = boom
        try:
            self.assertEqual(self.ext._click_policy(), "double")
        finally:
            Gio.Settings.new = original

    def _touch(self, directory):
        # Right only moves keyboard focus into a column that has a real row.
        with open(os.path.join(directory, "note.txt"), "w", encoding="utf-8") as handle:
            handle.write("n")

    def _step_into(self, view, name):
        column = view.columns[view.focused_index]
        column.select_name(name)
        view.flush_selection()
        self.assertTrue(column.handle_key(Gdk.KEY_Right))



    def _stub_preview(self):
        calls = []
        original = data.show_file_preview

        def record(path, widget=None):
            calls.append((path, widget))

        data.show_file_preview = record
        self.addCleanup(lambda: setattr(data, "show_file_preview", original))
        return calls

    def _open_file_column(self, folder_name):
        folder = self._make_dir(folder_name)
        self._touch(folder)
        data.set_miller_view_preferred(folder, True)
        self._open(self.slot, folder)
        view = self.slot._miller_view
        column = view.columns[0]
        self.window.present()
        pump()
        return folder, view, column

    def test_leading_space_previews_selected_file(self):
        calls = self._stub_preview()
        folder, view, column = self._open_file_column("space-preview")
        note = os.path.join(folder, "note.txt")
        column.select_name("note.txt")
        view.focus_active_column()
        pump()
        _dispatch_key(self.window, Gdk.KEY_space)
        self.assertEqual(calls, [(os.path.abspath(note), self.window)])

    def test_competing_space_handler_skips_preview(self):
        calls = self._stub_preview()
        folder, view, column = self._open_file_column("space-steal")
        column.select_name("note.txt")
        view.focus_active_column()
        pump()

        def steal(_widget, event):
            if event.keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space):
                return True
            return False

        self.window.connect("key-press-event", steal)
        _dispatch_key(self.window, Gdk.KEY_space)
        self.assertEqual(calls, [])

    def test_space_without_selection_does_not_preview(self):
        calls = self._stub_preview()
        _folder, view, column = self._open_file_column("space-empty")
        view.focus_active_column()
        pump()
        self.assertEqual(column.get_selected_entries(), [])
        _dispatch_key(self.window, Gdk.KEY_space)
        self.assertEqual(calls, [])

    def test_ctrl_and_alt_space_do_not_preview(self):
        calls = self._stub_preview()
        _folder, view, column = self._open_file_column("space-mods")
        column.select_name("note.txt")
        view.focus_active_column()
        pump()
        _dispatch_key(self.window, Gdk.KEY_space, Gdk.ModifierType.CONTROL_MASK)
        _dispatch_key(self.window, Gdk.KEY_space, Gdk.ModifierType.MOD1_MASK)
        self.assertEqual(calls, [])

    def test_space_outside_column_does_not_preview(self):
        calls = self._stub_preview()
        _folder, view, column = self._open_file_column("space-outside")
        column.select_name("note.txt")
        entry = None
        for widget in self.ext._walk(self.window):
            if isinstance(widget, Gtk.Entry):
                entry = widget
                break
        self.assertIsNotNone(entry)
        entry.grab_focus()
        pump()
        _dispatch_key(self.window, Gdk.KEY_space)
        self.assertEqual(calls, [])

    def test_stale_space_marker_does_not_preview_on_next_key(self):
        calls = self._stub_preview()
        _folder, view, column = self._open_file_column("space-stale")
        column.select_name("note.txt")
        self.window._miller_space_column = column
        self.ext.handle_window_key(
            self.window, SimpleNamespace(keyval=Gdk.KEY_x, state=0)
        )
        self.ext.handle_space_preview(
            self.window, SimpleNamespace(keyval=Gdk.KEY_x, state=0)
        )
        self.assertEqual(calls, [])
        self.assertIsNone(self.window._miller_space_column)

    def test_clipboard_keys_do_not_reach_nemo_when_a_column_has_focus(self):
        import miller.ui.column as column_module

        folder = self._make_dir("clipboard-keys")
        with open(os.path.join(folder, "a.txt"), "w", encoding="utf-8") as handle:
            handle.write("a")
        sub = os.path.join(folder, "sub")
        os.makedirs(sub)
        inside = os.path.join(sub, "inside.txt")
        with open(inside, "w", encoding="utf-8") as handle:
            handle.write("in")
        data.set_miller_view_preferred(folder, True)
        self._open(self.slot, folder)
        view = self.slot._miller_view
        column = view.columns[0]
        self.window.present()
        pump()
        column.select_name("a.txt")
        view.focus_active_column()
        pump()

        copied = []
        pasted = []
        original_copy = column_module.copy_to_clipboard
        original_paste = column_module.paste_from_clipboard
        column_module.copy_to_clipboard = (
            lambda paths, cut=False: copied.append((list(paths), cut))
        )
        column_module.paste_from_clipboard = (
            lambda dest: pasted.append(os.path.abspath(dest)) or []
        )
        nemo = []
        group = Gtk.AccelGroup()
        self.window.add_accel_group(group)

        def hit(name):
            def callback(*_args):
                nemo.append(name)
                return True
            return callback

        control = Gdk.ModifierType.CONTROL_MASK
        group.connect(Gdk.KEY_c, control, Gtk.AccelFlags.VISIBLE, hit("copy"))
        group.connect(Gdk.KEY_x, control, Gtk.AccelFlags.VISIBLE, hit("cut"))
        group.connect(Gdk.KEY_v, control, Gtk.AccelFlags.VISIBLE, hit("paste"))
        try:
            _dispatch_key(self.window, Gdk.KEY_c, control)
            self.assertEqual(
                copied,
                [([os.path.abspath(os.path.join(folder, "a.txt"))], False)],
            )
            self.assertEqual(nemo, [])

            copied.clear()
            _dispatch_key(self.window, Gdk.KEY_x, control)
            self.assertEqual(
                copied,
                [([os.path.abspath(os.path.join(folder, "a.txt"))], True)],
            )
            self.assertEqual(nemo, [])

            _dispatch_key(self.window, Gdk.KEY_v, control)
            self.assertEqual(pasted, [os.path.abspath(folder)])
            self.assertEqual(nemo, [])

            copied.clear()
            nemo.clear()
            _dispatch_key(self.window, Gdk.KEY_c, control | Gdk.ModifierType.SHIFT_MASK)
            self.assertEqual(copied, [])
            self.assertEqual(nemo, [])

            self._step_into(view, "sub")
            child = view.columns[1]
            child.select_name("inside.txt")
            child.focus_row()
            pump()
            copied.clear()
            _dispatch_key(self.window, Gdk.KEY_c, control)
            self.assertEqual(copied, [([os.path.abspath(inside)], False)])
            self.assertEqual(nemo, [])

            copied.clear()
            column.begin_rename()
            pump()
            _dispatch_key(self.window, Gdk.KEY_c, control)
            self.assertEqual(copied, [])
            self.assertEqual(nemo, ["copy"])
            column._end_rename_widget()
            pump()

            entry = None
            for widget in self.ext._walk(self.window):
                if isinstance(widget, Gtk.Entry):
                    entry = widget
                    break
            self.assertIsNotNone(entry)
            entry.grab_focus()
            pump()
            copied.clear()
            nemo.clear()
            _dispatch_key(self.window, Gdk.KEY_c, control)
            self.assertEqual(copied, [])
            self.assertEqual(nemo, ["copy"])
        finally:
            column_module.copy_to_clipboard = original_copy
            column_module.paste_from_clipboard = original_paste

class InstallScriptTests(unittest.TestCase):
    """install.sh links the extension and removes only that symlink."""

    def test_install_and_uninstall_with_fake_nemo(self):
        home = tempfile.TemporaryDirectory()
        bindir = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.addCleanup(bindir.cleanup)
        log_path = os.path.join(bindir.name, "nemo.log")
        nemo_path = os.path.join(bindir.name, "nemo")
        with open(nemo_path, "w", encoding="utf-8") as handle:
            handle.write(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    printf '%s\\n' "$*" >> "$NEMO_LOG"
                    exit 0
                    """
                )
            )
        os.chmod(nemo_path, 0o755)
        env = os.environ.copy()
        env["HOME"] = home.name
        env["PATH"] = bindir.name + os.pathsep + env.get("PATH", "")
        env["NEMO_LOG"] = log_path
        script = os.path.join(ROOT, "install.sh")
        self.assertTrue(os.access(script, os.X_OK))
        subprocess.run([script, "--skip-deps"], check=True, env=env)
        link = os.path.join(
            home.name,
            ".local",
            "share",
            "nemo-python",
            "extensions",
            "nemo_miller_columns.py",
        )
        self.assertTrue(os.path.islink(link))
        self.assertEqual(
            os.path.realpath(link),
            os.path.realpath(os.path.join(ROOT, "nemo_miller_columns.py")),
        )
        with open(log_path, encoding="utf-8") as handle:
            installed = handle.read()
        self.assertIn("-q", installed)
        subprocess.run([script, "--uninstall"], check=True, env=env)
        self.assertFalse(os.path.lexists(link))
        with open(log_path, encoding="utf-8") as handle:
            removed = handle.read()
        self.assertGreaterEqual(removed.count("-q"), 2)


class ExtensionSourceTests(unittest.TestCase):
    """The extension must not launch a file manager of its own."""

    def test_source_has_no_external_launcher(self):
        path = os.path.join(ROOT, "nemo_miller_columns.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("nemo-default-view", source)
        self.assertNotIn("nemo --existing-window", source)
        self.assertNotIn("subprocess", source)
