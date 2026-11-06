"""Directory listing and file metadata for Miller columns.

The column UI renders what ``list_directory`` and ``get_file_metadata``
return. Shared path helpers used by the other data modules live here too.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


# Glance, not a reader. The byte cap bounds IO; the line cap bounds the pane.
_PREVIEW_BYTE_LIMIT = 2048
_PREVIEW_LINE_LIMIT = 20

# Extensions are consulted because a few-byte file often sniffs as a generic
# type. Image is decided before text so those bytes are never shown as text.
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"})
_TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".py", ".json", ".css", ".js", ".ts",
    ".sh", ".xml", ".yml", ".yaml", ".ini", ".toml",
})
_TEXT_CONTENT_TYPES = frozenset({
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-yaml",
    "application/x-shellscript",
    "application/x-zerosize",
})

# @tags: #type/constant #scope/file-ops #side-effect/filesystem
_LIST_ATTRIBUTES = ",".join((
    "standard::name",
    "standard::type",
    "standard::size",
    "standard::content-type",
    "time::modified",
    "time::access",
    "time::created",
    "unix::mode",
    # Folder color lives in metadata. standard::icon still says "folder".
    "standard::icon",
    "metadata::custom-icon",
    "metadata::custom-icon-name",
    "metadata::emblems",
))

# One async page. The column still sorts the full result before painting,
# so a larger page only cuts callback traffic.
# @tags: #type/constant #scope/file-ops #format/int
_ENUMERATE_BATCH = 256


@dataclass
class FileEntry:
    """One row in a Miller column.

    Attributes:
        name: Basename. Never a path.
        path: Absolute local path.
        uri: URI from Gio, shared with the clipboard and navigation.
        is_dir: True for directories. Symlinks stay files; the column
            opens the link target only when the user activates it.
        size: ``standard::size`` in bytes. Directories may be 0.
        mtime: Modification time in seconds since the epoch, or 0 if unknown.
        atime: Access time in seconds since the epoch, or 0 if unknown.
        content_type: MIME type, or a Gio guess when the file has none.
        icon_name: Theme icon for preview and drag. Directories always
            use ``folder-symbolic``. The column does not paint that name:
            Nemo keeps the folder color in ``metadata::custom-icon``, and
            ``standard::icon`` still says ``folder``.
        permissions_str: Symbolic mode plus octal, for example
            ``rw-r--r-- (644)``.
        is_hidden: True when ``name`` starts with a dot. ``.`` and ``..``
            are never listed.
        icon_file: Local path of a ``file://`` ``metadata::custom-icon``
            that exists on disk. Empty for files, and when the stored
            icon is missing or is not a file URI.
        folder_icon_names: Theme names for a directory when ``icon_file``
            is empty. ``metadata::custom-icon-name`` when Nemo set one,
            otherwise ``standard::icon`` with ``-symbolic`` removed.
            Empty for files.
        emblems: Badge names for a directory. A stored name that already
            starts with ``emblem-`` is kept; a short name such as
            ``favorite`` is prefixed so the theme can resolve it. Empty
            for files.

    @tags: #type/data-model #scope/file-ops
    """

    name: str
    path: str
    uri: str
    is_dir: bool
    size: int
    mtime: float
    atime: float
    content_type: str
    icon_name: str
    permissions_str: str
    is_hidden: bool
    icon_file: str = ""
    folder_icon_names: tuple[str, ...] = ()
    emblems: tuple[str, ...] = ()


def list_directory(path: str, show_hidden: bool = False) -> list[FileEntry]:
    """List one directory level for a column.

    Missing or unreadable paths return an empty list instead of raising, so
    a column can render an empty state while the user is mid-navigation.
    Results are directories first, then files, each group in case-insensitive
    natural order (``file2`` before ``file10``).

    Args:
        path: Directory path. ``~`` is expanded.
        show_hidden: When false, names starting with ``.`` are omitted.
            ``.`` and ``..`` are never returned either way.

    Returns:
        Entry rows. Empty when ``path`` cannot be listed.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    directory = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(path)))
    enumerator = None
    entries: list[FileEntry] = []
    try:
        enumerator = directory.enumerate_children(
            _LIST_ATTRIBUTES,
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            entry = _entry_from_info(directory, info)
            if _include_entry(entry, show_hidden):
                entries.append(entry)
    except GLib.Error:
        return []
    finally:
        _close_enumerator(enumerator)
    return _sort_entries(entries)


def list_directory_async(
    path: str,
    show_hidden: bool,
    cancellable: Optional[Gio.Cancellable],
    callback: Callable[[list[FileEntry]], None],
) -> None:
    """List one directory off the calling thread and pass the rows to ``callback``.

    Enumeration runs in the GLib thread pool. ``callback`` runs on the
    main context that was current when this function was called, with the
    same order and hidden-file rules as ``list_directory``. The rows are
    delivered once, after the last page: a column that is about to be
    destroyed should not paint a prefix it will throw away.

    A cancel delivers nothing. Skipping ``callback`` on cancel avoids applying
    rows after ``refresh`` or destroy replaced the column state—the worker may
    finish after those paths ran. It is also not invoked when
    ``cancellable`` is already cancelled. Any other failure delivers an
    empty list, matching the synchronous contract.

    Args:
        path: Directory path. ``~`` is expanded.
        show_hidden: When false, names starting with ``.`` are omitted.
        cancellable: Cancel handle. ``None`` cannot be cancelled.
        callback: Receives the finished rows. Not called after a cancel.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem #complexity/high
    """
    if cancellable is not None and cancellable.is_cancelled():
        return
    directory = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(path)))
    entries: list[FileEntry] = []

    def finish(enumerator, error: Optional[GLib.Error]) -> None:
        _close_enumerator(enumerator)
        if _load_was_cancelled(error, cancellable):
            # No callback: see ``list_directory_async`` docstring—caller already
            # moved on (refresh/destroy).
            return
        callback([] if error is not None else _sort_entries(entries))

    def on_next(enumerator, result, _data) -> None:
        # Gio requires ``*_finish`` for every async result; abandoning ``result``
        # leaks the in-flight op. Cancellation still skips ``callback`` above.
        try:
            infos = enumerator.next_files_finish(result)
        except GLib.Error as error:
            finish(enumerator, error)
            return
        if cancellable is not None and cancellable.is_cancelled():
            _close_enumerator(enumerator)
            return
        if not infos:
            finish(enumerator, None)
            return
        for info in infos:
            entry = _entry_from_info(directory, info)
            if _include_entry(entry, show_hidden):
                entries.append(entry)
        if cancellable is not None and cancellable.is_cancelled():
            _close_enumerator(enumerator)
            return
        enumerator.next_files_async(
            _ENUMERATE_BATCH,
            GLib.PRIORITY_DEFAULT,
            cancellable,
            on_next,
            None,
        )

    def on_enumerated(source, result, _data) -> None:
        try:
            enumerator = source.enumerate_children_finish(result)
        except GLib.Error as error:
            finish(None, error)
            return
        if cancellable is not None and cancellable.is_cancelled():
            _close_enumerator(enumerator)
            return
        enumerator.next_files_async(
            _ENUMERATE_BATCH,
            GLib.PRIORITY_DEFAULT,
            cancellable,
            on_next,
            None,
        )

    directory.enumerate_children_async(
        _LIST_ATTRIBUTES,
        Gio.FileQueryInfoFlags.NONE,
        GLib.PRIORITY_DEFAULT,
        cancellable,
        on_enumerated,
        None,
    )


