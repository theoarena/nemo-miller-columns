"""Open files and navigate the current Nemo window.

Folder changes stay inside the current process. Emitting a navigation
signal on idle avoids re-entering Nemo's view switch from a click handler,
and it avoids starting another file-manager process (that path opens a
second window and has crashed Nemo 6 on Wayland).
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11
except (ValueError, ImportError):
    GdkX11 = None

from miller.listing import _gio_file, _local_path
from miller.transfer import _on_nemo_file_operation



def open_in_same_window(path_or_uri: str, widget: Gtk.Widget = None) -> None:
    """Navigate to a folder inside the current Nemo window.

    Emits ``location-changed`` on a ``NemoLocationBar``, or ``path-clicked``
    on a ``NemoPathBar``, with a ``Gio.File``. The emit is scheduled on
    idle so this function returns before Nemo handles the signal. If
    neither bar is in the target window, the call returns. It does not
    launch a handler: the default handler for a folder can start another
    Nemo process.

    Args:
        path_or_uri: Folder path or URI.
        widget: Widget whose toplevel is the target window. When omitted,
            the active application window is used.

    @tags: #scope/navigation #action/navigate
    """
    try:
        file_obj = _gio_file(path_or_uri)
        window = _resolve_target_window(widget)
        # Either bar can navigate. Neither one is not recovered by launching
        # a process; the folder simply stays where it is.
        has_bar = window is not None and (
            _find_navigation_bar(window, widget, "NemoLocationBar") is not None
            or _find_navigation_bar(window, widget, "NemoPathBar") is not None
        )
        if not has_bar:
            print(f"[miller] No in-window navigation bar for {path_or_uri}")
            return
        GLib.idle_add(_emit_in_window_navigation, window, file_obj, widget)
    except Exception as error:
        print(f"[miller] Failed to navigate to {path_or_uri}: {error}")


def open_file_default(path_or_uri: str) -> None:
    """Open a file with the desktop default application.

    Folder navigation must not call this: the default handler for a
    directory can start Nemo. ``launch_with_app`` is the other launcher
    and only runs when the user picks an application.

    Args:
        path_or_uri: File path or URI.

    @tags: #scope/navigation #action/navigate
    """
    try:
        file_obj = _gio_file(path_or_uri)
        Gio.AppInfo.launch_default_for_uri(file_obj.get_uri(), None)
    except Exception as error:
        print(f"[miller] Failed to open {path_or_uri}: {error}")


def _preview_xid(widget: Gtk.Widget) -> int:
    """Return the X11 id of ``widget``'s toplevel, or zero.

    nemo-preview uses that id as the transient parent. A Wayland window
    is not a ``GdkX11.X11Window``, and passing a fake xid makes it error.

    @tags: #scope/widget #scope/navigation #action/read #type/helper #complexity/high
    """
    if widget is None:
        return 0
    try:
        toplevel = widget.get_toplevel()
        gdk_window = toplevel.get_window() if isinstance(toplevel, Gtk.Window) else None
        if gdk_window is None:
            return 0
        if GdkX11 is not None and isinstance(gdk_window, GdkX11.X11Window):
            return int(GdkX11.X11Window.get_xid(gdk_window))
    except Exception:
        return 0
    return 0


def show_file_preview(path_or_uri: str, widget: Gtk.Widget = None) -> None:
    """Ask ``org.nemo.Preview`` to show ``path_or_uri``.

    This is the same D-Bus method Nemo's icon and list views use. The
    call is async: a missing previewer or a bus error is logged and
    ignored, so Space does nothing visible when the service is absent.
    ``closeIfShown`` is true, matching Nemo's toggle.

    Args:
        path_or_uri: File path or URI.
        widget: Widget whose toplevel supplies the X11 xid. Zero when
            the window is not an X11 window.

    @tags: #scope/navigation #scope/file-ops #action/navigate #type/helper
    """
    try:
        uri = _gio_file(path_or_uri).get_uri()
        xid = _preview_xid(widget)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(
            "org.nemo.Preview",
            "/org/nemo/Preview",
            "org.nemo.Preview",
            "ShowFile",
            GLib.Variant("(sib)", (uri, xid, True)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            _on_nemo_file_operation,
            "ShowFile",
        )
    except Exception as error:
        print(f"[miller] {error}")


def recommended_apps(content_types: list[str]) -> list[Gio.AppInfo]:
    """Return apps that can open every type, default of the first type first.

    One type keeps its default even when that app is not in the recommended
    list. Several types keep only ids recommended for all of them, so a
    mixed selection does not offer an app that can open only one file.

    Args:
        content_types: MIME types of the selected files, first selection first.

    Returns:
        Deduplicated ``Gio.AppInfo`` objects. Empty when ``content_types`` is.

    @tags: #scope/file-ops #action/read #type/helper
    """
    types = [item for item in content_types if item]
    if not types:
        return []
    groups = []
    for content_type in types:
        groups.append(list(Gio.AppInfo.get_recommended_for_type(content_type) or []))
    shared_ids = None
    for found in groups:
        ids = {app.get_id() for app in found if app.get_id()}
        shared_ids = ids if shared_ids is None else shared_ids & ids
    if shared_ids is None:
        shared_ids = set()
    several = len(types) > 1
    ordered = []
    seen = set()

    def accept(app, require_shared: bool) -> bool:
        """Keep an app when its id is new and shared across types when required.

        @tags: #scope/file-ops #action/read #type/helper
        """
        app_id = app.get_id()
        if not app_id or app_id in seen:
            return False
        if require_shared and app_id not in shared_ids:
            return False
        return True

    default = Gio.AppInfo.get_default_for_type(types[0], False)
    if default is not None and accept(default, several):
        ordered.append(default)
        seen.add(default.get_id())
    for app in groups[0]:
        if accept(app, several):
            ordered.append(app)
            seen.add(app.get_id())
    return ordered


def launch_with_app(app_info: Gio.AppInfo, paths: list[str]) -> bool:
    """Launch ``app_info`` on ``paths``.

    A directory rejects the whole call. Folder navigation must not start
    a handler, and a mixed selection must not launch the files either.

    Args:
        app_info: Application the user chose.
        paths: Local paths or URIs.

    Returns:
        True when ``Gio.AppInfo.launch`` accepted the files.

    @tags: #scope/navigation #action/navigate #scope/file-ops
    """
    if not paths or app_info is None:
        return False
    files = []
    for path in paths:
        local = _local_path(path) or path
        if os.path.isdir(local):
            return False
        files.append(_gio_file(path))
    try:
        display = Gdk.Display.get_default()
        context = display.get_app_launch_context() if display is not None else None
        return bool(app_info.launch(files, context))
    except Exception as error:
        print(f"[miller] Failed to launch {paths}: {error}")
        return False



def show_item_properties(paths: list[str]) -> bool:
    """Ask Nemo to open its stock properties window for ``paths``.

    Queues ``ShowItemProperties`` on ``org.freedesktop.FileManager1``.
    The call is async for the same reason as file operations: a sync call
    into Nemo from its main loop never returns. Empty input returns false
    without touching the bus.

    Args:
        paths: Local paths or URIs, in the order they should appear.

    Returns:
        True when the async call was queued.

    @tags: #scope/file-ops #action/read #side-effect/filesystem
    """
    if not paths:
        return False
    import miller.data as data

    if not data._nemo_bus_available():
        return False
    uris = [_gio_file(path).get_uri() for path in paths]
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(
            "org.Nemo",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            "ShowItemProperties",
            GLib.Variant("(ass)", (uris, "")),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            _on_nemo_file_operation,
            "ShowItemProperties",
        )
    except Exception as error:
        print(f"[miller] {error}")
        return False
    return True



def _find_widget_by_type_name(root: Gtk.Widget, type_name: str):
    """Depth-first search by GObject type name.

    Nemo's bars are created in the host process. This package cannot import
    those classes, so a test double that registers the same type name is
    found the same way as the real widget.

    @tags: #scope/navigation #action/find #type/helper
    """
    if root is None:
        return None
    gtype_name = GObject.type_name(root.__gtype__) if hasattr(root, "__gtype__") else ""
    if gtype_name == type_name:
        return root
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found = _find_widget_by_type_name(child, type_name)
            if found is not None:
                return found
    return None


def _resolve_target_window(widget: Gtk.Widget = None):
    """Pick the Nemo window that should navigate.

    Prefer the widget the user clicked, then the application's active
    window, then a ``NemoWindow``. Falling straight through to "any
    toplevel" would steer a click into an unrelated dialog.

    @tags: #scope/navigation #action/find #type/helper
    """
    if isinstance(widget, Gtk.Window):
        return widget
    if isinstance(widget, Gtk.Widget):
        toplevel = widget.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            return toplevel

    app = Gio.Application.get_default()
    if isinstance(app, Gtk.Application):
        active = app.get_active_window()
        if active is not None:
            return active
        windows = app.get_windows()
        if windows:
            return windows[0]

    toplevels = Gtk.Window.list_toplevels()
    nemo_windows = [
        window
        for window in toplevels
        if hasattr(window, "__gtype__") and GObject.type_name(window.__gtype__) == "NemoWindow"
    ]
    for window in nemo_windows:
        if window.is_active():
            return window
    if nemo_windows:
        return nemo_windows[0]
    for window in toplevels:
        if window.is_active():
            return window
    return toplevels[0] if toplevels else None


def _find_navigation_bar(window: Gtk.Window, source_widget: Gtk.Widget = None, type_name: str = "NemoLocationBar"):
    """Locate a Nemo location or path bar near the source widget.

    @tags: #scope/navigation #action/find #type/helper
    """
    # Walk up from the source first. A window can host more than one slot,
    # and the bar next to the widget that was clicked is the one to drive.
    current = source_widget
    while isinstance(current, Gtk.Widget):
        found = _find_widget_by_type_name(current, type_name)
        if found is not None:
            return found
        if current is window:
            break
        current = current.get_parent()
    return _find_widget_by_type_name(window, type_name)


def _emit_in_window_navigation(window: Gtk.Window, file_obj: Gio.File, source_widget: Gtk.Widget = None) -> bool:
    """Emit the navigation signal. Always returns false so idle runs once.

    @tags: #scope/navigation #action/navigate #lifecycle/destroy
    """
    try:
        location_bar = _find_navigation_bar(window, source_widget, "NemoLocationBar")
        if location_bar is not None:
            location_bar.emit("location-changed", file_obj)
            return False
        path_bar = _find_navigation_bar(window, source_widget, "NemoPathBar")
        if path_bar is not None:
            path_bar.emit("path-clicked", file_obj)
            return False
        print("[miller] Navigation bar disappeared before the idle handler ran")
    except Exception as error:
        print(f"[miller] Failed to emit navigation signal: {error}")
    # False removes the idle source. True would schedule another emit.
    return False
