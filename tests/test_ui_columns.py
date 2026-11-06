"""Widget tests for the Miller column, preview, and board."""

import base64
import inspect
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# This session's Wayland backend blocks inside the Gtk import. XWayland is
# available on DISPLAY, and the widgets do not choose a GDK backend.
if os.environ.get("GDK_BACKEND") == "wayland":
    os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from miller.data import FileEntry, get_file_metadata
from miller.i18n import install, reset
from miller.ui import MillerColumn, MillerColumnsView, PreviewPane
from miller.ui.column_list import ColumnListMixin
from miller.ui import dnd as dnd_module
from miller.ui.column import BACKGROUND_MENU_LABELS, CONTEXT_MENU_LABELS
from miller.ui.open_with import build_app_chooser
from miller.ui.preview import _PREVIEW_METADATA_FIELDS, icon_tint, pack_metadata_rows
import miller.ui.column as column_module
import miller.ui.nemo_items as nemo_items
import miller.ui.open_with as open_with_module

_GTK_READY = None

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_EXPECTED_MENU = (
    "Open",
    "Open With",
    "Open in New Tab",
    "Open in New Window",
    "New Folder",
    "Cut",
    "Copy",
    "Paste",
    "Rename\u2026",
    "Move to Trash",
    "Copy Path",
    "Properties",
)


def ensure_gtk():
    """Initialize GTK once. Returns false when there is no display."""
    global _GTK_READY
    if _GTK_READY is not None:
        return _GTK_READY
    checked = Gtk.init_check([])
    ok = checked[0] if isinstance(checked, tuple) else bool(checked)
    if ok:
        Gtk.init([])
    _GTK_READY = bool(ok)
    return _GTK_READY


def _walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from _walk(child)


def pump():
    context = GLib.main_context_default()
    for _ in range(8):
        while context.pending():
            context.iteration(False)


def pump_until(predicate, seconds=2.0):
    """Iterate the main context until ``predicate`` is true or time runs out."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pump()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def wait_loaded(column, seconds=5.0):
    """Pump until ``column`` has finished its initial listing."""
    if not pump_until(lambda: not getattr(column, "_loading", False), seconds=seconds):
        raise AssertionError("column load timed out: %s" % column.directory_path)


def install_column_load_wait():
    """Block test construction on the initial listing.

    Production leaves the column loading. Existing tests read rows as soon
    as the column exists, so the test double waits unless a case opts out
    with ``MillerColumnsView._wait_for_column_load``.
    """
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


install_column_load_wait()


def _delete_trashed(original_path: str) -> None:
    """Drop a file this test just trashed so the user trash stays clean."""
    info_dir = os.path.expanduser("~/.local/share/Trash/info")
    files_dir = os.path.expanduser("~/.local/share/Trash/files")
    if not os.path.isdir(info_dir):
        return
    for name in os.listdir(info_dir):
        info_path = os.path.join(info_dir, name)
        try:
            with open(info_path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        if original_path not in text:
            continue
        base = name[: -len(".trashinfo")] if name.endswith(".trashinfo") else name
        trashed_file = os.path.join(files_dir, base)
        if os.path.lexists(trashed_file):
            if os.path.isdir(trashed_file) and not os.path.islink(trashed_file):
                os.rmdir(trashed_file)
            else:
                os.remove(trashed_file)
        try:
            os.remove(info_path)
        except OSError:
            pass


def _has_ancestor(widget, ancestor) -> bool:
    current = widget.get_parent()
    while current is not None:
        if current is ancestor:
            return True
        current = current.get_parent()
    return False


def _walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from _walk(child)


def _label_texts(widget):
    texts = []
    for child in _walk(widget):
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
    return texts


def _entry_names(column):
    names = []
    for row in column.listbox.get_children():
        entry = getattr(row, "file_entry", None)
        if entry is not None:
            names.append(entry.name)
    return names


def _visible_names(column):
    """Names the column search still shows. Hidden rows stay in the list."""
    names = []
    for row in column.listbox.get_children():
        entry = getattr(row, "file_entry", None)
        if entry is None or not row.get_child_visible():
            continue
        names.append(entry.name)
    return names


def _focused_column(view):
    """Return the column that contains the window's current focus widget."""
    toplevel = view.get_toplevel()
    focused = toplevel.get_focus() if isinstance(toplevel, Gtk.Window) else None
    if focused is None:
        return None
    for column in view.columns:
        current = focused
        while current is not None:
            if current is column:
                return column
            current = current.get_parent()
    return None


def _current_columns(view):
    return [
        column
        for column in view.columns
        if column.get_style_context().has_class("mc-column-current")
    ]


def _menu_outline(menu):
    outline = []
    for item in menu.get_children():
        if isinstance(item, Gtk.SeparatorMenuItem):
            outline.append("---")
            continue
        label = item.get_label()
        if not label:
            texts = _label_texts(item)
            label = texts[0] if texts else None
        outline.append(label)
    return outline


def _row_named(column, name):
    for row in column.listbox.get_children():
        entry = getattr(row, "file_entry", None)
        if entry is not None and entry.name == name:
            return row
    raise AssertionError(name)


def _button_event(column, row, event_type, state=0):
    allocation = row.get_allocation()
    # EventButton.new, not Event.new: the signal handler reads button as an
    # int, and the generic Event wrapper hides that field.
    event = Gdk.EventButton.new(event_type)
    window = column.listbox.get_window()
    if window is not None:
        event.window = window
    event.button = 1
    event.x = allocation.x + max(allocation.width, 1) / 2
    event.y = allocation.y + max(allocation.height, 1) / 2
    event.x_root = event.x
    event.y_root = event.y
    event.state = state
    event.time = Gdk.CURRENT_TIME
    return event


def _click_row(column, row):
    """Press, pump, select, release, pump. Mirrors a ListBox that selects on release.

    Multiple mode makes ``select_row`` add a row. A plain click replaces the
    selection, so the previous rows are cleared without a notify first.
    """
    column.listbox.emit("button-press-event", _button_event(column, row, Gdk.EventType.BUTTON_PRESS))
    pump()
    column._suppress_select = True
    try:
        column.listbox.unselect_all()
    finally:
        column._suppress_select = False
    column.listbox.select_row(row)
    column.listbox.emit("button-release-event", _button_event(column, row, Gdk.EventType.BUTTON_RELEASE))
    pump()



def _dispatch_key(toplevel, keyval, state=0):
    """Send a real key through GTK with a keyboard device.

    Direct handler calls never reach ListBox's Space binding. This is
    the same path the verification probe used.
    """
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


def _focused_row_entry(view):
    """Return the FileEntry on the focused ListBoxRow, or None."""
    toplevel = view.get_toplevel()
    focused = toplevel.get_focus() if isinstance(toplevel, Gtk.Window) else None
    current = focused
    while current is not None:
        if isinstance(current, Gtk.ListBoxRow):
            return getattr(current, "file_entry", None)
        current = current.get_parent()
    return None


