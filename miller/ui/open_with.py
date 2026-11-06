"""“Open With” submenu and the application chooser.

The submenu lists apps that can open every selected file. Folders are not
offered a handler: launching a directory can start another Nemo. The chooser
is shown with ``show`` and a ``response`` handler: ``dialog.run`` would nest
a main loop inside Nemo.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk

from miller.data import launch_with_app, recommended_apps
from miller.i18n import _

# Match column row icons so large FileIcon apps do not blow up the menu.
# @tags: #type/constant #scope/widget #scope/view-preference
_MENU_ICON_SIZE = 16


def open_with_menu_item(column) -> Gtk.MenuItem:
    """Build the “Open With” item for the current selection.

    The item is insensitive when nothing is selected or any selected row is
    a folder. The submenu is built at popup time so the app list matches
    the files under the pointer.

    Args:
        column: Column that owns the selection.

    Returns:
        A ``Gtk.MenuItem``. Sensitive items have a submenu.

    @tags: #scope/widget #scope/file-ops #action/render
    """
    item = Gtk.MenuItem(label=_("Open With"))
    entries = column.get_selected_entries()
    if not entries or any(entry.is_dir for entry in entries):
        item.set_sensitive(False)
        return item
    item.set_submenu(build_open_with_submenu(column, entries))
    item.set_sensitive(True)
    return item


def build_open_with_submenu(column, entries) -> Gtk.Menu:
    """Build recommended apps, a separator, and “Other Application…”.

    Args:
        column: Column used as the chooser parent.
        entries: Selected files. Every entry is a file.

    Returns:
        A ``Gtk.Menu``.

    @tags: #scope/widget #scope/file-ops #action/render
    """
    menu = Gtk.Menu()
    content_types = []
    for entry in entries:
        if entry.content_type and entry.content_type not in content_types:
            content_types.append(entry.content_type)
    paths = [entry.path for entry in entries]
    for app in recommended_apps(content_types):
        menu.append(_app_item(app, paths))
    menu.append(Gtk.SeparatorMenuItem())
    other = Gtk.MenuItem(label=_("Other Application\u2026"))
    other.connect("activate", lambda _item: present_app_chooser(column, paths))
    menu.append(other)
    return menu


def build_app_chooser(parent, paths: list[str]) -> Gtk.AppChooserDialog:
    """Return a modal chooser for ``paths`` without running it.

    One file uses the file itself so the dialog can offer that file's
    type. Several files use the first file's content type; GTK has no
    chooser for a mixed list.

    Args:
        parent: Transient parent, or None.
        paths: One or more local files.

    Returns:
        A ``Gtk.AppChooserDialog``. The caller connects ``response`` or
        inspects the widget.

    @tags: #scope/widget #scope/file-ops #action/render
    """
    window = parent if isinstance(parent, Gtk.Window) else None
    if len(paths) == 1:
        return Gtk.AppChooserDialog.new(
            window,
            Gtk.DialogFlags.MODAL,
            Gio.File.new_for_path(paths[0]),
        )
    return Gtk.AppChooserDialog.new_for_content_type(
        window,
        Gtk.DialogFlags.MODAL,
        _content_type(paths[0]),
    )


def present_app_chooser(column, paths: list[str]) -> Gtk.AppChooserDialog:
    """Show the chooser and launch the chosen app on OK.

    Args:
        column: Widget whose toplevel is the parent window.
        paths: Files to open.

    Returns:
        The dialog already shown.

    @tags: #scope/widget #scope/navigation #action/navigate
    """
    parent = column.get_toplevel()
    dialog = build_app_chooser(parent, paths)
    dialog.connect(
        "response",
        lambda widget, response, chosen=list(paths): _on_chooser_response(widget, response, chosen),
    )
    dialog.show()
    return dialog


def _app_item(app, paths: list[str]) -> Gtk.MenuItem:
    """One recommended app, with its Gio icon beside the name.

    The label property stays set after the child is replaced, so callers
    can still read ``get_label``.

    @tags: #scope/widget #action/render #scope/file-ops #type/helper
    """
    name = app.get_display_name() or app.get_name() or app.get_id() or ""
    item = Gtk.MenuItem(label=name)
    child = item.get_child()
    if child is not None:
        item.remove(child)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    icon = app.get_icon()
    if icon is not None:
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.MENU)
    else:
        image = Gtk.Image.new_from_icon_name("application-x-executable-symbolic", Gtk.IconSize.MENU)
    image.set_pixel_size(_MENU_ICON_SIZE)
    box.pack_start(image, False, False, 0)
    box.pack_start(Gtk.Label(label=name), False, False, 0)
    item.add(box)
    item.connect("activate", lambda _item, app=app: launch_with_app(app, paths))
    return item


def _content_type(path: str) -> str:
    """Content type of ``path``, or a generic type when Gio cannot say.

    @tags: #scope/file-ops #action/read #type/helper
    """
    try:
        info = Gio.File.new_for_path(path).query_info(
            "standard::content-type",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
        found = info.get_content_type()
        if found:
            return found
    except Exception:
        pass
    guessed, _uncertain = Gio.content_type_guess(os.path.basename(path), None)
    return guessed or "application/octet-stream"


def _on_chooser_response(dialog, response, paths: list[str]) -> None:
    """Launch the chosen app, then destroy the dialog either way.

    @tags: #scope/navigation #action/navigate #lifecycle/destroy #scope/widget
    """
    try:
        if response == Gtk.ResponseType.OK:
            app = dialog.get_app_info()
            if app is not None:
                launch_with_app(app, paths)
    finally:
        dialog.destroy()
