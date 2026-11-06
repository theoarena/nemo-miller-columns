"""File mutations and drag-and-drop transfers.

Copies and moves go through ``org.Nemo.FileOperations`` on the session bus.
The call is asynchronous: a synchronous call from inside Nemo's main loop
deadlocks on Nemo's own bus name. Link stays a local symlink.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib



def create_subfolder(parent_path: str, name: str) -> str:
    """Create ``name`` inside ``parent_path`` and return its absolute path.

    Uses ``Gio.File.make_directory`` so a remote parent goes through the
    same backend Nemo uses, not a raw POSIX mkdir.

    Args:
        parent_path: Existing directory.
        name: Single path component.

    Returns:
        Absolute path of the new directory.

    Raises:
        ValueError: ``name`` is empty or contains ``/`` or NUL.
        GLib.Error: The backend rejected the create.

    @tags: #scope/file-ops #action/write #side-effect/filesystem
    """
    _validate_entry_name(name)
    parent = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(parent_path)))
    child = parent.get_child(name)
    child.make_directory(None)
    created = child.get_path()
    if not created:
        raise OSError(f"Created {name!r} but Gio did not return a local path")
    return os.path.abspath(created)


def rename_entry(path: str, new_name: str) -> str:
    """Rename ``path`` to ``new_name`` within the same parent.

    ``Gio.File.set_display_name`` keeps the operation on the file's backend
    (including remote mounts) instead of assuming a local ``os.rename``.

    Args:
        path: Existing file or directory.
        new_name: Single path component. Not a destination path.

    Returns:
        Absolute path after the rename.

    Raises:
        ValueError: ``new_name`` is empty or contains ``/`` or NUL.
        GLib.Error: The backend rejected the rename.

    @tags: #scope/file-ops #action/write #side-effect/filesystem
    """
    _validate_entry_name(new_name)
    source = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(path)))
    renamed = source.set_display_name(new_name, None)
    new_path = renamed.get_path()
    if not new_path:
        raise OSError(f"Renamed {path!r} but Gio did not return a local path")
    return os.path.abspath(new_path)


def trash_entry(path: str) -> bool:
    """Move ``path`` to the trash.

    Returns false when the backend cannot trash the file. The file is left
    in place: deleting it would skip the trash and surprise the user.

    Args:
        path: File or directory to trash.

    Returns:
        True when Gio accepted the trash operation.

    @tags: #scope/file-ops #action/write #side-effect/filesystem
    """
    try:
        return bool(Gio.File.new_for_path(path).trash(None))
    except Exception:
        return False



def choose_drop_action(
    actions: Gdk.DragAction,
    suggested: Gdk.DragAction,
    modifiers: Gdk.ModifierType,
    same_filesystem: bool,
) -> Gdk.DragAction:
    """Pick the drop action Nemo would use for these modifiers.

    Ctrl copies, Shift moves, Ctrl+Shift links, and Alt asks. With no
    modifier the drop moves on the same filesystem and copies across
    filesystems, even when the source suggested something else. The result
    is one action from ``actions``. When the preferred action was not
    offered, ``suggested`` is used if it is a single offered action.

    Args:
        actions: Actions the drag source offered.
        suggested: Action the source suggested.
        modifiers: Current pointer modifiers.
        same_filesystem: True when the first URI and the destination share
            ``id::filesystem``.

    Returns:
        One ``Gdk.DragAction`` flag, or ``0`` when nothing is allowed.

    @tags: #scope/file-ops #action/read #type/helper
    """
    modifiers = modifiers or 0
    ctrl = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)
    shift = bool(modifiers & Gdk.ModifierType.SHIFT_MASK)
    alt = bool(modifiers & Gdk.ModifierType.MOD1_MASK)
    if ctrl and shift:
        preferred = Gdk.DragAction.LINK
    elif ctrl:
        preferred = Gdk.DragAction.COPY
    elif shift:
        preferred = Gdk.DragAction.MOVE
    elif alt:
        preferred = Gdk.DragAction.ASK
    else:
        preferred = Gdk.DragAction.MOVE if same_filesystem else Gdk.DragAction.COPY
    if int(preferred) & int(actions):
        return preferred
    allowed = int(suggested) & int(actions)
    for candidate in (
        Gdk.DragAction.COPY,
        Gdk.DragAction.MOVE,
        Gdk.DragAction.LINK,
        Gdk.DragAction.ASK,
    ):
        if allowed == int(candidate):
            return candidate
    return Gdk.DragAction(0)


def same_filesystem(source_uri: str, dest_dir: str) -> bool:
    """Return whether ``source_uri`` and ``dest_dir`` share a filesystem.

    Compared with ``id::filesystem``. A missing file or a query failure
    returns false so the drop copies instead of attempting a move.

    Args:
        source_uri: URI being dragged.
        dest_dir: Destination directory path.

    Returns:
        True when both queries succeed and the ids match.

    @tags: #scope/file-ops #action/read #type/helper
    """
    try:
        source = Gio.File.new_for_uri(source_uri)
        dest = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(dest_dir)))
        source_info = source.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, None)
        dest_info = dest.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, None)
    except (GLib.Error, TypeError, ValueError):
        return False
    source_id = source_info.get_attribute_string("id::filesystem")
    dest_id = dest_info.get_attribute_string("id::filesystem")
    return bool(source_id) and source_id == dest_id


def filter_drop_uris(uris: list[str], dest_dir: str, action: Gdk.DragAction) -> list[str]:
    """Drop URIs that must not land in ``dest_dir``.

    A directory cannot be dropped onto itself or into its own descendant.
    A move into the directory that already contains the file is a no-op,
    so those URIs are removed only for ``MOVE``. Copy and link keep them.
    URIs without a local path are kept: they are not a self-drop we can see.

    Args:
        uris: Drag payload, ``text/uri-list``.
        dest_dir: Directory that would receive the files.
        action: Action about to run. Same-directory filtering applies to
            ``MOVE`` only.

    Returns:
        URIs that are safe to transfer, in the original order.

    @tags: #scope/file-ops #action/read #security/path-validation #type/helper
    """
    dest = os.path.abspath(os.path.expanduser(dest_dir))
    moving = action == Gdk.DragAction.MOVE
    kept: list[str] = []
    for uri in uris:
        if not uri:
            continue
        path = Gio.File.new_for_uri(uri).get_path()
        if not path:
            kept.append(uri)
            continue
        path = os.path.abspath(path)
        if path == dest or (os.path.isdir(path) and _path_is_inside(path, dest)):
            continue
        if moving and os.path.dirname(path) == dest:
            continue
        kept.append(uri)
    return kept


def transfer_uris(uris: list[str], dest_dir: str, action: Gdk.DragAction) -> bool:
    """Copy, move, or link ``uris`` into ``dest_dir``.

    Move and copy are handed to Nemo so progress, conflicts, and undo match
    the other views. Link is a local symlink with a free name. An empty
    list or an unknown action does nothing.

    Args:
        uris: Source URIs.
        dest_dir: Destination directory.
        action: ``MOVE``, ``COPY``, or ``LINK``.

    Returns:
        True when the operation was accepted. Move and copy return true
        once the async call is queued, not when Nemo finishes.

    @tags: #scope/file-ops #action/write #side-effect/filesystem
    """
    if not uris:
        return False
    destination = os.path.abspath(os.path.expanduser(dest_dir))
    dest_uri = Gio.File.new_for_path(destination).get_uri()
    import miller.data as data

    if action == Gdk.DragAction.MOVE:
        return data._call_nemo_file_operations("MoveURIs", list(uris), dest_uri)
    if action == Gdk.DragAction.COPY:
        return data._call_nemo_file_operations("CopyURIs", list(uris), dest_uri)
    if action == Gdk.DragAction.LINK:
        return _link_uris(uris, destination)
    return False



def _call_nemo_file_operations(method: str, uris: list[str], dest_uri: str) -> bool:
    """Queue ``MoveURIs`` or ``CopyURIs`` on ``org.Nemo.FileOperations``.

    The call is async. Nemo owns this bus name, and a sync call from its
    main loop never returns. A missing name is logged and returns false
    before the call is queued.

    Args:
        method: ``MoveURIs`` or ``CopyURIs``.
        uris: Source URIs.
        dest_uri: Destination directory URI.

    Returns:
        True when the async call was queued.

    @tags: #scope/file-ops #action/write #side-effect/filesystem #type/helper
    """
    import miller.data as data

    if not data._nemo_bus_available():
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(
            "org.Nemo",
            "/org/Nemo",
            "org.Nemo.FileOperations",
            method,
            GLib.Variant("(ass)", (uris, dest_uri)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            _on_nemo_file_operation,
            method,
        )
    except Exception as error:
        print(f"[miller] {error}")
        return False
    return True


def _name_has_owner(name: str) -> bool:
    """Return whether ``name`` owns a well-known name on the session bus.

    The check talks to the bus daemon, not to the owner, so it does not
    deadlock on Nemo's main loop.

    Args:
        name: Well-known bus name, for example ``org.Nemo``.

    Returns:
        True when some connection owns ``name``.

    @tags: #scope/file-ops #action/read #type/helper
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        variant = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
    except Exception as error:
        print(f"[miller] {error}")
        return False
    return bool(variant.unpack()[0]) if variant is not None else False