class MillerColumnsUiTests(unittest.TestCase):
    def setUp(self):
        if not ensure_gtk():
            self.skipTest("GTK display unavailable")
        self.window = Gtk.Window()
        self.window.set_default_size(960, 480)
        self._nemo_saved = {
            "load_actions": nemo_items.load_actions,
            "load_scripts": nemo_items.load_scripts,
            "extension_items": nemo_items.extension_items,
            "run_action": nemo_items.run_action,
            "run_script": nemo_items.run_script,
        }
        nemo_items.load_actions = lambda *a, **k: []
        nemo_items.load_scripts = lambda *a, **k: []
        nemo_items.extension_items = lambda *a, **k: []

    def tearDown(self):
        saved = getattr(self, "_nemo_saved", None)
        if saved is not None:
            for name, value in saved.items():
                setattr(nemo_items, name, value)
        window = getattr(self, "window", None)
        if window is not None:
            window.destroy()
            pump()

    def mount(self, view, wait=True):
        child = self.window.get_child()
        if child is not None:
            self.window.remove(child)
            child.destroy()
        self.window.add(view)
        self.window.show_all()
        rect = Gdk.Rectangle()
        rect.x = 0
        rect.y = 0
        rect.width = 960
        rect.height = 480
        view.size_allocate(rect)
        pump()
        if wait:
            for column in view.columns:
                wait_loaded(column)
        view._scroll_to_end()
        return view

    def test_constructor_signatures(self):
        column_params = list(inspect.signature(MillerColumn.__init__).parameters)
        self.assertEqual(
            column_params,
            [
                "self",
                "directory_path",
                "on_select",
                "on_activate_dir",
                "on_nav_left",
                "on_nav_right",
                "show_hidden",
            ],
        )
        self.assertIs(inspect.signature(MillerColumn.__init__).parameters["show_hidden"].default, False)

        view_params = list(inspect.signature(MillerColumnsView.__init__).parameters)
        self.assertEqual(
            view_params,
            ["self", "initial_path", "target_window", "on_active_path_changed"],
        )
        preview_params = list(inspect.signature(PreviewPane.update_file).parameters)
        self.assertEqual(preview_params, ["self", "path"])

    def test_root_column_lists_folder_then_file_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "docs")
            os.makedirs(folder)
            with open(os.path.join(tmp, "readme.txt"), "w", encoding="utf-8") as handle:
                handle.write("hello")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            self.assertEqual(_entry_names(column), ["docs", "readme.txt"])
            badges = [
                child.get_text()
                for child in _walk(column)
                if isinstance(child, Gtk.Label) and child.get_style_context().has_class("mc-column-count")
            ]
            self.assertEqual(badges, ["2 items"])
            self.assertIsNone(column.get_selected())

            single = os.path.join(tmp, "only")
            os.makedirs(single)
            with open(os.path.join(single, "one.txt"), "w", encoding="utf-8") as handle:
                handle.write("x")
            alone = self.mount(MillerColumnsView(single))
            alone_badges = [
                child.get_text()
                for child in _walk(alone.columns[0])
                if isinstance(child, Gtk.Label) and child.get_style_context().has_class("mc-column-count")
            ]
            self.assertEqual(alone_badges, ["1 item"])

    def test_selecting_folder_appends_column_without_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "inside.txt"), "w", encoding="utf-8") as handle:
                handle.write("inside")
            with open(os.path.join(tmp, "readme.txt"), "w", encoding="utf-8") as handle:
                handle.write("hello")
            view = self.mount(MillerColumnsView(tmp))
            view.columns[0].select_name("sub")
            view.flush_selection()
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(sub))
            self.assertEqual(view.focused_index, 1)
            self.assertEqual(view.get_active_path(), os.path.abspath(sub))
            self.assertEqual(view.preview_pane.preview_kind, "")
            self.assertEqual(view.preview_pane.get_title(), "")
            # The pane stays inside its own scrolled window. That window is
            # not in the column strip until a file is selected. GTK inserts a
            # viewport, so the pane's direct parent is never the strip.
            self.assertFalse(_has_ancestor(view.preview_pane, view.columns_box))

            view.columns[0].select_name("sub")
            view.flush_selection()
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(sub))

    def test_selecting_file_shows_text_preview_and_drops_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            note = os.path.join(tmp, "note.txt")
            with open(note, "w", encoding="utf-8") as handle:
                handle.write("hello miller")
            calls = []
            view = self.mount(MillerColumnsView(tmp, on_active_path_changed=calls.append))
            view.columns[0].select_name("sub")
            view.flush_selection()
            self.assertEqual(len(view.columns), 2)
            before = len(calls)
            view.columns[0].select_name("note.txt")
            view.flush_selection()
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(view.focused_index, 0)
            self.assertEqual(view.get_active_path(), os.path.abspath(tmp))
            self.assertEqual(calls[before:], [os.path.abspath(tmp)])
            self.assertEqual(view.preview_pane.get_title(), "note.txt")
            self.assertEqual(view.preview_pane.preview_kind, "text")
            blob = "\n".join(_label_texts(view.preview_pane))
            meta = get_file_metadata(note)
            self.assertTrue(meta["size_human"] in blob or "Size" in blob)
            self.assertIn("hello miller", blob)
            self.assertNotIn("Criado em", blob)
            buttons = [child for child in _walk(view.preview_pane) if isinstance(child, Gtk.Button)]
            self.assertEqual(buttons, [])

    def test_folder_click_reports_opened_directory_and_keeps_accent(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "inside.txt"), "w", encoding="utf-8") as handle:
                handle.write("inside")
            with open(os.path.join(tmp, "note.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            calls = []
            view = self.mount(MillerColumnsView(tmp, on_active_path_changed=calls.append))
            _click_row(view.columns[0], _row_named(view.columns[0], "sub"))
            view.flush_selection()
            self.assertEqual(calls[-1], os.path.abspath(sub))
            self.assertEqual(view.focused_index, 1)
            self.assertEqual(view.get_active_path(), os.path.abspath(sub))
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(_current_columns(view), [view.columns[0]])

            marked = len(calls)
            _click_row(view.columns[0], _row_named(view.columns[0], "sub"))
            view.flush_selection()
            self.assertEqual(len(calls), marked)
            self.assertEqual(view.focused_index, 1)
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(_current_columns(view), [view.columns[0]])

            view.columns[0].select_name("note.txt")
            view.flush_selection()
            self.assertEqual(calls[-1], os.path.abspath(tmp))
            self.assertEqual(view.focused_index, 0)
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(view.preview_pane.get_title(), "note.txt")
            self.assertEqual(_current_columns(view), [view.columns[0]])

    def test_keyboard_down_on_folder_waits_for_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            alpha = os.path.join(tmp, "alpha")
            os.makedirs(alpha)
            with open(os.path.join(tmp, "note.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.handle_key(Gdk.KEY_Down)
            self.assertEqual(column.get_selected().name, "alpha")
            self.assertFalse(column._keyboard_select)
            self.assertEqual(len(view.columns), 1)
            self.assertNotEqual(view._select_timer, 0)
            self.assertEqual(view._pending[1].name, "alpha")
            self.assertEqual(_current_columns(view), [column])
            view.flush_selection()
            self.assertEqual(view._select_timer, 0)
            self.assertIsNone(view._pending)
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(alpha))

    def test_repeated_keyboard_down_opens_only_the_last_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            alpha = os.path.join(tmp, "alpha")
            beta = os.path.join(tmp, "beta")
            os.makedirs(alpha)
            os.makedirs(beta)
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.handle_key(Gdk.KEY_Down)
            column.handle_key(Gdk.KEY_KP_Down)
            self.assertEqual(column.get_selected().name, "beta")
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(view._pending[1].name, "beta")
            view.flush_selection()
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(beta))

    def test_folder_click_appends_without_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            view = self.mount(MillerColumnsView(tmp))
            _click_row(view.columns[0], _row_named(view.columns[0], "sub"))
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(sub))
            self.assertEqual(view._select_timer, 0)
            self.assertIsNone(view._pending)

    def test_keyboard_down_onto_file_previews_without_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "alpha"))
            with open(os.path.join(tmp, "note.txt"), "w", encoding="utf-8") as handle:
                handle.write("hello")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.handle_key(Gdk.KEY_Down)
            self.assertEqual(column.get_selected().name, "alpha")
            self.assertEqual(len(view.columns), 1)
            column.handle_key(Gdk.KEY_Down)
            self.assertEqual(column.get_selected().name, "note.txt")
            self.assertEqual(view.preview_pane.get_title(), "note.txt")
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(view._select_timer, 0)
            self.assertIsNone(view._pending)

    def test_click_cancels_pending_keyboard_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            alpha = os.path.join(tmp, "alpha")
            beta = os.path.join(tmp, "beta")
            os.makedirs(alpha)
            os.makedirs(beta)
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.handle_key(Gdk.KEY_Down)
            self.assertEqual(view._pending[1].name, "alpha")
            self.assertEqual(len(view.columns), 1)
            _click_row(column, _row_named(column, "beta"))
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(beta))
            self.assertEqual(view._select_timer, 0)
            self.assertIsNone(view._pending)
            view.flush_selection()
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(beta))

    def test_png_preview_kind_is_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = os.path.join(tmp, "dot.png")
            with open(png, "wb") as handle:
                handle.write(_PNG_1X1)
            view = self.mount(MillerColumnsView(tmp))
            view.columns[0].select_name("dot.png")
            view.flush_selection()
            self.assertEqual(view.preview_pane.preview_kind, "image")
            self.assertEqual(view.preview_pane.get_title(), "dot.png")

    def test_right_moves_to_child_and_selects_first_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "child")
            os.makedirs(os.path.join(child, "alpha"))
            with open(os.path.join(child, "notes.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            calls = []
            view = self.mount(MillerColumnsView(tmp, on_active_path_changed=calls.append))
            view.columns[0].select_name("child")
            view.flush_selection()
            handled = view.columns[0].handle_key(Gdk.KEY_Right)
            self.assertTrue(handled)
            self.assertEqual(view.focused_index, 1)
            selected = view.columns[1].get_selected()
            self.assertIsNotNone(selected)
            self.assertEqual(selected.name, "alpha")
            self.assertEqual(view.get_active_path(), os.path.abspath(child))
            self.assertIn(os.path.abspath(child), calls)
            self.assertEqual(calls[-1], os.path.abspath(child))
            self.assertEqual(len(view.columns), 2)
            # First row is a folder: highlight only, no grandchild and no preview.
            self.assertEqual(view.preview_pane.preview_kind, "")
            self.assertEqual(view.preview_pane.get_title(), "")
            self.assertFalse(_has_ancestor(view.preview_pane, view.columns_box))

    def test_right_into_files_shows_preview_for_first_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "child")
            os.makedirs(child)
            note = os.path.join(child, "alpha.txt")
            with open(note, "w", encoding="utf-8") as handle:
                handle.write("hello preview")
            with open(os.path.join(child, "beta.txt"), "w", encoding="utf-8") as handle:
                handle.write("second")
            view = self.mount(MillerColumnsView(tmp))
            view.columns[0].select_name("child")
            view.flush_selection()
            handled = view.columns[0].handle_key(Gdk.KEY_Right)
            self.assertTrue(handled)
            self.assertEqual(view.focused_index, 1)
            selected = view.columns[1].get_selected()
            self.assertIsNotNone(selected)
            self.assertEqual(selected.name, "alpha.txt")
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.preview_pane.get_title(), "alpha.txt")
            self.assertEqual(view.preview_pane.preview_kind, "text")
            self.assertTrue(_has_ancestor(view.preview_pane, view.columns_box))
            blob = "\n".join(_label_texts(view.preview_pane))
            self.assertIn("hello preview", blob)

    def test_left_returns_to_parent_and_prunes_grandchildren(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "child")
            alpha = os.path.join(child, "alpha")
            os.makedirs(alpha)
            with open(os.path.join(child, "notes.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            view = self.mount(MillerColumnsView(tmp))
            view.columns[0].select_name("child")
            view.flush_selection()
            view.columns[0].handle_key(Gdk.KEY_Right)
            self.assertEqual(view.focused_index, 1)
            view.columns[1].select_name("notes.txt")
            view.flush_selection()
            view.columns[1].select_name("alpha")
            view.flush_selection()
            self.assertEqual(len(view.columns), 3)
            self.assertEqual(view.columns[2].directory_path, os.path.abspath(alpha))
            view.columns[1].handle_key(Gdk.KEY_Left)
            self.assertEqual(view.focused_index, 0)
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[0].directory_path, os.path.abspath(tmp))
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(child))
            self.assertEqual(view.get_active_path(), os.path.abspath(tmp))

    def test_left_on_root_climbs_to_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = os.path.join(tmp, "parent")
            child = os.path.join(parent, "child")
            os.makedirs(child)
            with open(os.path.join(child, "keep.txt"), "w", encoding="utf-8") as handle:
                handle.write("k")
            view = self.mount(MillerColumnsView(child))
            self.assertEqual(view.columns[0].directory_path, os.path.abspath(child))
            view.columns[0].handle_key(Gdk.KEY_Left)
            self.assertEqual(view.focused_index, 0)
            self.assertEqual(view.columns[0].directory_path, os.path.abspath(parent))
            self.assertEqual(view.get_active_path(), os.path.abspath(parent))
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(child))
            selected = view.columns[0].get_selected()
            self.assertIsNotNone(selected)
            self.assertEqual(os.path.abspath(selected.path), os.path.abspath(child))

    def test_set_root_path_keeps_left_columns_or_replaces_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root")
            sub = os.path.join(root, "sub")
            deep = os.path.join(sub, "deep")
            os.makedirs(deep)
            elsewhere = os.path.join(tmp, "elsewhere")
            os.makedirs(elsewhere)
            calls = []
            view = self.mount(MillerColumnsView(root, on_active_path_changed=calls.append))
            view.columns[0].select_name("sub")
            view.flush_selection()
            view.columns[1].select_name("deep")
            view.flush_selection()
            self.assertEqual(len(view.columns), 3)
            left = view.columns[0]
            child = view.columns[2]
            marked = len(calls)
            view.set_root_path(sub)
            self.assertIs(view.columns[0], left)
            self.assertEqual(view.columns[0].directory_path, os.path.abspath(root))
            self.assertEqual(view.focused_index, 1)
            self.assertEqual(view.get_active_path(), os.path.abspath(sub))
            self.assertIs(view.columns[2], child)
            self.assertEqual(view.columns[2].directory_path, os.path.abspath(deep))
            self.assertEqual(calls[-1], os.path.abspath(sub))
            self.assertEqual(view.preview_pane.preview_kind, "")

            again = len(calls)
            view.set_root_path(sub)
            self.assertEqual(len(calls), again)
            self.assertEqual(marked < len(calls), True)

            view.set_root_path(elsewhere)
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(view.columns[0].directory_path, os.path.abspath(elsewhere))
            self.assertEqual(view.focused_index, 0)
            self.assertEqual(view.get_active_path(), os.path.abspath(elsewhere))
            self.assertEqual(calls[-1], os.path.abspath(elsewhere))

    def test_create_rename_and_trash(self):
        home = os.path.expanduser("~")
        with tempfile.TemporaryDirectory(dir=home) as tmp:
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            created = column.create_folder("Docs")
            self.assertTrue(os.path.isdir(created))
            self.assertEqual(os.path.basename(created), "Docs")
            selected = column.get_selected()
            self.assertIsNotNone(selected)
            self.assertEqual(selected.name, "Docs")

            column.commit_rename("Docs2")
            renamed = os.path.join(os.path.abspath(tmp), "Docs2")
            self.assertTrue(os.path.isdir(renamed))
            self.assertFalse(os.path.exists(created))
            self.assertEqual(column.get_selected().name, "Docs2")
            self.assertIn("Docs2", _entry_names(column))
            self.assertNotIn("Docs", _entry_names(column))

            column.delete_selected()
            if os.path.exists(renamed):
                self.skipTest("trash unavailable")
            self.assertNotIn("Docs2", _entry_names(column))
            self.assertIsNone(column.get_selected())
            _delete_trashed(renamed)

    def test_invalid_rename_leaves_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            created = column.create_folder("Docs")
            column.commit_rename("bad/name")
            self.assertTrue(os.path.isdir(created))
            self.assertEqual(column.get_selected().name, "Docs")
            self.assertIn("Docs", _entry_names(column))

    def test_context_menu_labels_and_properties(self):
        self.assertEqual(CONTEXT_MENU_LABELS, _EXPECTED_MENU)
        calls = []
        original = column_module.show_item_properties
        column_module.show_item_properties = (
            lambda paths: calls.append(list(paths)) or True
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("meta")
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                column.select_name("note.txt")
                self.assertFalse(hasattr(column, "build_properties_dialog"))
                column._menu_properties()
                self.assertEqual(calls, [[os.path.abspath(note)]])
        finally:
            column_module.show_item_properties = original

    def test_show_hidden_is_local_to_the_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "visible.txt"), "w", encoding="utf-8") as handle:
                handle.write("v")
            with open(os.path.join(tmp, ".secret"), "w", encoding="utf-8") as handle:
                handle.write("s")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            self.assertIn("visible.txt", _entry_names(column))
            self.assertNotIn(".secret", _entry_names(column))
            column.set_show_hidden(True)
            self.assertIn(".secret", _entry_names(column))
            self.assertTrue(column.show_hidden)
            column.set_show_hidden(False)
            self.assertNotIn(".secret", _entry_names(column))
            self.assertFalse(column.show_hidden)

    def test_ctrl_h_toggles_hidden_on_that_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "visible.txt"), "w", encoding="utf-8") as handle:
                handle.write("v")
            with open(os.path.join(tmp, ".secret"), "w", encoding="utf-8") as handle:
                handle.write("s")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            handled = column.handle_key(Gdk.KEY_h, Gdk.ModifierType.CONTROL_MASK)
            self.assertTrue(handled)
            self.assertIn(".secret", _entry_names(column))
            column.handle_key(Gdk.KEY_h, Gdk.ModifierType.CONTROL_MASK)
            self.assertNotIn(".secret", _entry_names(column))

    def test_return_on_file_calls_open_file_default(self):
        opened = []
        original = column_module.open_file_default
        column_module.open_file_default = lambda path: opened.append(path)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("open-me")
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                column.select_name("note.txt")
                handled = column.handle_key(Gdk.KEY_Return, 0)
                self.assertTrue(handled)
                self.assertEqual(opened, [os.path.abspath(note)])
        finally:
            column_module.open_file_default = original

    def test_empty_directory_placeholder_ignores_arrows(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "empty")
            os.makedirs(empty)
            view = self.mount(MillerColumnsView(empty))
            column = view.columns[0]
            self.assertEqual(_entry_names(column), [])
            self.assertIsNone(column.get_selected())
            blob = "\n".join(_label_texts(column))
            self.assertIn("Empty folder", blob)
            column.handle_key(Gdk.KEY_Down)
            column.handle_key(Gdk.KEY_Up)
            column.handle_key(Gdk.KEY_Right)
            self.assertIsNone(column.get_selected())
            self.assertEqual(len(view.columns), 1)


    def test_portuguese_catalog_empty_folder_and_badge(self):
        install(languages=["pt"])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                view = self.mount(MillerColumnsView(tmp))
                blob = "\n".join(_label_texts(view.columns[0]))
                self.assertIn("Pasta vazia", blob)
            with tempfile.TemporaryDirectory() as tmp:
                os.makedirs(os.path.join(tmp, "docs"))
                with open(os.path.join(tmp, "readme.txt"), "w", encoding="utf-8") as handle:
                    handle.write("hello")
                filled = self.mount(MillerColumnsView(tmp))
                badges = [
                    child.get_text()
                    for child in _walk(filled.columns[0])
                    if isinstance(child, Gtk.Label) and child.get_style_context().has_class("mc-column-count")
                ]
                self.assertEqual(badges, ["2 itens"])
        finally:
            reset()

    def test_refocus_keeps_up_down_in_the_clicked_column(self):
        """Tab return focuses the accent column, so Up/Down does not prune to the root."""
        with tempfile.TemporaryDirectory() as tmp:
            beta = os.path.join(tmp, "beta")
            child = os.path.join(beta, "child")
            os.makedirs(os.path.join(child, "leaf"))
            os.makedirs(os.path.join(child, "other"))
            os.makedirs(os.path.join(tmp, "alpha"))
            with open(os.path.join(tmp, "zed.txt"), "w", encoding="utf-8") as handle:
                handle.write("z")
            view = self.mount(MillerColumnsView(tmp))
            _click_row(view.columns[0], _row_named(view.columns[0], "beta"))
            view.flush_selection()
            _click_row(view.columns[1], _row_named(view.columns[1], "child"))
            view.flush_selection()
            _click_row(view.columns[2], _row_named(view.columns[2], "leaf"))
            view.flush_selection()
            self.assertEqual(view.focused_index, 3)
            self.assertEqual(len(view.columns), 4)
            view.focus_active_column()
            self.assertIs(_focused_column(view), view.columns[2])
            self.assertEqual(view.columns[2].get_selected().name, "leaf")
            _focused_column(view).handle_key(Gdk.KEY_Down)
            view.flush_selection()
            self.assertEqual(view.focused_index, 3)
            self.assertEqual(
                [column.directory_path for column in view.columns],
                [
                    os.path.abspath(tmp),
                    os.path.abspath(beta),
                    os.path.abspath(child),
                    os.path.abspath(os.path.join(child, "other")),
                ],
            )
            self.assertEqual(view.columns[0].get_selected().name, "beta")
            self.assertEqual(view.columns[2].get_selected().name, "other")

    def test_right_moves_keyboard_focus_into_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "child")
            os.makedirs(os.path.join(child, "alpha"))
            with open(os.path.join(child, "notes.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            view = self.mount(MillerColumnsView(tmp))
            view.columns[0].select_name("child")
            view.flush_selection()
            view.focus_active_column()
            focused = _focused_column(view)
            self.assertIs(focused, view.columns[0])
            focused.handle_key(Gdk.KEY_Right)
            self.assertEqual(view.focused_index, 1)
            self.assertIs(_focused_column(view), view.columns[1])
            entry = _focused_row_entry(view)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.name, "alpha")
            self.assertEqual(view.columns[1].get_selected().name, "alpha")
            _focused_column(view).handle_key(Gdk.KEY_Down)
            self.assertEqual(view.columns[1].get_selected().name, "notes.txt")
            self.assertEqual(view.columns[0].get_selected().name, "child")

    def test_left_moves_focus_back_to_parent_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "child")
            os.makedirs(os.path.join(child, "alpha"))
            with open(os.path.join(child, "notes.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            view = self.mount(MillerColumnsView(tmp))
            view.columns[0].select_name("child")
            view.flush_selection()
            view.focus_active_column()
            _focused_column(view).handle_key(Gdk.KEY_Right)
            self.assertIs(_focused_column(view), view.columns[1])
            _focused_column(view).handle_key(Gdk.KEY_Left)
            self.assertEqual(view.focused_index, 0)
            self.assertIs(_focused_column(view), view.columns[0])
            entry = _focused_row_entry(view)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.name, "child")

    def test_left_on_root_focuses_selected_row_in_new_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = os.path.join(tmp, "parent")
            child = os.path.join(parent, "child")
            os.makedirs(child)
            with open(os.path.join(child, "keep.txt"), "w", encoding="utf-8") as handle:
                handle.write("k")
            view = self.mount(MillerColumnsView(child))
            view.focus_active_column()
            _focused_column(view).handle_key(Gdk.KEY_Left)
            self.assertEqual(view.focused_index, 0)
            self.assertIs(_focused_column(view), view.columns[0])
            entry = _focused_row_entry(view)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.name, "child")
            self.assertEqual(view.columns[0].get_selected().name, "child")

    def test_right_on_empty_folder_keeps_focus(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "empty")
            os.makedirs(empty)
            calls = []
            view = self.mount(MillerColumnsView(tmp, on_active_path_changed=calls.append))
            view.columns[0].select_name("empty")
            view.flush_selection()
            view.focus_active_column()
            marked = len(calls)
            active = view.get_active_path()
            self.assertEqual(active, os.path.abspath(empty))
            _focused_column(view).handle_key(Gdk.KEY_Right)
            self.assertEqual(view.focused_index, 1)
            self.assertIs(_focused_column(view), view.columns[0])
            entry = _focused_row_entry(view)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.name, "empty")
            self.assertEqual(view.get_active_path(), active)
            self.assertEqual(len(calls), marked)
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(empty))

    def test_initial_focus_is_inside_root_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as handle:
                handle.write("a")
            view = self.mount(MillerColumnsView(tmp))
            self.assertIs(_focused_column(view), view.columns[0])
            self.assertIsNone(view.columns[0].get_selected())

    def test_refresh_keeps_focus_in_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as handle:
                handle.write("a")
            with open(os.path.join(tmp, ".secret"), "w", encoding="utf-8") as handle:
                handle.write("s")
            elsewhere = os.path.join(tmp, "elsewhere")
            os.makedirs(elsewhere)
            view = self.mount(MillerColumnsView(tmp))
            view.focus_active_column()
            _focused_column(view).handle_key(Gdk.KEY_h, Gdk.ModifierType.CONTROL_MASK)
            self.assertIsNotNone(_focused_column(view))
            self.assertIs(_focused_column(view), view.columns[0])
            view.set_root_path(elsewhere)
            self.assertIsNotNone(_focused_column(view))
            self.assertIs(_focused_column(view), view.columns[0])

    def test_rename_escape_returns_focus_to_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.create_folder("Docs")
            rename = None
            for row in column.listbox.get_children():
                rename = getattr(row, "rename_widget", None)
                if rename is not None:
                    break
            self.assertIsNotNone(rename)
            self.assertIs(self.window.get_focus(), rename)
            # Escape is handled by the entry, not the column's handle_key.
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.keyval = Gdk.KEY_Escape
            event.state = 0
            column._on_rename_key(rename, event)
            self.assertIs(_focused_column(view), column)
            entry = _focused_row_entry(view)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.name, "Docs")
            self.assertEqual(column.get_selected().name, "Docs")

    def test_create_folder_disambiguates_existing_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "New Folder"))
            view = self.mount(MillerColumnsView(tmp))
            created = view.columns[0].create_folder()
            self.assertEqual(os.path.basename(created), "New Folder 2")
            self.assertTrue(os.path.isdir(created))
            self.assertEqual(view.columns[0].get_selected().name, "New Folder 2")

    def test_preview_content_is_fixed_and_has_no_buttons(self):
        pane = PreviewPane()
        self.assertEqual(
            [child for child in _walk(pane) if isinstance(child, Gtk.Button)],
            [],
        )
        content = next(
            child for child in _walk(pane) if child.get_style_context().has_class("mc-preview-content")
        )
        width, _height = content.get_size_request()
        self.assertEqual(width, 360)
        self.assertEqual(content.get_halign(), Gtk.Align.START)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        meta = {
            "mime_description": "Texto",
            "size_human": "1 B",
            "ctime_human": "01/01/2020 00:00",
            "mtime_human": "",
            "atime_human": "02/01/2020 00:00",
            "permissions": "rw-r--r-- (644)",
            "path": "/tmp/a/very/long/path/note.txt",
        }
        pack_metadata_rows(box, meta)
        labels = _label_texts(box)
        self.assertIn("INFORMATION", labels)
        self.assertIn("Type", labels)
        self.assertIn("Created", labels)
        self.assertIn("Last access", labels)
        self.assertIn("Permissions", labels)
        self.assertIn("Path", labels)
        self.assertNotIn("Modified", labels)
        path_label = next(
            child
            for child in _walk(box)
            if isinstance(child, Gtk.Label) and child.get_text() == meta["path"]
        )
        self.assertEqual(path_label.get_ellipsize(), Pango.EllipsizeMode.MIDDLE)
        self.assertEqual(path_label.get_tooltip_text(), meta["path"])
        self.assertTrue(path_label.get_selectable())
        self.assertFalse(path_label.get_line_wrap())

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        pack_metadata_rows(preview_box, meta, fields=_PREVIEW_METADATA_FIELDS)
        preview_labels = _label_texts(preview_box)
        self.assertIn("Size", preview_labels)
        self.assertNotIn("Type", preview_labels)
        self.assertNotIn("Path", preview_labels)
        self.assertNotIn(meta["path"], preview_labels)

    def test_icon_tint_saturated_and_grey(self):
        red = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8, 8)
        red.fill(0xFF0000FF)
        tint = icon_tint(red)
        self.assertIsNotNone(tint)
        red_channel, green_channel, blue_channel = tint
        self.assertGreater(red_channel, 200)
        self.assertLess(green_channel, 30)
        self.assertLess(blue_channel, 30)

        grey = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8, 8)
        grey.fill(0x808080FF)
        self.assertIsNone(icon_tint(grey))

    def test_symbolic_icon_uses_accent_tile(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "spec.pdf")
            with open(pdf, "wb") as handle:
                handle.write(b"%PDF-1.4")
            pane = PreviewPane()
            pane.update_file(pdf)
            tiles = [child for child in _walk(pane) if child.get_style_context().has_class("mc-preview-tile")]
            self.assertTrue(tiles)
            self.assertFalse(tiles[0].get_style_context().has_class("mc-preview-tile-accent"))

            original = Gtk.IconInfo.is_symbolic
            Gtk.IconInfo.is_symbolic = lambda _info: True
            try:
                pane.update_file(pdf)
            finally:
                Gtk.IconInfo.is_symbolic = original
            tiles = [child for child in _walk(pane) if child.get_style_context().has_class("mc-preview-tile")]
            self.assertTrue(tiles)
            self.assertTrue(tiles[0].get_style_context().has_class("mc-preview-tile-accent"))

    def test_narrow_window_scrolls_to_the_end(self):
        self.window.set_default_size(500, 420)
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "child")
            nested = os.path.join(child, "nested")
            os.makedirs(nested)
            with open(os.path.join(nested, "note.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            view = MillerColumnsView(tmp)
            self.window.add(view)
            self.window.show_all()
            self.window.resize(500, 420)
            rect = Gdk.Rectangle()
            rect.x = 0
            rect.y = 0
            rect.width = 500
            rect.height = 420
            view.size_allocate(rect)
            pump()
            view.columns[0].select_name("child")
            view.flush_selection()
            view.columns[1].select_name("nested")
            view.flush_selection()
            view.columns[2].select_name("note.txt")
            view.flush_selection()
            pump()
            view.size_allocate(rect)
            pump()
            adjustment = view.scroller.get_hadjustment()
            upper = adjustment.get_upper()
            page = adjustment.get_page_size()
            self.assertGreater(upper, page)
            self.assertAlmostEqual(adjustment.get_value(), upper - page, places=3)
            self.assertGreaterEqual(len(view.columns), 3)
            self.assertTrue(_has_ancestor(view.preview_pane, view.columns_box))

    def test_current_column_marker_follows_selection_and_arrows(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "child")
            os.makedirs(os.path.join(child, "alpha"))
            with open(os.path.join(tmp, "note.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            view = self.mount(MillerColumnsView(tmp))
            self.assertEqual(_current_columns(view), [view.columns[0]])

            view.columns[0].select_name("child")
            self.assertEqual(_current_columns(view), [view.columns[0]])
            view.flush_selection()
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(_current_columns(view), [view.columns[0]])

            view.columns[0].handle_key(Gdk.KEY_Right)
            self.assertEqual(_current_columns(view), [view.columns[1]])

            view.columns[1].handle_key(Gdk.KEY_Left)
            self.assertEqual(_current_columns(view), [view.columns[0]])

            view.columns[0].select_name("note.txt")
            self.assertEqual(_current_columns(view), [view.columns[0]])
            view.flush_selection()
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(_current_columns(view), [view.columns[0]])

            parent = os.path.join(tmp, "parent")
            nested = os.path.join(parent, "nested")
            os.makedirs(nested)
            climbed = self.mount(MillerColumnsView(nested))
            climbed.columns[0].handle_key(Gdk.KEY_Left)
            self.assertEqual(climbed.focused_index, 0)
            self.assertEqual(_current_columns(climbed), [climbed.columns[0]])

            climbed.set_root_path(parent)
            self.assertEqual(_current_columns(climbed), [climbed.columns[climbed.focused_index]])
            self.assertEqual(len(_current_columns(climbed)), 1)

    def test_folder_click_keeps_selection_and_appends_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "inside.txt"), "w", encoding="utf-8") as handle:
                handle.write("inside")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            row = _row_named(column, "sub")
            _click_row(column, row)
            view.flush_selection()
            selected = column.get_selected()
            self.assertIsNotNone(selected)
            self.assertEqual(selected.name, "sub")
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1].directory_path, os.path.abspath(sub))
            self.assertEqual(_current_columns(view), [column])

    def test_file_click_policy_opens_once_or_not_at_all(self):
        opened = []
        original = column_module.open_file_default
        column_module.open_file_default = lambda path: opened.append(path)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("open-me")
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                row = _row_named(column, "note.txt")
                target = os.path.abspath(note)

                column.file_click_policy = lambda: "double"
                _click_row(column, row)
                self.assertEqual(opened, [])

                column.file_click_policy = None
                _click_row(column, row)
                self.assertEqual(opened, [])

                column.file_click_policy = lambda: "single"
                _click_row(column, row)
                self.assertEqual(opened, [target])
                column._on_row_activated(column.listbox, row)
                self.assertEqual(opened, [target])
        finally:
            column_module.open_file_default = original

    def test_ui_sources_do_not_launch_nemo_or_write_view_metadata(self):
        ui_dir = os.path.join(ROOT, "miller", "ui")
        for name in os.listdir(ui_dir):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(ui_dir, name), encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("subprocess", source)
            self.assertNotIn("os.system", source)
            self.assertNotIn("nemo-default-view", source)
            self.assertNotIn("org.nemo.preferences", source)
            self.assertNotIn("Adw", source)
            self.assertNotIn("add_css_class", source)
            self.assertNotIn("set_child", source)


    def test_shift_arrows_ctrl_a_and_bulk_edit(self):
        copied = []
        original_copy = column_module.copy_to_clipboard
        column_module.copy_to_clipboard = lambda paths, cut=False: copied.append((list(paths), cut))
        opened = []
        original_open = column_module.open_file_default
        column_module.open_file_default = lambda path: opened.append(path)
        try:
            home = os.path.expanduser("~")
            with tempfile.TemporaryDirectory(dir=home) as tmp:
                paths = []
                for name in ("a.txt", "b.txt", "c.txt"):
                    path = os.path.join(tmp, name)
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(name)
                    paths.append(os.path.abspath(path))
                os.makedirs(os.path.join(tmp, "sub"))
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                column.select_name("a.txt")
                column.handle_key(Gdk.KEY_Down, Gdk.ModifierType.SHIFT_MASK)
                self.assertEqual(
                    [entry.name for entry in column.get_selected_entries()],
                    ["a.txt", "b.txt"],
                )
                self.assertIsNone(column.get_selected())
                column.handle_key(Gdk.KEY_Down)
                self.assertEqual(len(column.get_selected_entries()), 1)

                column.handle_key(Gdk.KEY_a, Gdk.ModifierType.CONTROL_MASK)
                self.assertEqual(
                    [entry.name for entry in column.get_selected_entries()],
                    ["sub", "a.txt", "b.txt", "c.txt"],
                )
                column.copy_selected(True)
                self.assertEqual(copied[-1][1], True)
                self.assertEqual(
                    copied[-1][0],
                    [os.path.abspath(os.path.join(tmp, "sub"))] + paths,
                )

                menu = column._build_context_menu()
                sensitive = {item.get_label(): item.get_sensitive() for item in menu.get_children()}
                self.assertFalse(sensitive["Rename\u2026"])
                self.assertTrue(sensitive["Properties"])
                self.assertFalse(sensitive["Copy Path"])
                self.assertFalse(sensitive["Open in New Tab"])
                self.assertFalse(sensitive["Open With"])
                self.assertTrue(sensitive["Copy"])
                self.assertTrue(sensitive["Open"])
                self.assertTrue(sensitive["New Folder"])

                props = []
                original_props = column_module.show_item_properties
                column_module.show_item_properties = (
                    lambda paths: props.append(list(paths)) or True
                )
                try:
                    column._menu_properties()
                finally:
                    column_module.show_item_properties = original_props
                self.assertEqual(
                    props,
                    [[os.path.abspath(os.path.join(tmp, "sub"))] + paths],
                )

                column.handle_key(Gdk.KEY_Return)
                self.assertEqual(opened, paths)

                column._set_selection(
                    [_row_named(column, "a.txt"), _row_named(column, "b.txt")],
                    notify=False,
                )
                column.delete_selected()
                if os.path.exists(paths[0]) or os.path.exists(paths[1]):
                    self.skipTest("trash unavailable")
                self.assertTrue(os.path.exists(paths[2]))
                _delete_trashed(paths[0])
                _delete_trashed(paths[1])
        finally:
            column_module.copy_to_clipboard = original_copy
            column_module.open_file_default = original_open

    def test_right_click_keeps_or_replaces_selection(self):
        original_popup = Gtk.Menu.popup
        Gtk.Menu.popup = lambda *args, **kwargs: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for name in ("a.txt", "b.txt", "c.txt"):
                    with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                        handle.write(name)
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                column._set_selection(
                    [_row_named(column, "a.txt"), _row_named(column, "b.txt")],
                    notify=False,
                )
                event = _button_event(column, _row_named(column, "a.txt"), Gdk.EventType.BUTTON_PRESS)
                event.button = 3
                column.listbox.emit("button-press-event", event)
                self.assertEqual(
                    [entry.name for entry in column.get_selected_entries()],
                    ["a.txt", "b.txt"],
                )
                event = _button_event(column, _row_named(column, "c.txt"), Gdk.EventType.BUTTON_PRESS)
                event.button = 3
                column.listbox.emit("button-press-event", event)
                self.assertEqual(
                    [entry.name for entry in column.get_selected_entries()],
                    ["c.txt"],
                )
                blank = Gdk.EventButton.new(Gdk.EventType.BUTTON_PRESS)
                window = column.listbox.get_window()
                if window is not None:
                    blank.window = window
                blank.button = 3
                blank.x = 1
                blank.y = 4000
                blank.time = Gdk.CURRENT_TIME
                column.listbox.emit("button-press-event", blank)
                self.assertEqual(column.get_selected_entries(), [])
                labels = _menu_outline(column._context_menu)
                self.assertEqual(labels[0], "New Folder")
                self.assertEqual(labels[1], "Paste")
                self.assertEqual(labels[-1], "Properties")
        finally:
            Gtk.Menu.popup = original_popup

    def test_multi_selection_prunes_columns_and_summarizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "big.bin"), "wb") as handle:
                handle.write(b"x" * 1000)
            with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as handle:
                handle.write("hello")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.select_name("sub")
            view.flush_selection()
            self.assertEqual(len(view.columns), 2)
            column.handle_key(Gdk.KEY_Down, Gdk.ModifierType.SHIFT_MASK)
            view.flush_selection()
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(view.preview_pane.get_title(), "2 items selected")
            labels = _label_texts(view.preview_pane)
            self.assertIn("1 folder, 1 file", labels)
            self.assertIn("5 B", labels)
            self.assertNotIn("1000", " ".join(labels))

    def test_directory_monitor_keeps_selection_and_waits_for_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            keep = os.path.join(tmp, "keep.txt")
            with open(keep, "w", encoding="utf-8") as handle:
                handle.write("keep")
            calls = []
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.on_select = lambda *args: calls.append(args)
            column.select_name("keep.txt")
            calls.clear()
            new_path = os.path.join(tmp, "new.txt")
            with open(new_path, "w", encoding="utf-8") as handle:
                handle.write("new")
            self.assertTrue(pump_until(lambda: "new.txt" in _entry_names(column)), "create")
            self.assertEqual([entry.name for entry in column.get_selected_entries()], ["keep.txt"])
            self.assertEqual(calls, [])

            os.remove(keep)
            self.assertTrue(pump_until(lambda: "keep.txt" not in _entry_names(column)), "delete")
            self.assertEqual(column.get_selected_entries(), [])
            self.assertTrue(calls)

            with open(keep, "w", encoding="utf-8") as handle:
                handle.write("keep")
            self.assertTrue(pump_until(lambda: "keep.txt" in _entry_names(column)), "recreate")
            column.select_name("keep.txt")
            column.begin_rename()
            extra = os.path.join(tmp, "extra.txt")
            with open(extra, "w", encoding="utf-8") as handle:
                handle.write("extra")
            time.sleep(0.45)
            pump()
            self.assertNotIn("extra.txt", _entry_names(column))
            column._end_rename_widget()
            self.assertTrue(pump_until(lambda: "extra.txt" in _entry_names(column)), "after rename")
            self.assertEqual(column.get_selected().name, "keep.txt")

    def test_drag_source_payload_and_silent_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "folder"))
            with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as handle:
                handle.write("a")
            calls = []
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.select_name("a.txt")
            column.on_select = lambda *args: calls.append(args)
            column._press_row = _row_named(column, "folder")
            column._selection_from_user = True
            column._primary_presses = 1
            icons = {}
            original_name = Gtk.drag_set_icon_name
            Gtk.drag_set_icon_name = lambda context, name, hot_x, hot_y: icons.setdefault("name", name)
            try:
                dnd_module.on_drag_begin(column, object())
            finally:
                Gtk.drag_set_icon_name = original_name
            self.assertEqual(calls, [])
            self.assertEqual(column.get_selected().name, "folder")
            self.assertFalse(column._selection_from_user)
            self.assertEqual(column._primary_presses, 0)
            self.assertEqual(icons["name"], "folder-symbolic")

            column._set_selection(
                [_row_named(column, "folder"), _row_named(column, "a.txt")],
                notify=False,
            )
            column._press_row = _row_named(column, "folder")
            widgets = {}
            original_widget = Gtk.drag_set_icon_widget
            Gtk.drag_set_icon_widget = lambda context, widget, hot_x, hot_y: widgets.setdefault("widget", widget)
            try:
                dnd_module.on_drag_begin(column, object())
            finally:
                Gtk.drag_set_icon_widget = original_widget
            self.assertEqual(
                [entry.name for entry in column.get_selected_entries()],
                ["folder", "a.txt"],
            )
            self.assertIn("2", _label_texts(widgets["widget"]))

            class _Selection:
                def set_uris(self, uris):
                    self.uris = list(uris)

            selection = _Selection()
            dnd_module.on_drag_data_get(column, None, selection, 0, 0)
            self.assertEqual(selection.uris, [entry.uri for entry in column.get_selected_entries()])

    def test_directory_row_paints_colored_file_and_emblems(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = os.path.join(tmp, "folder-violet.png")
            with open(png, "wb") as handle:
                handle.write(_PNG_1X1)
            folder = FileEntry(
                name="Cursos",
                path=os.path.join(tmp, "Cursos"),
                uri="file:///tmp/Cursos",
                is_dir=True,
                size=0,
                mtime=0.0,
                atime=0.0,
                content_type="inode/directory",
                icon_name="folder-symbolic",
                permissions_str="rwxr-xr-x (755)",
                is_hidden=False,
                icon_file=png,
                folder_icon_names=(),
                emblems=("emblem-favorite", "no-such-miller-emblem", "emblem-new"),
            )
            row = ColumnListMixin._make_row(object(), folder)
            images = [widget for widget in _walk(row) if isinstance(widget, Gtk.Image)]
            colored = []
            for image in images:
                stored = image.get_gicon()
                icon = getattr(stored, "gicon", None)
                if icon is None and stored:
                    icon = stored[0]
                if isinstance(icon, Gio.FileIcon):
                    colored.append((image, icon))
            self.assertEqual(len(colored), 1)
            image, icon = colored[0]
            self.assertEqual(image.get_pixel_size(), 16)
            self.assertEqual(icon.get_file().get_path(), os.path.abspath(png))
            badges = [image for image in images if image.get_pixel_size() == 8]
            self.assertEqual(len(badges), 2)
            self.assertEqual(
                [(badge.get_halign(), badge.get_valign()) for badge in badges],
                [(Gtk.Align.END, Gtk.Align.END), (Gtk.Align.START, Gtk.Align.END)],
            )
            overlays = [widget for widget in _walk(row) if isinstance(widget, Gtk.Overlay)]
            self.assertEqual(len(overlays), 1)

            text = FileEntry(
                name="note.txt",
                path=os.path.join(tmp, "note.txt"),
                uri="file:///tmp/note.txt",
                is_dir=False,
                size=1,
                mtime=0.0,
                atime=0.0,
                content_type="text/plain",
                icon_name="text-x-generic-symbolic",
                permissions_str="rw-r--r-- (644)",
                is_hidden=False,
                icon_file=png,
                folder_icon_names=("folder-yellow",),
                emblems=("emblem-favorite",),
            )
            row = ColumnListMixin._make_row(object(), text)
            self.assertFalse(any(isinstance(widget, Gtk.Overlay) for widget in _walk(row)))
            images = [widget for widget in _walk(row) if isinstance(widget, Gtk.Image)]
            self.assertEqual(len(images), 1)
            stored = images[0].get_icon_name()
            icon_name = stored if isinstance(stored, str) else stored.icon_name
            self.assertEqual(icon_name, "text-x-generic-symbolic")
            self.assertEqual(images[0].get_pixel_size(), 16)

            plain = FileEntry(
                name="Plain",
                path=os.path.join(tmp, "Plain"),
                uri="file:///tmp/Plain",
                is_dir=True,
                size=0,
                mtime=0.0,
                atime=0.0,
                content_type="inode/directory",
                icon_name="folder-symbolic",
                permissions_str="rwxr-xr-x (755)",
                is_hidden=False,
                folder_icon_names=("inode-directory", "folder"),
            )
            row = ColumnListMixin._make_row(object(), plain)
            self.assertFalse(any(isinstance(widget, Gtk.Overlay) for widget in _walk(row)))
            images = [widget for widget in _walk(row) if isinstance(widget, Gtk.Image)]
            stored = images[0].get_gicon()
            icon = getattr(stored, "gicon", None) if stored else None
            self.assertIsInstance(icon, Gio.ThemedIcon)
            self.assertEqual(list(icon.get_names())[:2], ["inode-directory", "folder"])
            self.assertEqual(images[0].get_pixel_size(), 16)

    def test_drop_refuses_same_directory_move_and_ask_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as handle:
                handle.write("a")
            foreign_dir = os.path.join(tmp, "foreign")
            os.makedirs(foreign_dir)
            foreign = os.path.join(foreign_dir, "note.txt")
            with open(foreign, "w", encoding="utf-8") as handle:
                handle.write("note")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.select_name("a.txt")

            class _Context:
                def get_actions(self):
                    return (
                        Gdk.DragAction.COPY
                        | Gdk.DragAction.MOVE
                        | Gdk.DragAction.LINK
                        | Gdk.DragAction.ASK
                    )

                def get_suggested_action(self):
                    return Gdk.DragAction.MOVE

                def get_device(self):
                    return None

            statuses = []
            fetched = []
            transferred = []
            finished = []
            original_status = Gdk.drag_status
            original_same = dnd_module.same_filesystem
            original_modifiers = dnd_module.pointer_modifiers
            original_transfer = dnd_module.transfer_uris
            original_finish = Gtk.drag_finish
            original_popup = Gtk.Menu.popup
            original_get = column.listbox.drag_get_data
            Gdk.drag_status = lambda context, action, time_: statuses.append(int(action))
            dnd_module.same_filesystem = lambda source, dest: True
            dnd_module.transfer_uris = lambda uris, dest, action: transferred.append((list(uris), dest, action)) or True
            Gtk.drag_finish = lambda context, ok, delete, time_: finished.append((ok, delete, time_))
            Gtk.Menu.popup = lambda *args, **kwargs: None
            column.listbox.drag_get_data = lambda *args: fetched.append(args)
            try:
                context = _Context()
                dnd_module.pointer_modifiers = lambda widget, ctx: 0
                dnd_module.on_drag_motion(column, context, 0, 10000, 3)
                dnd_module.on_drag_motion(column, context, 0, 10000, 4)
                self.assertEqual(len(fetched), 1)
                self.assertEqual(statuses[-1], 0)

                column._drag_uris[id(context)] = [column.get_selected().uri]
                dnd_module.on_drag_motion(column, context, 0, 10000, 5)
                self.assertEqual(statuses[-1], 0)

                note_uri = __import__("gi").repository.Gio.File.new_for_path(foreign).get_uri()
                ask = _Context()
                column._drag_uris[id(ask)] = [note_uri]
                dnd_module.pointer_modifiers = lambda widget, ctx: Gdk.ModifierType.MOD1_MASK
                dnd_module.on_drag_drop(column, ask, 0, 10000, 9)
                labels = [item.get_label() for item in column._drop_menu.get_children()]
                self.assertEqual(labels, ["Move Here", "Copy Here", "Link Here", "Cancel"])
                for item in column._drop_menu.get_children():
                    if item.get_label() == "Copy Here":
                        item.emit("activate")
                self.assertEqual(transferred[-1][0], [note_uri])
                self.assertEqual(transferred[-1][1], column.directory_path)
                self.assertEqual(transferred[-1][2], Gdk.DragAction.COPY)
                self.assertEqual(finished[-1], (True, False, 9))

                dismissed = _Context()
                column._drag_uris[id(dismissed)] = [note_uri]
                transferred_before = len(transferred)
                dnd_module.on_drag_drop(column, dismissed, 0, 10000, 11)
                column._drop_menu.emit("deactivate")
                self.assertEqual(len(transferred), transferred_before)
                self.assertEqual(finished[-1], (False, False, 11))
            finally:
                Gdk.drag_status = original_status
                dnd_module.same_filesystem = original_same
                dnd_module.pointer_modifiers = original_modifiers
                dnd_module.transfer_uris = original_transfer
                Gtk.drag_finish = original_finish
                Gtk.Menu.popup = original_popup
                column.listbox.drag_get_data = original_get

    def test_open_with_submenu_chooser_and_folder_guard(self):
        class _App:
            def __init__(self, app_id, name):
                self._id = app_id
                self._name = name

            def get_id(self):
                return self._id

            def get_display_name(self):
                return self._name

            def get_name(self):
                return self._name

            def get_icon(self):
                return None

        original_apps = open_with_module.recommended_apps
        open_with_module.recommended_apps = lambda content_types: [_App("gedit.desktop", "Editor")]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                other = os.path.join(tmp, "other.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("note")
                with open(other, "w", encoding="utf-8") as handle:
                    handle.write("other")
                os.makedirs(os.path.join(tmp, "sub"))
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                column.select_name("note.txt")
                menu = column._build_context_menu()
                item = next(child for child in menu.get_children() if child.get_label() == "Open With")
                self.assertTrue(item.get_sensitive())
                labels = []
                for child in item.get_submenu().get_children():
                    if isinstance(child, Gtk.SeparatorMenuItem):
                        labels.append("---")
                    else:
                        texts = _label_texts(child)
                        labels.append(texts[0] if texts else child.get_label())
                self.assertEqual(labels, ["Editor", "---", "Other Application\u2026"])
                icons = [
                    child
                    for child in item.get_submenu().get_children()
                    if not isinstance(child, Gtk.SeparatorMenuItem)
                    for child in _walk(child)
                    if isinstance(child, Gtk.Image)
                ]
                self.assertTrue(icons)
                self.assertTrue(all(icon.get_pixel_size() == open_with_module._MENU_ICON_SIZE for icon in icons))

                column.select_name("sub")
                folder_menu = column._build_context_menu()
                folder_item = next(child for child in folder_menu.get_children() if child.get_label() == "Open With")
                self.assertFalse(folder_item.get_sensitive())
                self.assertIsNone(folder_item.get_submenu())

                one = build_app_chooser(self.window, [note])
                self.assertIsInstance(one, Gtk.AppChooserDialog)
                one.destroy()
                several = build_app_chooser(self.window, [note, other])
                self.assertIsInstance(several, Gtk.AppChooserDialog)
                several.destroy()
        finally:
            open_with_module.recommended_apps = original_apps


    def test_selection_menu_inserts_nemo_sections_and_activates(self):
        from miller.nemo_actions import MenuTarget, NemoAction, ScriptNode

        action = NemoAction(
            path="/tmp/a.nemo_action",
            file_name="a.nemo_action",
            parent_dir="/tmp",
            label="Zip %f",
            tooltip="tip %f",
            icon_name="folder",
            exec_line="true %F",
            use_parent_dir=False,
            selection="any",
            extensions=["any"],
            mimetypes=[],
            conditions=[],
            separator=None,
            quote="none",
            terminal=False,
            uri_scheme=None,
        )
        script = ScriptNode(label="echo.sh", path="/tmp/echo.sh", children=[])
        runs = []
        scripts = []
        ext_calls = []

        class _Ext:
            def __init__(self, label, sensitive=True, separator=False, menu=None, widget=None):
                self._label = label
                self._sensitive = sensitive
                self._separator = separator
                self._menu = menu
                self._widget = widget
                self.activated = False

            def get_property(self, name):
                return {
                    "separator": self._separator,
                    "label": self._label,
                    "tip": "",
                    "sensitive": self._sensitive,
                    "icon": "",
                    "menu": self._menu,
                    "widget-a": self._widget,
                    "widget-b": None,
                }[name]

            def activate(self):
                self.activated = True
                ext_calls.append(self._label)

        class _Sub:
            def get_items(self):
                return [_Ext("Nested")]

        ext_main = _Ext("Compactar")
        ext_sep = _Ext("", separator=True)
        ext_off = _Ext("Quiet", sensitive=False)
        ext_sub = _Ext("More", menu=_Sub())
        ext_widget = _Ext("", widget=object())
        nemo_items.load_actions = lambda *a, **k: [action]
        nemo_items.load_scripts = lambda *a, **k: [script]
        nemo_items.extension_items = lambda *a, **k: [ext_main, ext_sep, ext_off, ext_sub, ext_widget]
        nemo_items.run_action = lambda chosen, target, xid: runs.append((chosen, target, xid)) or True
        nemo_items.run_script = lambda path, target, geometry="": scripts.append((path, target, geometry)) or True
        original_popup = Gtk.Menu.popup
        Gtk.Menu.popup = lambda *args, **kwargs: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("x")
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                column.select_name("note.txt")
                menu = column._build_context_menu()
                self.assertEqual(
                    _menu_outline(menu),
                    [
                        "Open",
                        "Open With",
                        "Open in New Tab",
                        "Open in New Window",
                        "---",
                        "Zip note.txt",
                        "Scripts",
                        "New Folder",
                        "Cut",
                        "Copy",
                        "Paste",
                        "Rename\u2026",
                        "Move to Trash",
                        "Copy Path",
                        "---",
                        "Compactar",
                        "---",
                        "Quiet",
                        "More",
                        "Properties",
                    ],
                )
                items = dict(zip(_menu_outline(menu), menu.get_children()))
                self.assertFalse(items["Quiet"].get_sensitive())
                self.assertIsNotNone(items["More"].get_submenu())
                self.assertEqual(
                    [child.get_label() for child in items["More"].get_submenu().get_children()],
                    ["Nested"],
                )
                items["Zip note.txt"].activate()
                self.assertEqual(runs[0][0], action)
                self.assertEqual(runs[0][1].paths, [os.path.abspath(note)])
                self.assertIsInstance(runs[0][1], MenuTarget)
                script_item = items["Scripts"].get_submenu().get_children()[0]
                script_item.activate()
                self.assertEqual(scripts[0][0], "/tmp/echo.sh")
                items["Compactar"].activate()
                self.assertEqual(ext_calls, ["Compactar"])
                self.assertTrue(ext_main.activated)
        finally:
            Gtk.Menu.popup = original_popup

    def test_background_menu_layout_and_properties(self):
        from miller.nemo_actions import NemoAction

        action = NemoAction(
            path="/tmp/none.nemo_action",
            file_name="none.nemo_action",
            parent_dir="/tmp",
            label="Empty action",
            tooltip="",
            icon_name="",
            exec_line="true",
            use_parent_dir=False,
            selection="none",
            extensions=["any"],
            mimetypes=[],
            conditions=[],
            separator=None,
            quote="none",
            terminal=False,
            uri_scheme=None,
        )
        nemo_items.load_actions = lambda *a, **k: [action]
        nemo_items.load_scripts = lambda *a, **k: []
        nemo_items.extension_items = lambda *a, **k: []
        calls = []
        original = column_module.show_item_properties
        column_module.show_item_properties = lambda paths: calls.append(list(paths)) or True
        try:
            with tempfile.TemporaryDirectory() as tmp:
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                menu = column._build_context_menu(background=True)
                self.assertEqual(_menu_outline(menu), ["New Folder", "Paste", "---", "Empty action", "Properties"])
                self.assertEqual(BACKGROUND_MENU_LABELS, ("New Folder", "Paste", "Properties"))
                column._on_menu_activate(None, "Properties", True)
                self.assertEqual(calls, [[column.directory_path]])
        finally:
            column_module.show_item_properties = original

    def test_nemo_menu_item_conversion(self):
        class _Ext:
            def __init__(self, label="", sensitive=True, separator=False, menu=None, widget=None):
                self._label = label
                self._sensitive = sensitive
                self._separator = separator
                self._menu = menu
                self._widget = widget
                self.activated = False

            def get_property(self, name):
                return {
                    "separator": self._separator,
                    "label": self._label,
                    "tip": "hint" if self._label else "",
                    "sensitive": self._sensitive,
                    "icon": "",
                    "menu": self._menu,
                    "widget-a": self._widget,
                    "widget-b": None,
                }[name]

            def activate(self):
                self.activated = True

        class _Sub:
            def get_items(self):
                return [_Ext("Child")]

        converted = []
        for raw in (
            _Ext(separator=True),
            _Ext("Off", sensitive=False),
            _Ext("Parent", menu=_Sub()),
            _Ext(widget=object()),
        ):
            item = nemo_items._nemo_item_to_gtk(raw)
            converted.append(item)
        self.assertIsInstance(converted[0], Gtk.SeparatorMenuItem)
        self.assertFalse(converted[1].get_sensitive())
        self.assertEqual(converted[1].get_tooltip_text(), "hint")
        self.assertIsNotNone(converted[2].get_submenu())
        self.assertEqual(
            [child.get_label() for child in converted[2].get_submenu().get_children()],
            ["Child"],
        )
        self.assertIsNone(converted[3])

    def test_prefix_search_filters_files_and_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Projects"))
            os.makedirs(os.path.join(tmp, "photos"))
            with open(os.path.join(tmp, "Readme.txt"), "w", encoding="utf-8") as handle:
                handle.write("r")
            with open(os.path.join(tmp, "my-note.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            with open(os.path.join(tmp, "note.txt"), "w", encoding="utf-8") as handle:
                handle.write("n")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            view.focus_active_column()
            self.assertFalse(column.search_label.get_visible())
            self.assertFalse(column.search_label.get_can_focus())

            self.assertTrue(column.handle_key(Gdk.KEY_r))
            self.assertEqual(column._search_query, "r")
            self.assertEqual(column.search_label.get_text(), "r")
            self.assertTrue(column.search_label.get_visible())
            self.assertEqual(column.listbox.get_margin_top(), 0)
            self.assertGreater(
                column.listbox.get_style_context().get_padding(Gtk.StateFlags.NORMAL).top,
                2,
            )
            self.assertEqual(_visible_names(column), ["Readme.txt"])
            self.assertIn("my-note.txt", _entry_names(column))
            focused = self.window.get_focus()
            self.assertIsInstance(focused, Gtk.ListBoxRow)
            self.assertIsNot(focused, column.search_label)
            count = next(
                child.get_text()
                for child in _walk(column)
                if isinstance(child, Gtk.Label)
                and child.get_style_context().has_class("mc-column-count")
            )
            self.assertEqual(count, "1 item")

            column.handle_key(Gdk.KEY_BackSpace)
            self.assertEqual(column._search_query, "")
            self.assertFalse(column.search_label.get_visible())
            self.assertEqual(column.listbox.get_margin_top(), 0)
            self.assertLessEqual(
                column.listbox.get_style_context().get_padding(Gtk.StateFlags.NORMAL).top,
                4,
            )
            self.assertEqual(_visible_names(column), _entry_names(column))

            column.handle_key(Gdk.KEY_n)
            self.assertEqual(_visible_names(column), ["note.txt"])
            column.handle_key(Gdk.KEY_Escape)
            self.assertEqual(column._search_query, "")
            self.assertEqual(sorted(_visible_names(column)), sorted(_entry_names(column)))

            column.handle_key(Gdk.KEY_p)
            self.assertEqual(
                sorted(name.casefold() for name in _visible_names(column)),
                ["photos", "projects"],
            )
            column._on_search_commit(None, "ã")
            self.assertEqual(column._search_query, "pã")
            self.assertEqual(_visible_names(column), [])
            self.assertTrue(column._owns_focus())
            self.assertFalse(column.search_label.get_can_focus())

    def test_leading_space_does_not_start_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a b.txt", "ab.txt", "other.txt"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                    handle.write("x")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            view.focus_active_column()

            self.assertFalse(column.handle_key(Gdk.KEY_space))
            self.assertEqual(column._search_query, "")
            self.assertFalse(column.search_label.get_visible())
            self.assertEqual(sorted(_visible_names(column)), sorted(_entry_names(column)))

            for keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space):
                event = SimpleNamespace(keyval=keyval, state=0)
                self.assertFalse(column._on_key_press(column, event))
                self.assertEqual(column._search_query, "")
                self.assertFalse(column.search_label.get_visible())

            self.assertTrue(column.handle_key(Gdk.KEY_a))
            self.assertTrue(column.handle_key(Gdk.KEY_space))
            self.assertEqual(column._search_query, "a ")
            self.assertEqual(column.search_label.get_text(), "a ")
            self.assertEqual(_visible_names(column), ["a b.txt"])

            column.handle_key(Gdk.KEY_BackSpace)
            column.handle_key(Gdk.KEY_BackSpace)
            self.assertEqual(column._search_query, "")
            self.assertFalse(column.handle_key(Gdk.KEY_space))
            self.assertEqual(column._search_query, "")


    def test_leading_space_does_not_open_and_still_bubbles(self):
        opened = []
        original = column_module.open_file_default
        column_module.open_file_default = lambda path: opened.append(path)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("open-me")
                with open(os.path.join(tmp, "a b.txt"), "w", encoding="utf-8") as handle:
                    handle.write("x")
                view = self.mount(MillerColumnsView(tmp))
                column = view.columns[0]
                self.window.present()
                view.focus_active_column()
                column.select_name("note.txt")
                column.focus_row()
                pump()

                bubbled = []
                view.connect(
                    "key-press-event",
                    lambda _widget, event: bubbled.append(event.keyval) or False,
                )

                _dispatch_key(self.window, Gdk.KEY_space)
                self.assertEqual(opened, [])
                self.assertEqual(column._search_query, "")
                self.assertIn(Gdk.KEY_space, bubbled)

                opened.clear()
                _dispatch_key(self.window, Gdk.KEY_Return)
                self.assertEqual(opened, [os.path.abspath(note)])

                opened.clear()
                column.handle_key(Gdk.KEY_a)
                bubbled.clear()
                _dispatch_key(self.window, Gdk.KEY_space)
                self.assertEqual(column._search_query, "a ")
                self.assertEqual(opened, [])
                self.assertNotIn(Gdk.KEY_space, bubbled)

                column.handle_key(Gdk.KEY_Escape)
                column.select_name("note.txt")
                column.file_click_policy = None
                _click_row(column, _row_named(column, "note.txt"))
                self.assertEqual(opened, [])
                column._on_row_activated(column.listbox, _row_named(column, "note.txt"))
                self.assertEqual(opened, [os.path.abspath(note)])
        finally:
            column_module.open_file_default = original

    def test_search_ignores_shortcuts_rename_and_keeps_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "visible.txt"), "w", encoding="utf-8") as handle:
                handle.write("v")
            with open(os.path.join(tmp, ".secret"), "w", encoding="utf-8") as handle:
                handle.write("s")
            with open(os.path.join(tmp, "other.txt"), "w", encoding="utf-8") as handle:
                handle.write("o")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            self.assertTrue(column.handle_key(Gdk.KEY_h, Gdk.ModifierType.CONTROL_MASK))
            self.assertEqual(column._search_query, "")
            self.assertIn(".secret", _visible_names(column))

            column.handle_key(Gdk.KEY_v)
            self.assertEqual(_visible_names(column), ["visible.txt"])
            column.refresh()
            self.assertEqual(column._search_query, "v")
            self.assertEqual(_visible_names(column), ["visible.txt"])
            self.assertEqual(column.search_label.get_text(), "v")

            column.select_name("visible.txt")
            column.begin_rename()
            self.assertEqual(column._search_query, "")
            self.assertFalse(column.handle_key(Gdk.KEY_a))
            self.assertEqual(column._search_query, "")
            self.assertIn("other.txt", _visible_names(column))

    def test_search_prunes_hidden_folder_and_leaves_the_child_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            beta = os.path.join(tmp, "beta")
            os.makedirs(os.path.join(beta, "child"))
            with open(os.path.join(tmp, "alpha.txt"), "w", encoding="utf-8") as handle:
                handle.write("a")
            view = self.mount(MillerColumnsView(tmp))
            _click_row(view.columns[0], _row_named(view.columns[0], "beta"))
            view.flush_selection()
            self.assertEqual(len(view.columns), 2)

            parent = view.columns[0]
            parent.handle_key(Gdk.KEY_b)
            view.flush_selection()
            self.assertEqual(_visible_names(parent), ["beta"])
            self.assertEqual(len(view.columns), 2)
            self.assertEqual(view.columns[1]._search_query, "")
            self.assertIn("child", _visible_names(view.columns[1]))

            child_names = list(_visible_names(view.columns[1]))
            parent.handle_key(Gdk.KEY_Right)
            self.assertEqual(parent._search_query, "b")
            self.assertEqual(view.columns[1]._search_query, "")
            self.assertEqual(_visible_names(view.columns[1]), child_names)

            parent.handle_key(Gdk.KEY_z)
            view.flush_selection()
            self.assertEqual(len(view.columns), 1)
            self.assertEqual(_visible_names(parent), [])

            parent.handle_key(Gdk.KEY_Escape)
            self.assertIn("alpha.txt", _visible_names(parent))
            self.assertIn("beta", _visible_names(parent))

    def test_search_arrows_and_select_all_skip_hidden_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("aa.txt", "ab.txt", "zz.txt"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                    handle.write("x")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            column.handle_key(Gdk.KEY_a)
            column.handle_key(Gdk.KEY_Down)
            self.assertEqual(column.get_selected().name, "aa.txt")
            column.handle_key(Gdk.KEY_Down)
            self.assertEqual(column.get_selected().name, "ab.txt")
            column.handle_key(Gdk.KEY_Down)
            self.assertEqual(column.get_selected().name, "ab.txt")
            column.handle_key(Gdk.KEY_a, Gdk.ModifierType.CONTROL_MASK)
            self.assertEqual(
                [entry.name for entry in column.get_selected_entries()],
                ["aa.txt", "ab.txt"],
            )
            self.assertNotIn("zz.txt", [entry.name for entry in column.get_selected_entries()])



    def test_loading_row_is_present_until_the_listing_returns(self):
        import miller.ui.column_list as column_list

        with tempfile.TemporaryDirectory() as tmp:
            held = {}
            real = column_list.list_directory_async

            def fake(path, show_hidden, cancellable, callback):
                held["callback"] = callback
                held["cancellable"] = cancellable

            column_list.list_directory_async = fake
            try:
                column = MillerColumn(
                    tmp,
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda *_args: None,
                )
                self.window.add(column)
                self.window.show_all()
                pump()
                self.assertTrue(column._loading)
                self.assertIn("Loading…", _label_texts(column))
                self.assertEqual(_entry_names(column), [])
                self.assertFalse(column.listbox.get_children()[0].get_selectable())
                with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as handle:
                    handle.write("a")
                from miller.data import list_directory
                held["callback"](list_directory(tmp))
                wait_loaded(column)
                self.assertEqual(_entry_names(column), ["a.txt"])
                self.assertNotIn("Loading…", _label_texts(column))

                from miller.data import FileEntry
                many = [
                    FileEntry(
                        f"f{index:03d}.txt",
                        os.path.join(tmp, f"f{index:03d}.txt"),
                        "file:///x",
                        False,
                        1,
                        0,
                        0,
                        "text/plain",
                        "text-x-generic-symbolic",
                        "rw-r--r-- (644)",
                        False,
                    )
                    for index in range(80)
                ]
                wide = MillerColumn(
                    tmp,
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda *_args: None,
                )
                held["callback"](many)
                # A large result must not replace the loading row in the
                # enumeration callback. The rows arrive on a later timeout.
                self.assertTrue(wide._loading)
                self.assertIn("Loading…", _label_texts(wide))
                self.assertEqual(_entry_names(wide), [])
                wait_loaded(wide)
                self.assertEqual(len(_entry_names(wide)), 80)
                self.assertNotIn("Loading…", _label_texts(wide))
            finally:
                column_list.list_directory_async = real

    def test_destroy_during_load_cancels_and_skips_the_callback(self):
        import miller.ui.column_list as column_list

        with tempfile.TemporaryDirectory() as tmp:
            held = {}
            selected = []
            real = column_list.list_directory_async

            def fake(path, show_hidden, cancellable, callback):
                held["callback"] = callback
                held["cancellable"] = cancellable

            column_list.list_directory_async = fake
            try:
                column = MillerColumn(
                    tmp,
                    lambda _column, entry: selected.append(entry),
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda *_args: None,
                )
                self.window.add(column)
                self.window.show_all()
                pump()
                cancellable = held["cancellable"]
                callback = held["callback"]
                column.destroy()
                pump()
                self.assertTrue(cancellable.is_cancelled())
                from miller.data import FileEntry
                callback([
                    FileEntry(
                        "late.txt",
                        os.path.join(tmp, "late.txt"),
                        "file:///late.txt",
                        False,
                        1,
                        0,
                        0,
                        "text/plain",
                        "text-x-generic-symbolic",
                        "rw-r--r-- (644)",
                        False,
                    )
                ])
                pump()
                self.assertEqual(selected, [])
            finally:
                column_list.list_directory_async = real

    def test_down_click_and_left_discard_a_loading_column(self):
        import miller.ui.column_list as column_list

        real = column_list.list_directory_async
        held = []
        hold_names = {"alpha"}

        def fake(path, show_hidden, cancellable, callback):
            if os.path.basename(path) in hold_names:
                held.append((path, cancellable, callback))
                return
            real(path, show_hidden, cancellable, callback)

        column_list.list_directory_async = fake
        previous = MillerColumnsView._wait_for_column_load
        MillerColumnsView._wait_for_column_load = False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                alpha = os.path.join(tmp, "alpha")
                beta = os.path.join(tmp, "beta")
                os.makedirs(alpha)
                os.makedirs(beta)
                view = self.mount(MillerColumnsView(tmp), wait=False)
                wait_loaded(view.columns[0])
                column = view.columns[0]
                column.handle_key(Gdk.KEY_Down)
                view.flush_selection()
                self.assertEqual(len(view.columns), 2)
                loading = view.columns[1]
                self.assertTrue(loading._loading)
                self.assertIn("Loading…", _label_texts(loading))
                alpha_cancel = held[-1][1]
                column.handle_key(Gdk.KEY_Down)
                view.flush_selection()
                self.assertTrue(alpha_cancel.is_cancelled())
                self.assertNotIn(loading, view.columns)
                self.assertEqual(view.columns[1].directory_path, os.path.abspath(beta))
                wait_loaded(view.columns[1])

            held.clear()
            hold_names.clear()
            hold_names.add("slow")
            with tempfile.TemporaryDirectory() as tmp:
                alpha = os.path.join(tmp, "alpha")
                slow = os.path.join(tmp, "slow")
                os.makedirs(alpha)
                os.makedirs(slow)
                view = self.mount(MillerColumnsView(tmp), wait=False)
                wait_loaded(view.columns[0])
                _click_row(view.columns[0], _row_named(view.columns[0], "slow"))
                loading = view.columns[1]
                self.assertTrue(loading._loading)
                slow_cancel = held[-1][1]
                _click_row(view.columns[0], _row_named(view.columns[0], "alpha"))
                self.assertTrue(slow_cancel.is_cancelled())
                self.assertNotIn(loading, view.columns)
                self.assertEqual(view.columns[1].directory_path, os.path.abspath(alpha))
                wait_loaded(view.columns[1])

            held.clear()
            hold_names.clear()
            hold_names.add("leaf")
            with tempfile.TemporaryDirectory() as tmp:
                mid = os.path.join(tmp, "mid")
                leaf = os.path.join(mid, "leaf")
                os.makedirs(leaf)
                view = self.mount(MillerColumnsView(tmp), wait=False)
                wait_loaded(view.columns[0])
                view.columns[0].select_name("mid")
                wait_loaded(view.columns[1])
                view.columns[1].select_name("leaf")
                loading = view.columns[2]
                self.assertTrue(loading._loading)
                leaf_cancel = held[-1][1]
                view.columns[1].handle_key(Gdk.KEY_Left)
                self.assertTrue(leaf_cancel.is_cancelled())
                self.assertNotIn(loading, view.columns)
                self.assertEqual(len(view.columns), 2)
                self.assertEqual(view.focused_index, 0)
        finally:
            column_list.list_directory_async = real
            MillerColumnsView._wait_for_column_load = previous

    def test_refresh_stays_synchronous_after_the_initial_load(self):
        import miller.ui.column_list as column_list

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as handle:
                handle.write("a")
            view = self.mount(MillerColumnsView(tmp))
            column = view.columns[0]
            self.assertFalse(column._loading)
            self.assertEqual(_entry_names(column), ["a.txt"])
            with open(os.path.join(tmp, "b.txt"), "w", encoding="utf-8") as handle:
                handle.write("b")
            column.refresh()
            self.assertFalse(column._loading)
            self.assertEqual(_entry_names(column), ["a.txt", "b.txt"])
            column.select_name("b.txt")
            self.assertEqual(column.get_selected().name, "b.txt")

            held = {}
            real = column_list.list_directory_async

            def fake(path, show_hidden, cancellable, callback):
                held["callback"] = callback

            column_list.list_directory_async = fake
            previous = MillerColumnsView._wait_for_column_load
            MillerColumnsView._wait_for_column_load = False
            try:
                fresh = MillerColumn(
                    tmp,
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda *_args: None,
                )
                self.assertTrue(fresh._loading)
                fresh.refresh()
                self.assertFalse(fresh._loading)
                self.assertEqual(_entry_names(fresh), ["a.txt", "b.txt"])
                held["callback"]([])
                pump()
                self.assertEqual(_entry_names(fresh), ["a.txt", "b.txt"])
            finally:
                column_list.list_directory_async = real
                MillerColumnsView._wait_for_column_load = previous


if __name__ == "__main__":
    unittest.main()