def _include_entry(entry: FileEntry, show_hidden: bool) -> bool:
    """Return whether one enumerated name belongs in the column.

    ``.`` and ``..`` are never listed. Dotfiles stay out unless the column
    asked for them. Gio's hidden flag is ignored: on some backends it does
    not match a leading dot.

    Args:
        entry: Row built from one ``Gio.FileInfo``.
        show_hidden: True when dotfiles are included.

    Returns:
        True when the row should be kept.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/pure-function
    """
    if entry.name in (".", ".."):
        return False
    if entry.is_hidden and not show_hidden:
        return False
    return True


def _sort_entries(entries: list[FileEntry]) -> list[FileEntry]:
    """Order directories first, then case-insensitive natural names.

    Args:
        entries: Unsorted rows. The list is sorted in place.

    Returns:
        The same list, ordered.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/pure-function
    """
    # False < True, so ``not is_dir`` puts directories in front.
    entries.sort(key=lambda entry: (not entry.is_dir, _natural_key(entry.name)))
    return entries


def _close_enumerator(enumerator) -> None:
    """Close an enumerator, ignoring a second close or a failed backend.

    @tags: #scope/file-ops #action/clear #type/helper #side-effect/filesystem
    """
    if enumerator is None:
        return
    try:
        enumerator.close(None)
    except GLib.Error:
        pass