def _nemo_bus_available() -> bool:
    """Return whether ``org.Nemo`` owns a name on the session bus.

    The check talks to the bus daemon, not to Nemo, so it does not deadlock.

    @tags: #scope/file-ops #action/read #type/helper
    """
    owned = _name_has_owner("org.Nemo")
    if not owned:
        print("[miller] org.Nemo is not available")
    return owned


def _on_nemo_file_operation(connection, result, method) -> None:
    """Finish an async file operation and log a bus error.

    @tags: #scope/file-ops #action/write #lifecycle/destroy #type/helper
    """
    try:
        connection.call_finish(result)
    except Exception as error:
        print(f"[miller] {method} failed: {error}")


def _link_uris(uris: list[str], dest_dir: str) -> bool:
    """Create one symlink per URI, picking a free basename each time.

    A failure removes the links this call already created. The drop is
    reported as failed, so a half-written set would disagree with that.

    @tags: #scope/file-ops #action/write #side-effect/filesystem #type/helper
    """
    import miller.data as data

    created = []
    for uri in uris:
        source = Gio.File.new_for_uri(uri)
        target = source.get_path() or uri
        basename = source.get_basename() or "link"
        destination = data._unique_link(dest_dir, basename)
        try:
            destination.make_symbolic_link(target, None)
        except Exception as error:
            print(f"[miller] Failed to link {uri}: {error}")
            for made in created:
                try:
                    made.delete(None)
                except Exception as cleanup_error:
                    print(f"[miller] Failed to remove partial link: {cleanup_error}")
            return False
        created.append(destination)
    return bool(created)


