"""Contract tests for miller.data and miller.theme."""

import datetime
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, GObject, Gio, Gtk

import miller
from miller.data import (
    DASHBOARD_PATH,
    DEFAULT_STORE_PATH,
    METADATA_KEY,
    choose_drop_action,
    create_subfolder,
    filter_drop_uris,
    format_gnome_copied_files,
    format_uri_list,
    get_file_metadata,
    is_dashboard_uri,
    launch_with_app,
    list_directory,
    open_in_same_window,
    parse_gnome_copied_files,
    recommended_apps,
    rename_entry,
    resolve_miller_view_preferred,
    same_filesystem,
    set_miller_view_preferred,
    show_file_preview,
    show_item_properties,
    transfer_uris,
    trash_entry,
)
import miller.data as data_module
from miller.listing import _entry_from_info
from miller.theme import (
    ACCENT_INK_CLASS,
    _apply_accent_ink,
    accent_ink_for_context,
    bind_accent_ink,
    build_css,
    selected_row_uses_accent_ink,
)

_GTK_READY = None
_LOCATION_BAR_CLASS = None

_METADATA_KEYS = {
    "name",
    "path",
    "uri",
    "is_dir",
    "size_human",
    "mtime_human",
    "atime_human",
    "ctime_human",
    "content_type",
    "mime_description",
    "icon_name",
    "permissions",
    "preview_kind",
    "preview_text",
}


def ensure_gtk():
    """Initialize GTK once. Returns false when there is no display.

    ``Gtk.init`` aborts if the display is missing, so ``init_check`` decides.
    """
    global _GTK_READY
    if _GTK_READY is not None:
        return _GTK_READY
    checked = Gtk.init_check([])
    ok = checked[0] if isinstance(checked, tuple) else bool(checked)
    if ok:
        Gtk.init([])
    _GTK_READY = bool(ok)
    return _GTK_READY


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
        if os.path.isfile(trashed_file):
            os.remove(trashed_file)
        try:
            os.remove(info_path)
        except OSError:
            pass


def location_bar_class():
    """Register the NemoLocationBar test double a single time."""
    global _LOCATION_BAR_CLASS
    if _LOCATION_BAR_CLASS is None:

        class NemoLocationBar(Gtk.Box):
            __gtype_name__ = "NemoLocationBar"
            __gsignals__ = {
                "location-changed": (GObject.SignalFlags.RUN_FIRST, None, (Gio.File,)),
            }

        _LOCATION_BAR_CLASS = NemoLocationBar
    return _LOCATION_BAR_CLASS


def _rgba(red, green, blue, alpha=1.0):
    """Build a Gdk.RGBA without parsing a hex string."""
    color = Gdk.RGBA()
    color.red = red
    color.green = green
    color.blue = blue
    color.alpha = alpha
    return color


class _ThemeColors:
    """Style context stand-in. ``lookup_color`` matches the GTK return pair."""

    def __init__(self, colors):
        self.colors = colors
        self.classes = set()

    def lookup_color(self, name):
        color = self.colors.get(name)
        if color is None:
            return False, Gdk.RGBA()
        return True, color

    def has_class(self, name):
        return name in self.classes

    def add_class(self, name):
        self.classes.add(name)

    def remove_class(self, name):
        self.classes.discard(name)


class _InkWidget:
    """Widget whose class change re-enters the accent-ink update."""

    def __init__(self, context):
        self._context = context
        self.adds = 0

    def get_style_context(self):
        return self

    def lookup_color(self, name):
        return self._context.lookup_color(name)

    def has_class(self, name):
        return self._context.has_class(name)

    def add_class(self, name):
        self.adds += 1
        self._context.add_class(name)
        _apply_accent_ink(self)

    def remove_class(self, name):
        self._context.remove_class(name)
        _apply_accent_ink(self)


def _file_info(name: str, file_type: Gio.FileType) -> Gio.FileInfo:
    info = Gio.FileInfo()
    info.set_name(name)
    info.set_file_type(file_type)
    return info