def _load_was_cancelled(error: Optional[GLib.Error], cancellable) -> bool:
    """Return whether the listing stopped because the caller cancelled it.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/pure-function
    """
    if cancellable is not None and cancellable.is_cancelled():
        return True
    return bool(
        error is not None and error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
    )


def get_file_metadata(path: str) -> dict:
    """Describe one file for the preview column.

    ``preview_kind`` is ``image``, ``text``, or ``icon``. Text previews are
    the first ``_PREVIEW_BYTE_LIMIT`` bytes, cut at ``_PREVIEW_LINE_LIMIT``
    lines. Image and icon previews leave ``preview_text`` empty so the UI
    does not render file bytes as characters.

    Args:
        path: Local file or directory. ``~`` is expanded.

    Returns:
        A dict whose keys are exactly ``name``, ``path``, ``uri``,
        ``is_dir``, ``size_human``, ``mtime_human``, ``atime_human``,
        ``ctime_human``, ``content_type``, ``mime_description``,
        ``icon_name``, ``permissions``, ``preview_kind``, and
        ``preview_text``. ``ctime_human`` is empty when the filesystem
        has no birth time. ``is_dir`` is a real bool so the dict stays
        JSON-serializable.

    @tags: #scope/file-ops #action/read #scope/widget
    """
    abs_path = os.path.abspath(os.path.expanduser(path))
    gfile = Gio.File.new_for_path(abs_path)
    info = gfile.query_info(_LIST_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
    name = info.get_name() or os.path.basename(abs_path)
    is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
    content_type = _content_type(info, abs_path)
    kind = _preview_kind(name, content_type)
    # Directories match neither image nor text; don't open() them as files.
    if kind == "text" and not is_dir:
        preview_text = _read_preview_text(abs_path)
    else:
        preview_text = ""
    permissions = _permissions_string(_mode_bits(info, abs_path))
    description = ""
    if content_type:
        description = Gio.content_type_get_description(content_type) or ""
    return {
        "name": name,
        "path": abs_path,
        "uri": gfile.get_uri(),
        "is_dir": bool(is_dir),
        "size_human": format_size(_file_size(info, abs_path)),
        "mtime_human": _format_timestamp(_timestamp(info, "time::modified")),
        "atime_human": _format_timestamp(_timestamp(info, "time::access")),
        "ctime_human": _format_timestamp(_timestamp(info, "time::created")),
        "content_type": content_type,
        "mime_description": description,
        "icon_name": _icon_name(is_dir, content_type),
        "permissions": permissions,
        "preview_kind": kind,
        "preview_text": preview_text,
    }



def _natural_key(name: str) -> list:
    """Order ``file2`` before ``file10`` without caring about case.

    ``re.split`` keeps digit runs as their own chunks, and those become
    ints so the comparison is numeric. The split always starts with a
    string (empty when the name starts with a digit), so an int chunk
    never lines up against a str chunk.

    @tags: #scope/file-ops #action/read #type/helper
    """
    key = []
    for chunk in re.split(r"(\d+)", name.casefold()):
        if chunk.isdigit():
            key.append(int(chunk))
        else:
            key.append(chunk)
    return key


def _entry_from_info(directory: Gio.File, info: Gio.FileInfo) -> FileEntry:
    """Build a ``FileEntry`` from Gio directory enumeration info.

    Directories also copy the column face. ``icon_name`` stays
    ``folder-symbolic`` so preview and drag keep the outline.

    @tags: #scope/file-ops #action/read #type/helper
    """
    name = info.get_name() or ""
    child = directory.get_child(name)
    child_path = child.get_path() or ""
    if child_path:
        child_path = os.path.abspath(child_path)
    is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
    content_type = _content_type(info, child_path or name)
    icon_file = ""
    folder_icon_names: tuple[str, ...] = ()
    emblems: tuple[str, ...] = ()
    if is_dir:
        icon_file, folder_icon_names, emblems = _directory_face(info)
    return FileEntry(
        name=name,
        path=child_path,
        uri=child.get_uri(),
        is_dir=is_dir,
        size=_file_size(info, child_path),
        mtime=_timestamp(info, "time::modified"),
        atime=_timestamp(info, "time::access"),
        content_type=content_type,
        icon_name=_icon_name(is_dir, content_type),
        permissions_str=_permissions_string(_mode_bits(info, child_path)),
        is_hidden=name.startswith("."),
        icon_file=icon_file,
        folder_icon_names=folder_icon_names,
        emblems=emblems,
    )


def _directory_face(info: Gio.FileInfo) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Color file, theme names, and emblems for one directory row.

    Kept off ``icon_name`` because preview and drag still want
    ``folder-symbolic``. ``standard::icon`` does not include the color
    Nemo stores in ``metadata::custom-icon``.

    A custom icon is kept only as a local ``file://`` path that exists.
    When that file is set, theme names stay empty so the row does not
    also paint a generic folder under the color.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    icon_file = _custom_icon_path(info)
    if icon_file:
        names: tuple[str, ...] = ()
    else:
        custom_name = _attribute_string(info, "metadata::custom-icon-name")
        names = (custom_name,) if custom_name else _plain_icon_names(info)
    return icon_file, names, _emblem_names(info)


def _custom_icon_path(info: Gio.FileInfo) -> str:
    """Local path for a ``file://`` custom icon that exists on disk.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem #security/path-validation
    """
    raw = _attribute_string(info, "metadata::custom-icon")
    if not raw.startswith("file://"):
        return ""
    local = _local_path(raw)
    if not local or not os.path.isfile(local):
        return ""
    return local


def _plain_icon_names(info: Gio.FileInfo) -> tuple[str, ...]:
    """``standard::icon`` names with the symbolic suffix removed.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/pure-function
    """
    icon = info.get_icon()
    raw_names: list[str] = []
    if isinstance(icon, Gio.ThemedIcon):
        raw_names = list(icon.get_names())
    elif icon is not None and hasattr(icon, "get_names"):
        raw_names = list(icon.get_names())
    names: list[str] = []
    for name in raw_names:
        plain = name[: -len("-symbolic")] if name.endswith("-symbolic") else name
        if plain and plain not in names:
            names.append(plain)
    return tuple(names)


def _emblem_names(info: Gio.FileInfo) -> tuple[str, ...]:
    """Theme names for ``metadata::emblems``.

    Nemo stores either ``emblem-new`` or a short name such as
    ``favorite``. The theme only has the prefixed form.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/pure-function
    """
    if not info.has_attribute("metadata::emblems"):
        return ()
    stored = info.get_attribute_stringv("metadata::emblems") or []
    names: list[str] = []
    for item in stored:
        if not item:
            continue
        names.append(item if item.startswith("emblem-") else f"emblem-{item}")
    return tuple(names)


def _attribute_string(info: Gio.FileInfo, attribute: str) -> str:
    """String attribute, or empty when the enumeration did not return it.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/pure-function
    """
    if not info.has_attribute(attribute):
        return ""
    return info.get_attribute_string(attribute) or ""


def _content_type(info: Gio.FileInfo, path: str) -> str:
    """Resolve MIME type from file info or content sniffing.

    @tags: #scope/file-ops #action/read #type/helper
    """
    content_type = info.get_content_type() if info.has_attribute("standard::content-type") else ""
    if content_type:
        return content_type
    guessed, _uncertain = Gio.content_type_guess(path or None, None)
    return guessed or "application/octet-stream"


def _file_size(info: Gio.FileInfo, path: str) -> int:
    """Return file size from Gio info or local stat fallback.

    @tags: #scope/file-ops #action/read #type/helper
    """
    if info.has_attribute("standard::size"):
        return int(info.get_size())
    if path:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    return 0


def _timestamp(info: Gio.FileInfo, attribute: str) -> float:
    """Read a Gio time attribute as seconds since the epoch.

    @tags: #scope/file-ops #action/read #type/helper
    """
    if not info.has_attribute(attribute):
        return 0.0
    return float(info.get_attribute_uint64(attribute))


def _mode_bits(info: Gio.FileInfo, path: str) -> int:
    """Return unix mode bits from Gio or local stat.

    @tags: #scope/file-ops #action/read #type/helper
    """
    if info.has_attribute("unix::mode"):
        return int(info.get_attribute_uint32("unix::mode"))
    # Some backends omit the attribute. A local stat still has a mode the
    # preview can show; if that fails too, the string is "--------- (0)".
    if path:
        try:
            return os.stat(path).st_mode
        except OSError:
            return 0
    return 0


def _permissions_string(mode: int) -> str:
    """Format mode bits as symbolic permissions plus octal.

    @tags: #scope/file-ops #action/read #type/helper
    """
    bits = mode & 0o777
    characters = []
    for shift in (6, 3, 0):
        value = (bits >> shift) & 0o7
        characters.append("r" if value & 0o4 else "-")
        characters.append("w" if value & 0o2 else "-")
        characters.append("x" if value & 0o1 else "-")
    return f"{''.join(characters)} ({format(bits, 'o')})"


def _icon_name(is_dir: bool, content_type: str) -> str:
    """Pick a themed icon name for a directory or file MIME type.

    @tags: #scope/file-ops #action/read #type/helper
    """
    if is_dir:
        return "folder-symbolic"
    if not content_type:
        return "text-x-generic-symbolic"
    icon = Gio.content_type_get_icon(content_type)
    names = []
    if isinstance(icon, Gio.ThemedIcon):
        names = list(icon.get_names())
    elif icon is not None and hasattr(icon, "get_names"):
        names = list(icon.get_names())
    if names:
        return names[0]
    return "text-x-generic-symbolic"


def _preview_kind(name: str, content_type: str) -> str:
    """Classify preview rendering as image, text, or icon.

    @tags: #scope/file-ops #action/read #scope/widget
    """
    suffix = os.path.splitext(name)[1].casefold()
    ctype = (content_type or "").casefold()
    # Image first: an ``image/*`` type or a known picture suffix must not
    # fall through into the text preview.
    if ctype.startswith("image/") or suffix in _IMAGE_SUFFIXES:
        return "image"
    # text/* plus structured types Gio does not mark as text, plus suffixes
    # we still want readable when the sniff comes back generic.
    if ctype.startswith("text/") or ctype in _TEXT_CONTENT_TYPES or suffix in _TEXT_SUFFIXES:
        return "text"
    return "icon"


def _read_preview_text(path: str) -> str:
    """Read a bounded UTF-8 text snippet for the preview pane.

    @tags: #scope/file-ops #action/read #scope/widget
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_PREVIEW_BYTE_LIMIT)
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    return "".join(text.splitlines(keepends=True)[:_PREVIEW_LINE_LIMIT])


def format_size(size: int) -> str:
    """Format a byte count in 1024-based units with one decimal past 1023.

    2048 is ``2.0 KB`` and 1536 is ``1.5 KB``. Values under 1024 stay in
    bytes (``512 B``) so small files are not shown as ``0.5 KB``.

    @tags: #scope/file-ops #action/read #type/helper
    """
    if size < 0:
        size = 0
    if size < 1024:
        return f"{size} B"
    value = float(size)
    unit = "KB"
    for unit in ("KB", "MB", "GB"):
        value /= 1024.0
        # Stop at GB even when the number is huge; the UI has no terabyte token.
        if value < 1024.0 or unit == "GB":
            break
    return f"{value:.1f} {unit}"


def _format_timestamp(timestamp: float) -> str:
    """Format epoch seconds for preview metadata, or empty when unknown.

    @tags: #scope/file-ops #action/read #type/helper
    """
    # 0 means the attribute was missing. Showing the epoch would look like a
    # real modification time from 1970.
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")


def _local_path(path_or_uri: str) -> Optional[str]:
    """Return an absolute local path, or None for a non-local URI.

    ``file://`` goes through Gio so percent-encoding is decoded the same
    way the file manager decodes it. Other strings are local paths; ``~``
    is expanded. ``os.path.abspath`` also drops a trailing slash.

    @tags: #scope/navigation #action/read #type/helper
    """
    if "://" in path_or_uri:
        local = Gio.File.new_for_uri(path_or_uri).get_path()
        if not local:
            return None
        return os.path.abspath(local)
    return os.path.abspath(os.path.expanduser(path_or_uri))


def _gio_file(path_or_uri: str) -> Gio.File:
    """Build a ``Gio.File`` from a local path or URI without corrupting URIs.

    @tags: #scope/navigation #action/read #type/helper
    """
    # A URI must not go through expanduser: ``file://`` would become a
    # nonsense relative path and the clipboard would publish the wrong target.
    if "://" in path_or_uri:
        return Gio.File.new_for_uri(path_or_uri)
    return Gio.File.new_for_path(os.path.abspath(os.path.expanduser(path_or_uri)))