def _unique_link(dest_dir: str, basename: str) -> Gio.File:
    """Return a path in ``dest_dir`` whose basename is not taken.

    The first free name is ``basename``, then ``basename 2``, and so on.
    ``lexists`` counts a broken symlink as taken.

    @tags: #scope/file-ops #action/write #type/helper
    """
    candidate = basename
    number = 2
    while os.path.lexists(os.path.join(dest_dir, candidate)):
        candidate = f"{basename} {number}"
        number += 1
        if number > 10000:
            break
    return Gio.File.new_for_path(os.path.join(dest_dir, candidate))


def _path_is_inside(parent: str, child: str) -> bool:
    """Return whether ``child`` is ``parent`` or a path under it.

    @tags: #scope/file-ops #security/path-validation #action/read #type/helper
    """
    parent_path = os.path.abspath(parent)
    child_path = os.path.abspath(child)
    if child_path == parent_path:
        return True
    prefix = parent_path.rstrip(os.sep) + os.sep
    return child_path.startswith(prefix)



def _validate_entry_name(name: str) -> None:
    """Reject path components that are not safe single-segment names.

    @tags: #scope/file-ops #security/path-validation #type/helper
    """
    # One path component. A slash would escape the parent; NUL is not a
    # legal filename byte on this platform.
    if name == "" or "/" in name or "\x00" in name:
        raise ValueError(f"Invalid file name: {name!r}")
