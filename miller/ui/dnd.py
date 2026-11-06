"""Drag source and drop target for one Miller column.

The column publishes ``text/uri-list`` and accepts the same target. Copy
and move are not performed here: ``transfer_uris`` asks Nemo, which already
owns progress dialogs, conflicts, and undo. Link is the exception and stays
a local symlink.

A drag that starts on an unselected row selects that row without telling
the board. Telling it would open a folder column for a press that became
a drag.
"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from miller.data import (
    choose_drop_action,
    filter_drop_uris,
    same_filesystem,
    transfer_uris,
)
from miller.i18n import _

# Every action Nemo's views offer. The actual action is chosen per motion.
# @tags: #type/constant #scope/file-ops
_DRAG_ACTIONS = (
    Gdk.DragAction.COPY
    | Gdk.DragAction.MOVE
    | Gdk.DragAction.LINK
    | Gdk.DragAction.ASK
)
_URI_LIST = "text/uri-list"

_ASK_ITEMS = (
    ("Move Here", Gdk.DragAction.MOVE),
    ("Copy Here", Gdk.DragAction.COPY),
    ("Link Here", Gdk.DragAction.LINK),
    ("Cancel", None),
)


def attach_drag_and_drop(column) -> None:
    """Make ``column`` a URI drag source and a manually handled drop target.

    ``DestDefaults`` stays empty so GTK does not highlight or choose an
    action on its own. Motion decides both.

    Args:
        column: ``MillerColumn`` whose list is wired.

    @tags: #scope/widget #scope/file-ops #action/bind #lifecycle/init
    """
    column._drag_uris = {}
    column._drag_fetching = set()
    column._drop_active = False
    column._drop_point = (0, 0)
    column._motion_point = (0, 0, 0)
    column._drop_menu = None
    listbox = column.listbox
    listbox.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [], _DRAG_ACTIONS)
    listbox.drag_source_add_uri_targets()
    listbox.drag_dest_set(0, [], _DRAG_ACTIONS)
    listbox.drag_dest_add_uri_targets()
    listbox.connect("drag-begin", lambda _widget, context: on_drag_begin(column, context))
    listbox.connect(
        "drag-data-get",
        lambda _widget, context, data, info, time: on_drag_data_get(column, context, data, info, time),
    )
    listbox.connect(
        "drag-motion",
        lambda _widget, context, x, y, time: on_drag_motion(column, context, x, y, time),
    )
    listbox.connect("drag-leave", lambda _widget, context, time: on_drag_leave(column, context, time))
    listbox.connect(
        "drag-drop",
        lambda _widget, context, x, y, time: on_drag_drop(column, context, x, y, time),
    )
    listbox.connect(
        "drag-data-received",
        lambda _widget, context, x, y, data, info, time: on_drag_data_received(
            column, context, x, y, data, info, time
        ),
    )
    listbox.connect("drag-end", lambda _widget, context: on_drag_end(column, context))


def on_drag_begin(column, context) -> None:
    """Select the pressed row when it was not part of the drag, then set the icon.

    The selection flag and the click counter are cleared here. After a drag
    starts, the button release never reaches the list, and leaving them set
    would open a folder or a file when the grab ends.

    Args:
        column: Source column.
        context: GDK drag context.

    @tags: #scope/file-ops #scope/widget #action/bind
    """
    row = getattr(column, "_press_row", None)
    if row is not None and getattr(row, "file_entry", None) is not None and not row.is_selected():
        column._set_selection([row], notify=False)
        column._anchor_row = row
    column._selection_from_user = False
    column._primary_presses = 0
    entries = column.get_selected_entries()
    if len(entries) == 1:
        Gtk.drag_set_icon_name(context, entries[0].icon_name or "text-x-generic-symbolic", 0, 0)
        return
    if len(entries) > 1:
        Gtk.drag_set_icon_widget(context, _count_icon(entries), 0, 0)


def on_drag_data_get(column, _context, selection_data, _info, _time) -> None:
    """Publish the selected URIs in list order.

    Args:
        column: Source column.
        selection_data: GTK selection to fill.

    @tags: #scope/file-ops #action/read #scope/widget
    """
    selection_data.set_uris([entry.uri for entry in column.get_selected_entries()])


def on_drag_motion(column, context, x, y, time) -> bool:
    """Highlight the folder under the pointer, or the column, and report an action.

    URIs are requested once per context. Until they arrive the drop is
    refused so a move is not advertised for a payload we have not seen.

    Args:
        column: Destination column.
        context: GDK drag context.
        x: Pointer x relative to the list.
        y: Pointer y relative to the list.
        time: Event time.

    Returns:
        True. The column always handles motion itself.

    @tags: #scope/file-ops #scope/widget #action/bind
    """
    column._motion_point = (x, y, time)
    _highlight(column, x, y)
    cached = column._drag_uris.get(id(context))
    if cached is None:
        if id(context) not in column._drag_fetching:
            column._drag_fetching.add(id(context))
            column.listbox.drag_get_data(context, Gdk.Atom.intern_static_string(_URI_LIST), time)
        Gdk.drag_status(context, Gdk.DragAction(0), time)
        return True
    _publish_status(column, context, x, y, time)
    return True


def on_drag_leave(column, _context, _time) -> None:
    """Clear the highlight when the pointer leaves the list.

    @tags: #scope/widget #action/clear #scope/file-ops
    """
    _clear_highlight(column)


def on_drag_drop(column, context, x, y, time) -> bool:
    """Accept the drop and wait for the URI payload when it is not cached yet.

    Args:
        column: Destination column.
        context: GDK drag context.
        x: Drop x relative to the list.
        y: Drop y relative to the list.
        time: Event time.

    Returns:
        True so GTK delivers ``drag-data-received``.

    @tags: #scope/file-ops #action/write #scope/widget
    """
    column._drop_point = (x, y)
    column._drop_time = time
    cached = column._drag_uris.get(id(context))
    if cached is None:
        column._drop_active = True
        column.listbox.drag_get_data(context, Gdk.Atom.intern_static_string(_URI_LIST), time)
        return True
    _complete_drop(column, context, cached, x, y, time)
    return True


def on_drag_data_received(column, context, _x, _y, selection_data, _info, time) -> None:
    """Cache URIs. During a drop, transfer them.

    Motion and drop both land here. ``_drop_active`` tells them apart:
    motion only needs the cache so the next motion can call ``drag_status``.

    @tags: #scope/file-ops #action/write #scope/widget
    """
    getter = getattr(selection_data, "get_uris", None)
    uris = list(getter() or []) if getter is not None else []
    column._drag_uris[id(context)] = uris
    column._drag_fetching.discard(id(context))
    if column._drop_active:
        column._drop_active = False
        point = column._drop_point
        _complete_drop(column, context, uris, point[0], point[1], time)
        return
    motion_x, motion_y, motion_time = column._motion_point
    _publish_status(column, context, motion_x, motion_y, motion_time or time)


def on_drag_end(column, context) -> None:
    """Forget the URI cache for this context.

    @tags: #scope/file-ops #lifecycle/destroy #scope/widget
    """
    column._drag_uris.pop(id(context), None)
    column._drag_fetching.discard(id(context))
    _clear_highlight(column)


def pointer_modifiers(widget, context) -> int:
    """Read the modifier mask under the drag pointer.

    ``drag-motion`` does not carry modifiers. The device position on the
    list window does.

    Args:
        widget: Widget that owns the window, usually the list.
        context: GDK drag context.

    Returns:
        A ``Gdk.ModifierType`` mask, or ``0`` when the device is unknown.

    @tags: #scope/file-ops #action/read #type/helper
    """
    window = widget.get_window() if widget is not None else None
    getter = getattr(context, "get_device", None)
    device = getter() if getter is not None else None
    if window is None or device is None:
        return 0
    position = window.get_device_position(device)
    if not position:
        return 0
    return int(position[3] or 0)


def build_ask_menu(column, context, uris, dest, time) -> Gtk.Menu:
    """Build the Alt-drop menu. Activating an item transfers or cancels.

    The menu is returned so a test can activate an item without a grab.
    ``column._drop_menu`` keeps the widget alive until the column replaces it.

    Args:
        column: Destination column.
        context: GDK drag context finished when an item is chosen.
        uris: Payload already filtered only for self and ancestor drops.
        dest: Destination directory.
        time: Event time passed to ``drag_finish``.

    Returns:
        A ``Gtk.Menu`` with move, copy, link, and cancel.

    @tags: #scope/file-ops #scope/widget #action/render
    """
    menu = Gtk.Menu()
    column._drop_menu = menu
    # activate runs before the menu closes. A click outside the menu only
    # emits deactivate, and the drag grab stays up until drag_finish.
    state = {"done": False}

    def finish_once(ok: bool) -> None:
        """Finish the drag once; ignore duplicate menu deactivate calls.

        @tags: #scope/file-ops #action/write #lifecycle/destroy #scope/widget
        """
        if state["done"]:
            return
        state["done"] = True
        finish_drag(context, ok, time)
        _clear_highlight(column)

    for label, action in _ASK_ITEMS:
        item = Gtk.MenuItem(label=_(label))
        item.connect(
            "activate",
            lambda _item, action=action: _on_ask_activate(
                column, context, uris, dest, action, time, finish_once
            ),
        )
        menu.append(item)
    menu.connect("deactivate", lambda _menu: finish_once(False))
    menu.show_all()
    return menu


def _count_icon(entries) -> Gtk.Widget:
    """Icon plus the number of dragged rows.

    @tags: #scope/widget #action/render #type/helper
    """
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    image = Gtk.Image.new_from_icon_name(
        entries[0].icon_name or "text-x-generic-symbolic",
        Gtk.IconSize.DND,
    )
    label = Gtk.Label(label=str(len(entries)))
    box.pack_start(image, False, False, 0)
    box.pack_start(label, False, False, 0)
    box.show_all()
    return box


def _highlight(column, x, y) -> str:
    """Highlight the folder row under ``(x, y)``, or the whole column.

    Returns:
        Directory path that would receive the drop.

    @tags: #scope/widget #action/render #scope/file-ops #type/helper
    """
    row = column.listbox.get_row_at_y(int(y))
    entry = getattr(row, "file_entry", None) if row is not None else None
    if entry is not None and entry.is_dir:
        column.listbox.drag_highlight_row(row)
        column.drag_unhighlight()
        return entry.path
    column.listbox.drag_unhighlight_row()
    column.drag_highlight()
    return column.directory_path


def _clear_highlight(column) -> None:
    """Remove row and column drop highlights.

    ``gtk_drag_highlight`` is a widget method in the GTK 3 binding.

    @tags: #scope/widget #action/clear #type/helper
    """
    column.listbox.drag_unhighlight_row()
    column.drag_unhighlight()


def _action_for(column, context, dest, uris):
    """Return the action to advertise, or ``0`` when the drop is refused.

    Refused when the payload is empty, the chosen action is not offered,
    or every URI is the target, inside a dragged folder, or a same-directory
    move. Ask still allows a same-directory drop: the user may copy.

    @tags: #scope/file-ops #action/read #type/helper
    """
    if not uris:
        return Gdk.DragAction(0)
    modifiers = pointer_modifiers(column.listbox, context)
    same = same_filesystem(uris[0], dest)
    chosen = choose_drop_action(
        context.get_actions(),
        context.get_suggested_action(),
        modifiers,
        same,
    )
    if not chosen:
        return Gdk.DragAction(0)
    probe = Gdk.DragAction.COPY if chosen == Gdk.DragAction.ASK else chosen
    if not filter_drop_uris(uris, dest, probe):
        return Gdk.DragAction(0)
    return chosen


def _publish_status(column, context, x, y, time) -> None:
    """Set the drag status for the current pointer position.

    @tags: #scope/file-ops #action/bind #type/helper
    """
    dest = _highlight(column, x, y)
    action = _action_for(column, context, dest, column._drag_uris.get(id(context)) or [])
    Gdk.drag_status(context, action, time)


def _complete_drop(column, context, uris, x, y, time) -> None:
    """Transfer ``uris`` or open the ask menu.

    ``drag_finish`` uses delete false. Nemo moves the files itself; asking
    GTK to delete them as well would race that move.

    @tags: #scope/file-ops #action/write #type/helper
    """
    dest = _highlight(column, x, y)
    chosen = _action_for(column, context, dest, uris)
    if not chosen:
        finish_drag(context, False, time)
        _clear_highlight(column)
        return
    if chosen == Gdk.DragAction.ASK:
        menu = build_ask_menu(column, context, list(uris), dest, time)
        menu.popup(None, None, None, None, 1, time)
        return
    filtered = filter_drop_uris(uris, dest, chosen)
    ok = bool(filtered) and transfer_uris(filtered, dest, chosen)
    finish_drag(context, ok, time)
    _clear_highlight(column)


def _on_ask_activate(column, context, uris, dest, action, time, finish_once) -> None:
    """Run the menu choice. Cancel finishes the drag as a failure.

    ``finish_once`` also runs from the menu's deactivate signal, so a
    dismissed menu cannot finish the drag a second time.

    @tags: #scope/file-ops #action/write #scope/widget
    """
    if action is None:
        finish_once(False)
        return
    filtered = filter_drop_uris(uris, dest, action)
    ok = bool(filtered) and transfer_uris(filtered, dest, action)
    finish_once(ok)


def finish_drag(context, ok: bool, time) -> None:
    """Finish the context without asking the source to delete the originals.

    @tags: #scope/file-ops #action/write #type/helper
    """
    try:
        Gtk.drag_finish(context, bool(ok), False, time)
    except Exception as error:
        print(f"[miller] drag_finish failed: {error}")
