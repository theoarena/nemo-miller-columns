"""GNOME copy/cut clipboard for local files.

GTK's Python bindings do not wrap ``gtk_clipboard_set_with_data``, so the
cut flag is published through ctypes. Paste reads
``x-special/gnome-copied-files``.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, Gtk

from miller.listing import _gio_file


_GNOME_COPIED_FILES = b"x-special/gnome-copied-files"
_TEXT_URI_LIST = b"text/uri-list"



def format_gnome_copied_files(paths: list[str], cut: bool = False) -> str:
    """Build the ``x-special/gnome-copied-files`` payload.

    The first line is the operation because a paste cannot tell a cut from
    a copy using URIs alone. Each following line is a Gio URI. Local paths
    and URIs are both accepted.

    Args:
        paths: Files to place on the clipboard.
        cut: True for a cut, false for a copy.

    Returns:
        ``copy`` or ``cut``, a newline, the URIs, and a trailing newline.

    @tags: #scope/file-ops #action/read #side-effect/clipboard
    """
    action = "cut" if cut else "copy"
    lines = [action]
    for path in paths:
        lines.append(_gio_file(path).get_uri())
    return "\n".join(lines) + "\n"


def parse_gnome_copied_files(payload: str) -> tuple[str, list[str]]:
    """Parse a ``x-special/gnome-copied-files`` payload.

    A missing or unrecognized first line defaults to ``copy`` and is kept
    as a URI when it is non-empty. Blank lines are dropped.

    Args:
        payload: Clipboard bytes decoded as text.

    Returns:
        ``("copy" or "cut", uri strings)``.

    @tags: #scope/file-ops #action/read #side-effect/clipboard
    """
    lines = payload.splitlines()
    action = "copy"
    body = lines
    if lines and lines[0].strip() in ("copy", "cut"):
        action = lines[0].strip()
        body = lines[1:]
    uris = [line.strip() for line in body if line.strip()]
    return action, uris


def format_uri_list(paths: list[str]) -> str:
    """Build an RFC 2483 ``text/uri-list`` body.

    Args:
        paths: Local paths or URIs.

    Returns:
        URIs separated by ``CRLF``, with a trailing ``CRLF``.

    @tags: #scope/file-ops #action/read #side-effect/clipboard
    """
    uris = [_gio_file(path).get_uri() for path in paths]
    return "\r\n".join(uris) + "\r\n"


def copy_to_clipboard(paths: list[str], cut: bool = False) -> None:
    """Publish ``paths`` on the CLIPBOARD selection.

    Sets ``x-special/gnome-copied-files`` (operation plus URIs) and
    ``text/uri-list`` (RFC 2483). Nemo pastes the first; other apps read
    the second. No display is not an error: the call returns so a headless
    process does not crash.

    Args:
        paths: Files to copy or cut.
        cut: True to advertise a cut.

    @tags: #scope/file-ops #action/write #side-effect/clipboard
    """
    if not _display_available():
        return
    try:
        _set_clipboard_targets(
            format_gnome_copied_files(paths, cut=cut).encode("utf-8"),
            format_uri_list(paths).encode("utf-8"),
        )
    except Exception as error:
        print(f"[miller] Failed to copy to clipboard: {error}")


def paste_from_clipboard(dest_dir: str) -> list[str]:
    """Paste the GNOME clipboard into ``dest_dir``.

    Copy uses ``Gio.File.copy`` and, when the basename is taken, inserts
    ``" (copy)"`` before the extension, then ``" (copy 2)"`` and so on.
    Cut uses ``Gio.File.move``. An empty clipboard or a missing display
    returns an empty list.

    Args:
        dest_dir: Directory that receives the files.

    Returns:
        Absolute paths of the files that landed in ``dest_dir``, in
        clipboard order.

    @tags: #scope/file-ops #action/write #side-effect/clipboard
    """
    if not _display_available():
        return []
    try:
        payload = _read_gnome_clipboard()
    except Exception as error:
        print(f"[miller] Failed to read clipboard: {error}")
        return []
    if not payload:
        return []
    action, uris = parse_gnome_copied_files(payload)
    if not uris:
        return []

    destination_dir = os.path.abspath(os.path.expanduser(dest_dir))
    written: list[str] = []
    for uri in uris:
        source = Gio.File.new_for_uri(uri)
        basename = source.get_basename()
        if not basename:
            raise ValueError(f"Clipboard URI has no basename: {uri}")
        if action == "cut":
            destination = Gio.File.new_for_path(os.path.join(destination_dir, basename))
            source.move(destination, Gio.FileCopyFlags.NONE, None, None, None)
        else:
            destination = _unique_destination(destination_dir, basename)
            source.copy(destination, Gio.FileCopyFlags.NONE, None, None, None)
        local = destination.get_path()
        if not local:
            raise OSError(f"Paste of {uri} did not produce a local path")
        written.append(os.path.abspath(local))
    return written



def _unique_destination(dest_dir: str, basename: str) -> Gio.File:
    """Pick a non-colliding destination file for paste-copy.

    @tags: #scope/file-ops #action/write #type/helper
    """
    direct = Gio.File.new_for_path(os.path.join(dest_dir, basename))
    if not direct.query_exists(None):
        return direct
    stem, extension = os.path.splitext(basename)
    # First collision uses the GNOME name ``name (copy).ext``. Later pastes
    # increment so they don't overwrite the previous copy.
    for index in range(1, 10000):
        if index == 1:
            candidate_name = f"{stem} (copy){extension}"
        else:
            candidate_name = f"{stem} (copy {index}){extension}"
        candidate = Gio.File.new_for_path(os.path.join(dest_dir, candidate_name))
        if not candidate.query_exists(None):
            return candidate
    raise RuntimeError(f"No free name for {basename!r} in {dest_dir}")


def _display_available() -> bool:
    """Return whether a GDK display exists.

    ``Gtk.init`` aborts the process when the display is missing, so the
    probe is ``init_check`` only.

    @tags: #scope/widget #action/read #type/helper
    """
    try:
        if Gdk.Display.get_default() is not None:
            return True
        checked = Gtk.init_check([])
        ready = checked[0] if isinstance(checked, tuple) else bool(checked)
        return bool(ready) and Gdk.Display.get_default() is not None
    except Exception:
        return False


class _GtkTargetEntry(ctypes.Structure):
    """C ``GtkTargetEntry``. The Python override of this struct cannot be
    passed to ``gtk_clipboard_set_with_data``, which PyGObject does not wrap.

    @tags: #type/helper #complexity/high #side-effect/clipboard
    """

    _fields_ = (
        ("target", ctypes.c_char_p),
        ("flags", ctypes.c_uint),
        ("info", ctypes.c_uint),
    )


_CLIPBOARD_GET_TYPE = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p
)
_CLIPBOARD_CLEAR_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

# Module globals on purpose. GTK invokes the callbacks later from C, and a
# function object that only lives on the stack would be freed first.
_CLIPBOARD_PAYLOADS: dict = {}
_CLIPBOARD_TARGET_NAMES: dict = {}
_CLIPBOARD_TARGET_ARRAY = None
_libgtk_cdll = None
_libgdk_cdll = None

_PY_API = ctypes.PyDLL(None)
_PY_API.PyCapsule_GetPointer.restype = ctypes.c_void_p
_PY_API.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]


def _libgtk():
    """Lazy-load libgtk-3 and clipboard C function signatures.

    @tags: #type/helper #complexity/high #side-effect/clipboard
    """
    global _libgtk_cdll
    if _libgtk_cdll is None:
        library = ctypes.util.find_library("gtk-3") or "libgtk-3.so.0"
        handle = ctypes.CDLL(library)
        handle.gtk_clipboard_set_with_data.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_GtkTargetEntry),
            ctypes.c_uint,
            _CLIPBOARD_GET_TYPE,
            _CLIPBOARD_CLEAR_TYPE,
            ctypes.c_void_p,
        ]
        handle.gtk_clipboard_set_with_data.restype = ctypes.c_int
        handle.gtk_selection_data_set.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        handle.gtk_selection_data_set.restype = None
        _libgtk_cdll = handle
    return _libgtk_cdll


def _libgdk():
    """Lazy-load libgdk-3 for clipboard atom interning.

    @tags: #type/helper #complexity/high #side-effect/clipboard
    """
    global _libgdk_cdll
    if _libgdk_cdll is None:
        library = ctypes.util.find_library("gdk-3") or "libgdk-3.so.0"
        handle = ctypes.CDLL(library)
        handle.gdk_atom_intern.argtypes = [ctypes.c_char_p, ctypes.c_int]
        handle.gdk_atom_intern.restype = ctypes.c_void_p
        _libgdk_cdll = handle
    return _libgdk_cdll


def _pygobject_pointer(obj: object) -> int:
    """Extract the C pointer from a PyGObject wrapper.

    @tags: #type/helper #complexity/high
    """
    pointer = _PY_API.PyCapsule_GetPointer(obj.__gpointer__, None)
    if not pointer:
        raise RuntimeError("GTK object has no C pointer")
    return pointer


def _clipboard_get(clipboard_ptr, selection_ptr, info, user_data) -> None:
    """GTK clipboard get callback that serves GNOME copy/cut payloads.

    @tags: #scope/file-ops #action/read #side-effect/clipboard #complexity/high
    """
    try:
        key = int(info)
        data = _CLIPBOARD_PAYLOADS.get(key)
        name = _CLIPBOARD_TARGET_NAMES.get(key)
        if data is None or name is None:
            return
        atom = _libgdk().gdk_atom_intern(name, False)
        _libgtk().gtk_selection_data_set(selection_ptr, atom, 8, data, len(data))
    except Exception as error:
        print(f"[miller] Failed to serve clipboard data: {error}")


def _clipboard_clear(_clipboard_ptr, _user_data) -> None:
    return None


_CLIPBOARD_GET_CB = _CLIPBOARD_GET_TYPE(_clipboard_get)
_CLIPBOARD_CLEAR_CB = _CLIPBOARD_CLEAR_TYPE(_clipboard_clear)


def _set_clipboard_targets(gnome_payload: bytes, uri_list_payload: bytes) -> None:
    """Publish clipboard targets via raw GTK C API.

    @tags: #scope/file-ops #action/write #side-effect/clipboard #complexity/high
    """
    global _CLIPBOARD_TARGET_ARRAY
    _CLIPBOARD_PAYLOADS[0] = gnome_payload
    _CLIPBOARD_PAYLOADS[1] = uri_list_payload
    _CLIPBOARD_TARGET_NAMES[0] = _GNOME_COPIED_FILES
    _CLIPBOARD_TARGET_NAMES[1] = _TEXT_URI_LIST
    entries = (_GtkTargetEntry * 2)()
    entries[0] = _GtkTargetEntry(_GNOME_COPIED_FILES, 0, 0)
    entries[1] = _GtkTargetEntry(_TEXT_URI_LIST, 0, 1)
    # GTK keeps the target strings. The array has to outlive this function.
    _CLIPBOARD_TARGET_ARRAY = entries
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    accepted = _libgtk().gtk_clipboard_set_with_data(
        _pygobject_pointer(clipboard),
        entries,
        2,
        _CLIPBOARD_GET_CB,
        _CLIPBOARD_CLEAR_CB,
        None,
    )
    if not accepted:
        print("[miller] Clipboard rejected the copy targets")


def _read_gnome_clipboard() -> str:
    """Read the ``x-special/gnome-copied-files`` clipboard payload.

    @tags: #scope/file-ops #action/read #side-effect/clipboard
    """
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    atom = Gdk.Atom.intern("x-special/gnome-copied-files", False)
    selection = clipboard.wait_for_contents(atom)
    if selection is None:
        return ""
    raw = selection.get_data()
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    return bytes(raw).decode("utf-8", errors="replace")
