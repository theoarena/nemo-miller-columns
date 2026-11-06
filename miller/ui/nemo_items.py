"""Turn Nemo actions, scripts, and extension items into Gtk menu rows.

Looks up ``load_actions``, ``load_scripts``, ``extension_items``,
``run_action``, and ``run_script`` as module globals so tests can rebind
them on this module the same way they rebind ``open_file_default`` on
``miller.ui.column``.
"""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from miller.i18n import _
from miller.data import (
    MenuTarget,
    action_visible,
    expand,
    extension_items,
    load_actions,
    load_scripts,
    run_action,
    run_script,
)

try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11
except (ValueError, ImportError):
    GdkX11 = None

# Match column row icons and miller.ui.open_with.
# @tags: #type/constant #scope/widget
_MENU_ICON_SIZE = 16


def action_items(column, target: MenuTarget) -> list[Gtk.MenuItem]:
    """Build mnemonic items for actions visible on ``target``.

    Args:
        column: Column used for the window XID.
        target: Current selection.

    Returns:
        Gtk items. Activating one calls ``run_action``.

    @tags: #scope/widget #scope/file-ops #action/render
    """
    xid = _column_xid(column)
    items: list[Gtk.MenuItem] = []
    for action in load_actions():
        if not action_visible(action, target):
            continue
        label = expand(action.label, action, target, xid, for_label=True)
        item = Gtk.MenuItem(label=label, use_underline=True)
        if action.tooltip:
            item.set_tooltip_text(expand(action.tooltip, action, target, xid))
        if action.icon_name:
            _pack_icon(item, action.icon_name, label)
        item.connect(
            "activate",
            lambda _item, chosen=action: run_action(chosen, target, _column_xid(column)),
        )
        items.append(item)
    return items


def scripts_menu_item(column, target: MenuTarget) -> Optional[Gtk.MenuItem]:
    """Return a ``Scripts`` submenu, or None when the tree is empty.

    Args:
        column: Column used for window geometry.
        target: Current selection.

    Returns:
        A menu item, or None.

    @tags: #scope/widget #scope/file-ops #action/render
    """
    nodes = load_scripts()
    if not nodes:
        return None
    item = Gtk.MenuItem(label=_("Scripts"))
    item.set_submenu(_scripts_menu(nodes, column, target))
    return item


def extension_menu_items(column, target: MenuTarget, background: bool) -> list[Gtk.MenuItem]:
    """Convert ``Nemo.MenuItem`` values from providers into Gtk items.

    Separators, insensitive rows, and nested ``Nemo.Menu`` submenus map
    across. Items that only carry ``widget-a`` / ``widget-b`` are skipped.

    Args:
        column: Column whose toplevel is passed to the providers.
        target: Current selection.
        background: True for a blank-area click.

    Returns:
        Gtk items. Activating one calls ``item.activate()``.

    @tags: #scope/widget #scope/file-ops #action/render
    """
    window = column.get_toplevel()
    if not isinstance(window, Gtk.Window):
        window = None
    converted: list[Gtk.MenuItem] = []
    for item in extension_items(window, target, background):
        gtk_item = _nemo_item_to_gtk(item)
        if gtk_item is not None:
            converted.append(gtk_item)
    return converted



def _pack_icon(item: Gtk.MenuItem, icon_name: str, label: str) -> None:
    """Put a 16px icon beside the label, matching Open With.

    @tags: #scope/widget #action/render #type/helper
    """
    child = item.get_child()
    if child is not None:
        item.remove(child)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
    image.set_pixel_size(_MENU_ICON_SIZE)
    box.pack_start(image, False, False, 0)
    name = Gtk.Label(label=label, use_underline=True)
    name.set_xalign(0)
    box.pack_start(name, False, False, 0)
    item.add(box)


def _scripts_menu(nodes, column, target: MenuTarget) -> Gtk.Menu:
    """Build a nested Gtk menu for script nodes.

    @tags: #scope/widget #scope/file-ops #action/render #type/helper
    """
    menu = Gtk.Menu()
    for node in nodes:
        label = node.label.replace("_", "__")
        item = Gtk.MenuItem(label=label, use_underline=True)
        if node.children:
            item.set_submenu(_scripts_menu(node.children, column, target))
        else:
            item.connect(
                "activate",
                lambda _item, path=node.path: run_script(path, target, _column_geometry(column)),
            )
        menu.append(item)
    return menu


def _nemo_item_to_gtk(item) -> Optional[Gtk.MenuItem]:
    """Convert one ``Nemo.MenuItem`` into a Gtk row when mappable.

    @tags: #scope/widget #scope/file-ops #action/render #type/helper #type/extension
    """
    if item.get_property("separator"):
        return Gtk.SeparatorMenuItem()
    widget_a = item.get_property("widget-a")
    widget_b = item.get_property("widget-b")
    label = item.get_property("label") or ""
    submenu = item.get_property("menu")
    if (widget_a is not None or widget_b is not None) and not label and submenu is None:
        return None
    gtk_item = Gtk.MenuItem(label=label, use_underline=True)
    tip = item.get_property("tip")
    if tip:
        gtk_item.set_tooltip_text(tip)
    gtk_item.set_sensitive(bool(item.get_property("sensitive")))
    icon = item.get_property("icon")
    if icon:
        _pack_icon(gtk_item, str(icon), label)
    if submenu is not None:
        gtk_sub = Gtk.Menu()
        for child in submenu.get_items() or []:
            converted = _nemo_item_to_gtk(child)
            if converted is not None:
                gtk_sub.append(converted)
        gtk_item.set_submenu(gtk_sub)
    gtk_item.connect("activate", lambda _item, source=item: source.activate())
    return gtk_item


def _column_xid(column) -> int:
    """Return the X11 window id for the column toplevel, or zero.

    @tags: #scope/widget #scope/navigation #action/read #type/helper
    """
    try:
        toplevel = column.get_toplevel()
        gdk_window = toplevel.get_window() if isinstance(toplevel, Gtk.Window) else None
        if gdk_window is None:
            return 0
        if GdkX11 is not None:
            return int(GdkX11.X11Window.get_xid(gdk_window))
        getter = getattr(gdk_window, "get_xid", None)
        return int(getter()) if getter is not None else 0
    except Exception:
        return 0


def _column_geometry(column) -> str:
    """Format ``WIDTHxHEIGHT+X+Y`` for the column toplevel window.

    @tags: #scope/widget #scope/navigation #action/read #type/helper
    """
    toplevel = column.get_toplevel()
    if not isinstance(toplevel, Gtk.Window):
        return ""
    try:
        width, height = toplevel.get_size()
        x_pos, y_pos = toplevel.get_position()
        return f"{width}x{height}+{x_pos}+{y_pos}"
    except Exception:
        return ""