class DataAndThemeTests(unittest.TestCase):
    def test_package_exports(self):
        self.assertEqual(miller.__version__, "0.1.0")
        self.assertEqual(miller.METADATA_KEY, METADATA_KEY)
        self.assertEqual(miller.METADATA_KEY, "metadata::nemo-miller-columns-view")
        self.assertEqual(miller.DASHBOARD_PATH, DASHBOARD_PATH)
        self.assertEqual(
            DEFAULT_STORE_PATH,
            os.path.abspath(os.path.expanduser("~/.config/nemo-miller-columns/folder_views.json")),
        )

    def test_list_directory_natural_order_and_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("zebra", "apple", "banana"):
                os.makedirs(os.path.join(tmp, name))
            with open(os.path.join(tmp, "apple", "inside.txt"), "w", encoding="utf-8") as handle:
                handle.write("nested")
            for name in ("file10.txt", "file2.txt", "file1.txt", ".secret"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                    handle.write(name)

            visible = list_directory(tmp, show_hidden=False)
            self.assertEqual(
                [entry.name for entry in visible],
                ["apple", "banana", "zebra", "file1.txt", "file2.txt", "file10.txt"],
            )
            self.assertTrue(all(entry.is_dir for entry in visible[:3]))
            self.assertTrue(all(not entry.is_dir for entry in visible[3:]))
            self.assertTrue(all(not entry.is_hidden for entry in visible))
            self.assertNotIn("inside.txt", [entry.name for entry in visible])
            apple = visible[0]
            self.assertEqual(apple.icon_name, "folder-symbolic")
            self.assertEqual(apple.icon_file, "")
            self.assertEqual(apple.emblems, ())
            self.assertIsInstance(apple.folder_icon_names, tuple)
            self.assertTrue(apple.uri.startswith("file://"))
            self.assertTrue(apple.path.startswith(os.path.abspath(tmp)))

            shown = list_directory(tmp, show_hidden=True)
            self.assertEqual(
                [entry.name for entry in shown],
                ["apple", "banana", "zebra", ".secret", "file1.txt", "file2.txt", "file10.txt"],
            )
            secret = next(entry for entry in shown if entry.name == ".secret")
            self.assertTrue(secret.is_hidden)
            self.assertFalse(secret.is_dir)
            self.assertEqual(secret.icon_file, "")
            self.assertEqual(secret.folder_icon_names, ())
            self.assertEqual(secret.emblems, ())
            dirs = [entry.name for entry in shown if entry.is_dir]
            self.assertEqual(dirs, ["apple", "banana", "zebra"])

            self.assertEqual(list_directory(os.path.join(tmp, "missing-dir")), [])
            self.assertEqual(list_directory(os.path.join(tmp, "file1.txt")), [])

    def test_directory_face_reads_file_info(self):
        directory = Gio.File.new_for_path("/tmp")
        plain_icon = Gio.ThemedIcon.new_from_names(["inode-directory", "folder-symbolic"])
        with tempfile.TemporaryDirectory() as tmp:
            png = os.path.join(tmp, "folder-violet.png")
            with open(png, "wb") as handle:
                handle.write(b"png")
            colored = _file_info("Cursos", Gio.FileType.DIRECTORY)
            colored.set_attribute_string(
                "metadata::custom-icon",
                Gio.File.new_for_path(png).get_uri(),
            )
            colored.set_attribute_string("metadata::custom-icon-name", "folder-yellow")
            colored.set_icon(plain_icon)
            entry = _entry_from_info(directory, colored)
            self.assertEqual(entry.icon_file, os.path.abspath(png))
            self.assertEqual(entry.folder_icon_names, ())
            self.assertEqual(entry.icon_name, "folder-symbolic")

        missing = _file_info("Missing", Gio.FileType.DIRECTORY)
        missing.set_attribute_string(
            "metadata::custom-icon",
            "file:///tmp/miller-no-such-folder-icon.png",
        )
        missing.set_icon(plain_icon)
        entry = _entry_from_info(directory, missing)
        self.assertEqual(entry.icon_file, "")
        self.assertEqual(entry.folder_icon_names, ("inode-directory", "folder"))

        remote = _file_info("Remote", Gio.FileType.DIRECTORY)
        remote.set_attribute_string(
            "metadata::custom-icon",
            "https://example.com/folder-violet.png",
        )
        remote.set_icon(plain_icon)
        entry = _entry_from_info(directory, remote)
        self.assertEqual(entry.icon_file, "")
        self.assertEqual(entry.folder_icon_names, ("inode-directory", "folder"))

        named = _file_info("Areas", Gio.FileType.DIRECTORY)
        named.set_attribute_string("metadata::custom-icon-name", "folder-yellow")
        named.set_icon(plain_icon)
        entry = _entry_from_info(directory, named)
        self.assertEqual(entry.icon_file, "")
        self.assertEqual(entry.folder_icon_names, ("folder-yellow",))
        self.assertEqual(entry.icon_name, "folder-symbolic")

        badged = _file_info("Badged", Gio.FileType.DIRECTORY)
        badged.set_attribute_stringv("metadata::emblems", ["favorite", "emblem-new"])
        badged.set_icon(plain_icon)
        entry = _entry_from_info(directory, badged)
        self.assertEqual(entry.emblems, ("emblem-favorite", "emblem-new"))
        self.assertEqual(entry.folder_icon_names, ("inode-directory", "folder"))

        plain = _file_info("Plain", Gio.FileType.DIRECTORY)
        plain.set_icon(plain_icon)
        entry = _entry_from_info(directory, plain)
        self.assertEqual(entry.icon_file, "")
        self.assertEqual(entry.emblems, ())
        self.assertEqual(entry.folder_icon_names, ("inode-directory", "folder"))
        self.assertEqual(entry.icon_name, "folder-symbolic")

        text = _file_info("note.txt", Gio.FileType.REGULAR)
        text.set_attribute_string(
            "metadata::custom-icon",
            "file:///usr/share/icons/ZorinBlue-Dark/48x48/places/folder-violet.png",
        )
        text.set_attribute_string("metadata::custom-icon-name", "folder-yellow")
        text.set_attribute_stringv("metadata::emblems", ["favorite"])
        text.set_icon(plain_icon)
        entry = _entry_from_info(directory, text)
        self.assertEqual(entry.icon_file, "")
        self.assertEqual(entry.folder_icon_names, ())
        self.assertEqual(entry.emblems, ())

    def test_get_file_metadata_preview_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = "miller-preview-source\n"
            encoded = marker.encode("utf-8")
            py_path = os.path.join(tmp, "sample.py")
            with open(py_path, "wb") as handle:
                handle.write(encoded + b"#" * (2048 - len(encoded)))

            meta = get_file_metadata(py_path)
            self.assertEqual(set(meta), _METADATA_KEYS)
            self.assertEqual(meta["preview_kind"], "text")
            self.assertIn("miller-preview-source", meta["preview_text"])
            self.assertEqual(meta["size_human"], "2.0 KB")
            self.assertIs(meta["is_dir"], False)
            self.assertEqual(meta["name"], "sample.py")
            self.assertEqual(meta["path"], os.path.abspath(py_path))
            self.assertRegex(
                meta["permissions"],
                r"^[r-][w-][x-][r-][w-][x-][r-][w-][x-] \([0-7]+\)$",
            )

            sized = os.path.join(tmp, "sized.txt")
            with open(sized, "wb") as handle:
                handle.write(b"x" * 1536)
            self.assertEqual(get_file_metadata(sized)["size_human"], "1.5 KB")

            png_path = os.path.join(tmp, "photo.png")
            with open(png_path, "wb") as handle:
                handle.write(b"\x89PNG\r\n")
            photo = get_file_metadata(png_path)
            self.assertEqual(photo["preview_kind"], "image")
            self.assertEqual(photo["preview_text"], "")

            bin_path = os.path.join(tmp, "data.bin")
            with open(bin_path, "wb") as handle:
                handle.write(b"\x00\x01\x02")
            binary = get_file_metadata(bin_path)
            self.assertEqual(binary["preview_kind"], "icon")
            self.assertEqual(binary["preview_text"], "")
            self.assertEqual(binary["size_human"], "3 B")

            lines_path = os.path.join(tmp, "lines.txt")
            with open(lines_path, "w", encoding="utf-8") as handle:
                handle.write("".join(f"line-{index}\n" for index in range(30)))
            preview = get_file_metadata(lines_path)["preview_text"]
            self.assertEqual(len(preview.splitlines()), 25)
            self.assertIn("line-0", preview)
            self.assertNotIn("line-25", preview)

    def test_mtime_human_matches_local_strftime(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "note.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("x")
            mtime = 1700000000
            atime = 1700003600
            os.utime(path, (atime, mtime))
            meta = get_file_metadata(path)
            self.assertEqual(
                meta["mtime_human"],
                datetime.datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M"),
            )
            self.assertEqual(
                meta["atime_human"],
                datetime.datetime.fromtimestamp(atime).strftime("%d/%m/%Y %H:%M"),
            )
            created = meta["ctime_human"]
            self.assertIsInstance(created, str)
            if created:
                datetime.datetime.strptime(created, "%d/%m/%Y %H:%M")
            else:
                self.assertEqual(created, "")

    def test_view_preference_inheritance_and_json_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = os.path.join(tmp, "parent")
            child = os.path.join(parent, "child")
            grandchild = os.path.join(child, "grand")
            sibling = os.path.join(tmp, "sibling")
            os.makedirs(grandchild)
            os.makedirs(sibling)
            store = os.path.join(tmp, "folder_views.json")

            set_miller_view_preferred(parent, True, store_path=store)
            self.assertTrue(resolve_miller_view_preferred(
                child,
                inherit_viewer=True,
                ignore_metadata=False,
                store_path=store,
            ))
            self.assertTrue(resolve_miller_view_preferred(
                grandchild,
                inherit_viewer=True,
                ignore_metadata=False,
                store_path=store,
            ))
            self.assertFalse(resolve_miller_view_preferred(
                sibling,
                inherited_state=False,
                inherit_viewer=True,
                ignore_metadata=False,
                store_path=store,
            ))
            self.assertFalse(resolve_miller_view_preferred(
                child,
                inherit_viewer=False,
                ignore_metadata=False,
                store_path=store,
            ))

            set_miller_view_preferred(child, False, store_path=store)
            self.assertFalse(resolve_miller_view_preferred(
                child,
                inherit_viewer=True,
                ignore_metadata=False,
                store_path=store,
            ))
            self.assertFalse(resolve_miller_view_preferred(
                grandchild,
                inherit_viewer=True,
                ignore_metadata=False,
                store_path=store,
            ))
            self.assertTrue(resolve_miller_view_preferred(
                parent,
                inherit_viewer=True,
                ignore_metadata=False,
                store_path=store,
            ))
            self.assertFalse(resolve_miller_view_preferred(
                parent,
                inherited_state=False,
                ignore_metadata=True,
                inherit_viewer=True,
                store_path=store,
            ))

            with open(store, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertIs(saved[os.path.abspath(parent)], True)
            self.assertIs(saved[os.path.abspath(child)], False)

        for name in ("data.py", "preference.py"):
            source_path = os.path.join(ROOT, "miller", name)
            with open(source_path, encoding="utf-8") as handle:
                self.assertNotIn("nemo-default-view", handle.read(), name)

    def test_is_dashboard_uri(self):
        self.assertTrue(is_dashboard_uri("~/.dashboard"))
        self.assertTrue(is_dashboard_uri(DASHBOARD_PATH))
        self.assertTrue(is_dashboard_uri("file://" + DASHBOARD_PATH))
        self.assertTrue(is_dashboard_uri(DASHBOARD_PATH + "/"))
        self.assertFalse(is_dashboard_uri(os.path.dirname(DASHBOARD_PATH)))
        self.assertFalse(is_dashboard_uri(DASHBOARD_PATH + "/extra"))

    def test_create_subfolder_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                create_subfolder(tmp, "")
            with self.assertRaises(ValueError):
                create_subfolder(tmp, "a/b")
            with self.assertRaises(ValueError):
                create_subfolder(tmp, "a\x00b")

            created = create_subfolder(tmp, "alpha")
            self.assertTrue(os.path.isdir(created))
            self.assertEqual(os.path.basename(created), "alpha")
            self.assertEqual(created, os.path.abspath(created))

            with self.assertRaises(ValueError):
                rename_entry(created, "")
            with self.assertRaises(ValueError):
                rename_entry(created, "bad/name")

            renamed = rename_entry(created, "beta")
            self.assertTrue(os.path.isdir(renamed))
            self.assertEqual(os.path.basename(renamed), "beta")
            self.assertFalse(os.path.exists(created))
            self.assertEqual(renamed, os.path.join(os.path.abspath(tmp), "beta"))

    def test_trash_entry(self):
        # /tmp is often a system mount that refuses GVFS trash. A temp dir
        # under the home directory uses the same trash the file manager uses.
        with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as tmp:
            target = os.path.join(tmp, "doomed.txt")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("bye")
            trashed = trash_entry(target)
            if not trashed:
                self.skipTest("GVFS trash unavailable")
            self.assertTrue(trashed)
            self.assertFalse(os.path.exists(target))
            _delete_trashed(target)

    def test_clipboard_payload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [
                os.path.join(tmp, "a.txt"),
                os.path.join(tmp, "b c.txt"),
            ]
            payload = format_gnome_copied_files(paths, cut=True)
            action, uris = parse_gnome_copied_files(payload)
            self.assertEqual(action, "cut")
            self.assertTrue(payload.startswith("cut\n"))
            self.assertTrue(payload.endswith("\n"))
            self.assertFalse(payload.endswith("\r\n"))
            expected = [Gio.File.new_for_path(path).get_uri() for path in paths]
            self.assertEqual(uris, expected)
            self.assertIn("%20", uris[1])

            copy_payload = format_gnome_copied_files(paths, cut=False)
            copy_action, copy_uris = parse_gnome_copied_files(copy_payload)
            self.assertEqual(copy_action, "copy")
            self.assertEqual(copy_uris, expected)

            default_action, default_uris = parse_gnome_copied_files(expected[0] + "\n")
            self.assertEqual(default_action, "copy")
            self.assertEqual(default_uris, [expected[0]])

            uri_list = format_uri_list(paths)
            self.assertTrue(uri_list.endswith("\r\n"))
            self.assertGreaterEqual(uri_list.count("\r\n"), 2)
            self.assertNotIn("\n", uri_list.replace("\r\n", ""))
            self.assertEqual(uri_list.split("\r\n")[:-1], expected)

    def test_open_in_same_window_emits_location_changed(self):
        if not ensure_gtk():
            self.skipTest("GTK display unavailable")
        received = []
        bar = location_bar_class()()
        bar.connect(
            "location-changed",
            lambda _widget, gfile: received.append(gfile.get_path()),
        )
        window = Gtk.Window()
        try:
            window.add(bar)
            target = os.path.abspath(os.path.join(tempfile.gettempdir(), "miller-columns-nav-target"))
            open_in_same_window(target, bar)
            while GLib.main_context_default().pending():
                GLib.main_context_default().iteration(False)
            self.assertEqual(received, [target])
        finally:
            window.destroy()

    def test_build_css_uses_theme_colors(self):
        css = build_css()
        for token in (
            ".mc-root",
            ".mc-scroll",
            ".mc-column",
            ".mc-column-current",
            ".mc-column-header",
            ".mc-column-title",
            ".mc-column-count",
            ".mc-column-search",
            ".mc-column-search-icon",
            ".mc-list",
            ".mc-row",
            ".mc-row:selected",
            ".mc-column-current .mc-row:selected",
            ".mc-chevron",
            ".mc-preview",
            ".mc-preview-content",
            ".mc-preview-tile",
            ".mc-preview-tile-accent",
            ".mc-preview-image",
            ".mc-preview-text",
            ".mc-preview-title",
            ".mc-preview-subtitle",
            ".mc-preview-section",
            ".mc-meta",
            ".mc-meta-label",
            ".mc-meta-value",
            ".mc-empty",
            ".mc-accent-ink",
            "alpha(@theme_selected_bg_color, 0.75)",
            "@theme_base_color",
            "@theme_fg_color",
            "@theme_bg_color",
            "@theme_selected_bg_color",
            "@theme_selected_fg_color",
            "min-width: 220px",
            "alpha(@theme_fg_color, 0.12)",
        ):
            self.assertIn(token, css)
        self.assertNotIn(".mc-action", css)
        self.assertNotRegex(css, r"#[0-9A-Fa-f]{3,8}")
        if not ensure_gtk():
            self.skipTest("GTK display unavailable")
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))

    def test_current_row_ink_is_theme_fg_until_accent_wins(self):
        css = build_css()
        self.assertIn(
            ".mc-column-current .mc-row:selected {\n"
            "  background-color: alpha(@theme_selected_bg_color, 0.75);\n"
            "  color: @theme_fg_color;",
            css,
        )
        self.assertIn(
            ".mc-root.mc-accent-ink .mc-column-current .mc-row:selected label {\n"
            "  color: @theme_selected_fg_color;",
            css,
        )
        self.assertIn(
            ".mc-root.mc-accent-ink .mc-column-current .mc-row:selected .mc-chevron {\n"
            "  color: @theme_selected_fg_color;",
            css,
        )
        self.assertNotIn(
            ".mc-column-current .mc-row:selected {\n"
            "  background-color: alpha(@theme_selected_bg_color, 0.75);\n"
            "  color: @theme_selected_fg_color;",
            css,
        )

    def test_washed_accent_keeps_plain_ink_when_it_contrasts_more(self):
        # Opaque pair prefers white by a hair. The 75% wash over white prefers black.
        base = _rgba(1, 1, 1)
        selected_bg = _rgba(79 / 255, 119 / 255, 174 / 255)
        theme_fg = _rgba(0, 0, 0)
        selected_fg = _rgba(1, 1, 1)
        self.assertFalse(
            selected_row_uses_accent_ink(base, selected_bg, theme_fg, selected_fg)
        )

    def test_dark_wash_keeps_accent_ink(self):
        base = _rgba(1, 1, 1)
        selected_bg = _rgba(0, 0, 0)
        theme_fg = _rgba(0, 0, 0)
        selected_fg = _rgba(1, 1, 1)
        self.assertTrue(
            selected_row_uses_accent_ink(base, selected_bg, theme_fg, selected_fg)
        )

    def test_light_wash_on_a_dark_theme_uses_accent_ink(self):
        base = _rgba(0.12, 0.12, 0.12)
        selected_bg = _rgba(0.95, 0.85, 0.15)
        theme_fg = _rgba(0.93, 0.93, 0.93)
        selected_fg = _rgba(0.08, 0.08, 0.08)
        self.assertTrue(
            selected_row_uses_accent_ink(base, selected_bg, theme_fg, selected_fg)
        )

    def test_equal_contrast_keeps_plain_ink(self):
        color = _rgba(0.2, 0.2, 0.2)
        self.assertFalse(selected_row_uses_accent_ink(color, color, color, color))

    def test_missing_theme_color_leaves_accent_ink_off(self):
        context = _ThemeColors({"theme_fg_color": _rgba(0, 0, 0)})
        self.assertFalse(accent_ink_for_context(context))

    def test_accent_ink_reads_the_four_theme_tokens_in_order(self):
        context = _ThemeColors(
            {
                "theme_base_color": _rgba(1, 1, 1),
                "theme_selected_bg_color": _rgba(0, 0, 0),
                "theme_fg_color": _rgba(0, 0, 0),
                "theme_selected_fg_color": _rgba(1, 1, 1),
            }
        )
        self.assertTrue(accent_ink_for_context(context))

    def test_applying_accent_ink_does_not_reenter(self):
        context = _ThemeColors(
            {
                "theme_base_color": _rgba(1, 1, 1),
                "theme_selected_bg_color": _rgba(0, 0, 0),
                "theme_fg_color": _rgba(0, 0, 0),
                "theme_selected_fg_color": _rgba(1, 1, 1),
            }
        )
        widget = _InkWidget(context)
        _apply_accent_ink(widget)
        self.assertEqual(widget.adds, 1)
        self.assertIn(ACCENT_INK_CLASS, context.classes)
        _apply_accent_ink(widget)
        self.assertEqual(widget.adds, 1)

    def test_bind_accent_ink_listens_once(self):
        if not ensure_gtk():
            self.skipTest("GTK display unavailable")
        widget = Gtk.EventBox()
        widget.get_style_context().add_class("mc-root")
        connections = []
        real_connect = widget.connect

        def spy(signal, callback, *args):
            connections.append(signal)
            return real_connect(signal, callback, *args)

        widget.connect = spy
        bind_accent_ink(widget)
        bind_accent_ink(widget)
        self.assertEqual(connections, ["style-updated"])

    def test_choose_drop_action_follows_modifiers_and_filesystem(self):
        offered = (
            Gdk.DragAction.COPY
            | Gdk.DragAction.MOVE
            | Gdk.DragAction.LINK
            | Gdk.DragAction.ASK
        )
        cases = (
            (Gdk.ModifierType.CONTROL_MASK, True, Gdk.DragAction.COPY),
            (Gdk.ModifierType.SHIFT_MASK, False, Gdk.DragAction.MOVE),
            (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, True, Gdk.DragAction.LINK),
            (Gdk.ModifierType.MOD1_MASK, False, Gdk.DragAction.ASK),
            (0, True, Gdk.DragAction.MOVE),
            (0, False, Gdk.DragAction.COPY),
        )
        for modifiers, same, expected in cases:
            got = choose_drop_action(offered, Gdk.DragAction.COPY, modifiers, same)
            self.assertEqual(got, expected)
        self.assertEqual(
            choose_drop_action(Gdk.DragAction.COPY, Gdk.DragAction.COPY, 0, True),
            Gdk.DragAction.COPY,
        )
        self.assertEqual(
            int(choose_drop_action(Gdk.DragAction.COPY, Gdk.DragAction.MOVE, 0, True)),
            0,
        )

    def test_filter_drop_uris_drops_self_descendant_and_same_dir_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "folder")
            child = os.path.join(folder, "child")
            other = os.path.join(tmp, "other")
            os.makedirs(child)
            os.makedirs(other)
            file_path = os.path.join(tmp, "file.txt")
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("file")

            def uri(path):
                return Gio.File.new_for_path(path).get_uri()

            self.assertEqual(filter_drop_uris([uri(folder)], folder, Gdk.DragAction.COPY), [])
            self.assertEqual(filter_drop_uris([uri(folder)], child, Gdk.DragAction.MOVE), [])
            self.assertEqual(filter_drop_uris([uri(file_path)], tmp, Gdk.DragAction.MOVE), [])
            self.assertEqual(
                filter_drop_uris([uri(file_path)], tmp, Gdk.DragAction.COPY),
                [uri(file_path)],
            )
            self.assertEqual(
                filter_drop_uris([uri(file_path)], other, Gdk.DragAction.MOVE),
                [uri(file_path)],
            )
            remote = "https://example.test/miller"
            self.assertEqual(filter_drop_uris([remote], other, Gdk.DragAction.MOVE), [remote])

    def test_same_filesystem_and_transfer_routing(self):
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source.txt")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("source")
            self.assertTrue(same_filesystem(Gio.File.new_for_path(source).get_uri(), tmp))
            self.assertFalse(same_filesystem("file:///no/such/miller-columns-path", tmp))

            calls = []
            original = data_module._call_nemo_file_operations
            data_module._call_nemo_file_operations = (
                lambda method, uris, dest: calls.append((method, list(uris), dest)) or True
            )
            try:
                dest = os.path.join(tmp, "dest")
                os.makedirs(dest)
                self.assertTrue(transfer_uris(["file:///a"], dest, Gdk.DragAction.MOVE))
                self.assertEqual(calls[-1][0], "MoveURIs")
                self.assertTrue(calls[-1][2].endswith("/dest"))
                self.assertTrue(transfer_uris(["file:///a"], dest, Gdk.DragAction.COPY))
                self.assertEqual(calls[-1][0], "CopyURIs")
            finally:
                data_module._call_nemo_file_operations = original

            link_dest = os.path.join(tmp, "links")
            os.makedirs(link_dest)
            source_uri = Gio.File.new_for_path(source).get_uri()
            self.assertTrue(transfer_uris([source_uri], link_dest, Gdk.DragAction.LINK))
            first = os.path.join(link_dest, "source.txt")
            self.assertTrue(os.path.islink(first))
            self.assertEqual(os.path.realpath(first), os.path.realpath(source))
            self.assertTrue(transfer_uris([source_uri], link_dest, Gdk.DragAction.LINK))
            second = os.path.join(link_dest, "source.txt 2")
            self.assertTrue(os.path.islink(second))
            self.assertEqual(os.path.realpath(second), os.path.realpath(source))

            other = os.path.join(tmp, "other.txt")
            with open(other, "w", encoding="utf-8") as handle:
                handle.write("other")
            other_uri = Gio.File.new_for_path(other).get_uri()
            rollback = os.path.join(tmp, "rollback")
            os.makedirs(rollback)
            original_unique = data_module._unique_link
            calls = {"n": 0}

            def fail_second(dest_dir, basename):
                calls["n"] += 1
                if calls["n"] >= 2:
                    return Gio.File.new_for_path(os.path.join(dest_dir, "missing", basename))
                return original_unique(dest_dir, basename)

            data_module._unique_link = fail_second
            try:
                self.assertFalse(transfer_uris([source_uri, other_uri], rollback, Gdk.DragAction.LINK))
            finally:
                data_module._unique_link = original_unique
            self.assertEqual(os.listdir(rollback), [])

        original_bus = Gio.bus_get_sync
        Gio.bus_get_sync = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no bus"))
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                self.assertFalse(
                    data_module._call_nemo_file_operations("MoveURIs", ["file:///a"], "file:///tmp")
                )
            self.assertIn("[miller]", buffer.getvalue())
        finally:
            Gio.bus_get_sync = original_bus

    def test_show_item_properties_queues_file_manager1(self):
        self.assertFalse(show_item_properties([]))

        calls = []

        class _Bus:
            def call(self, *args, **kwargs):
                calls.append(args)

        original_available = data_module._nemo_bus_available
        original_bus = Gio.bus_get_sync
        data_module._nemo_bus_available = lambda: True
        Gio.bus_get_sync = lambda *a, **k: _Bus()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("x")
                folder = os.path.join(tmp, "Docs")
                os.makedirs(folder)
                self.assertTrue(show_item_properties([note, folder]))
                self.assertEqual(len(calls), 1)
                bus_name, object_path, interface, method, variant = calls[0][:5]
                self.assertEqual(bus_name, "org.Nemo")
                self.assertEqual(object_path, "/org/freedesktop/FileManager1")
                self.assertEqual(interface, "org.freedesktop.FileManager1")
                self.assertEqual(method, "ShowItemProperties")
                uris, startup = variant.unpack()
                self.assertEqual(
                    list(uris),
                    [
                        Gio.File.new_for_path(note).get_uri(),
                        Gio.File.new_for_path(folder).get_uri(),
                    ],
                )
                self.assertEqual(startup, "")
        finally:
            data_module._nemo_bus_available = original_available
            Gio.bus_get_sync = original_bus

        calls.clear()
        data_module._nemo_bus_available = lambda: False
        Gio.bus_get_sync = lambda *a, **k: (_ for _ in ()).throw(AssertionError("bus"))
        try:
            self.assertFalse(show_item_properties(["/tmp"]))
            self.assertEqual(calls, [])
        finally:
            data_module._nemo_bus_available = original_available
            Gio.bus_get_sync = original_bus

    def test_recommended_apps_and_launch_with_app(self):
        class _App:
            def __init__(self, app_id):
                self.app_id = app_id

            def get_id(self):
                return self.app_id

        gedit = _App("gedit")
        code = _App("code")
        defaults = {"text/plain": gedit}
        groups = {"text/plain": [code], "text/x-python": [code]}
        original_default = Gio.AppInfo.get_default_for_type
        original_recommended = Gio.AppInfo.get_recommended_for_type
        Gio.AppInfo.get_default_for_type = lambda content_type, must: defaults.get(content_type)
        Gio.AppInfo.get_recommended_for_type = lambda content_type: list(groups.get(content_type, []))
        try:
            self.assertEqual(
                [app.get_id() for app in recommended_apps(["text/plain"])],
                ["gedit", "code"],
            )
            groups["text/plain"] = [gedit, code]
            self.assertEqual(
                [app.get_id() for app in recommended_apps(["text/plain", "text/x-python"])],
                ["code"],
            )
        finally:
            Gio.AppInfo.get_default_for_type = original_default
            Gio.AppInfo.get_recommended_for_type = original_recommended

        class _Launcher:
            def __init__(self):
                self.files = None

            def launch(self, files, context):
                self.files = files
                return True

        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(launch_with_app(_Launcher(), [tmp]))
            note = os.path.join(tmp, "note.txt")
            with open(note, "w", encoding="utf-8") as handle:
                handle.write("note")
            launcher = _Launcher()
            self.assertTrue(launch_with_app(launcher, [note]))
            self.assertEqual(len(launcher.files), 1)

    def test_data_exports_nemo_menu_helpers(self):
        from miller.data import (
            MenuTarget,
            NemoAction,
            ScriptNode,
            action_visible,
            expand,
            extension_items,
            load_actions,
            load_scripts,
            run_action,
            run_script,
        )

        self.assertTrue(callable(load_actions))
        self.assertTrue(callable(action_visible))
        self.assertTrue(callable(expand))
        self.assertTrue(callable(run_action))
        self.assertTrue(callable(load_scripts))
        self.assertTrue(callable(run_script))
        self.assertTrue(callable(extension_items))
        self.assertTrue(callable(MenuTarget))
        self.assertTrue(callable(NemoAction))
        self.assertTrue(callable(ScriptNode))

    def test_show_file_preview_builds_uri_and_swallows_bus_errors(self):
        import io
        from contextlib import redirect_stdout

        if not ensure_gtk():
            self.skipTest("GTK display unavailable")

        calls = []

        class _Bus:
            def call(self, *args, **kwargs):
                calls.append(args)

        original_bus = Gio.bus_get_sync
        Gio.bus_get_sync = lambda *a, **k: _Bus()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("x")
                widget = Gtk.Label()
                show_file_preview(note, widget)
                self.assertEqual(len(calls), 1)
                bus_name, object_path, interface, method, variant = calls[0][:5]
                self.assertEqual(bus_name, "org.nemo.Preview")
                self.assertEqual(object_path, "/org/nemo/Preview")
                self.assertEqual(interface, "org.nemo.Preview")
                self.assertEqual(method, "ShowFile")
                uri, xid, close_if_shown = variant.unpack()
                self.assertEqual(uri, Gio.File.new_for_path(note).get_uri())
                self.assertEqual(xid, 0)
                self.assertTrue(close_if_shown)
        finally:
            Gio.bus_get_sync = original_bus

        class _Boom:
            def call(self, *args, **kwargs):
                raise RuntimeError("unknown service")

        Gio.bus_get_sync = lambda *a, **k: _Boom()
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                show_file_preview("/tmp", Gtk.Label())
            self.assertIn("[miller]", buffer.getvalue())
        finally:
            Gio.bus_get_sync = original_bus

    def test_name_has_owner_queries_the_given_name(self):
        calls = []

        class _Bus:
            def call_sync(self, *args, **kwargs):
                calls.append(args)
                return GLib.Variant("(b)", (True,))

        original = Gio.bus_get_sync
        Gio.bus_get_sync = lambda *a, **k: _Bus()
        try:
            self.assertTrue(data_module._name_has_owner("org.example.Foo"))
            self.assertEqual(calls[0][4].unpack()[0], "org.example.Foo")
        finally:
            Gio.bus_get_sync = original


if __name__ == "__main__":
    unittest.main()
