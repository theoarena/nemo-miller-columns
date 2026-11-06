"""Miller view preference for a folder.

The JSON file is what tests and headless runs can point at a temp path,
and the Gio metadata attribute is what other processes can see on the
folder. Both are our own key. The file manager's built-in view setting
is never written.

Reads reuse a module-level Gio.Settings and a JSON store cache keyed by
file signature so navigating folders does not reopen the same file on
every resolve. ``_reset_caches`` clears both for tests.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from miller.listing import _local_path


# Our folder-view attribute. Distinct from the file manager's own view
# metadata so enabling columns does not overwrite how Nemo opens the folder.
# @tags: #type/constant #scope/view-preference #side-effect/filesystem
METADATA_KEY: str = "metadata::nemo-miller-columns-view"

# Set on a directory that already hosts another view. Miller reads it
# and does not pack columns or store a view choice there. The writer
# lives outside this package.
# @tags: #type/constant #scope/view-preference #scope/navigation #side-effect/filesystem
RESERVED_VIEW_KEY: str = "metadata::nemo-reserved-view"

# JSON map of absolute path -> bool. ``store_path`` overrides this so tests
# never touch the user's real config.
# @tags: #type/constant #scope/view-preference #side-effect/filesystem
DEFAULT_STORE_PATH: str = os.path.abspath(
    os.path.expanduser("~/.config/nemo-miller-columns/folder_views.json")
)

# JSON store cache: path -> (st_ino, st_mtime_ns, st_size, store dict).
# Invalidated on write (os.replace changes inode) or when the file signature
# changes under us. Missing/corrupt files are not cached.
# @tags: #type/constant #scope/view-preference #scope/shared #side-effect/filesystem
_STORE_CACHE: dict[str, tuple[int, int, int, dict]] = {}

# Lazily created Gio.Settings for org.nemo.preferences. Creation failures
# are not cached so a later successful open can still work.
# @tags: #type/constant #scope/view-preference #scope/shared #side-effect/gsettings
_PREFERENCE_SETTINGS = None


def _reset_caches() -> None:
    """Drop preference caches so tests can isolate Settings and JSON reads.

    @tags: #action/clear #scope/view-preference #scope/shared #type/helper
        #lifecycle/destroy #side-effect/gsettings #side-effect/filesystem
    """
    global _PREFERENCE_SETTINGS
    _STORE_CACHE.clear()
    _PREFERENCE_SETTINGS = None


def is_reserved_location(uri_or_path: str) -> bool:
    """Return whether ``uri_or_path`` is marked as a reserved location.

    A reserved directory is still a real directory. It is not a column
    host: another view already owns it, and storing a Miller preference
    there would write a choice the user cannot see. The mark is
    ``RESERVED_VIEW_KEY`` set to the exact string ``true``. A child has
    no attribute of its own, so it stays a normal folder. A missing
    attribute or ``GLib.Error`` is not reserved.

    Args:
        uri_or_path: Local path or ``file://`` URI. A trailing slash is
            the same directory.

    Returns:
        True only when the attribute is exactly ``true``.

    @tags: #type/helper #scope/navigation #scope/view-preference #action/read #side-effect/filesystem
    """
    local = _local_path(uri_or_path)
    if local is None:
        return False
    return _read_view_attribute(local, RESERVED_VIEW_KEY) == "true"


def resolve_miller_view_preferred(
    path_or_uri: str,
    inherited_state: Optional[bool] = None,
    store_path: Optional[str] = None,
    ignore_metadata: Optional[bool] = None,
    inherit_viewer: Optional[bool] = None,
) -> bool:
    """Decide whether ``path_or_uri`` should open in Miller columns.

    Explicit values come from the metadata attribute when it is exactly
    ``true`` or ``false``, otherwise from the JSON store. The nearest
    explicit folder wins: a child set to false does not inherit a parent
    set to true.

    When ``ignore_metadata`` is true the folder record is skipped and
    ``inherited_state`` is returned (``None`` counts as false). When the
    folder has no record and ``inherit_viewer`` is false, the answer is
    false. When inheritance is on, parents are walked with
    ``os.path.dirname`` until an explicit value appears or the filesystem
    root is reached (``dirname(path) == path``).

    ``ignore_metadata`` and ``inherit_viewer`` are read from
    ``org.nemo.preferences`` only when the argument is ``None``. A missing
    schema treats that flag as false. This function never writes settings.

    Args:
        path_or_uri: Folder path or URI.
        inherited_state: View already chosen above this folder.
        store_path: JSON file to read. Defaults to ``DEFAULT_STORE_PATH``.
        ignore_metadata: When true, skip the folder record.
        inherit_viewer: When true, walk parents for a record.

    Returns:
        Whether the Miller view is preferred.

    @tags: #scope/view-preference #action/read #side-effect/gsettings
    """
    import miller.data as data

    ignore, inherit = data._preference_flags(ignore_metadata, inherit_viewer)
    # The file manager's "ignore view metadata" switch applies here too.
    # None means nobody above us chose a view, which is off, not on.
    if ignore:
        return bool(inherited_state)

    path = _local_path(path_or_uri)
    if path is None:
        return False

    store = _load_store(store_path)
    # Check this folder before any parent so the closest explicit value wins.
    explicit = _explicit_preference(path, store)
    if explicit is not None:
        return explicit

    # No record here. Without inheritance a parent preference must not leak.
    if not inherit:
        return False

    current = path
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        explicit = _explicit_preference(current, store)
        if explicit is not None:
            return explicit
    return bool(inherited_state)


def set_miller_view_preferred(
    path_or_uri: str,
    enabled: bool,
    store_path: Optional[str] = None,
) -> None:
    """Remember whether ``path_or_uri`` prefers Miller columns.

    Always updates the JSON store. Also tries to set ``METADATA_KEY`` on
    the folder so other processes can see the choice. A GVFS failure is
    ignored because the JSON record is enough for the next resolve.

    Args:
        path_or_uri: Folder path or ``file://`` URI. Must be a local path.
        enabled: True to prefer columns.
        store_path: JSON file to write. Defaults to ``DEFAULT_STORE_PATH``.

    Raises:
        ValueError: ``path_or_uri`` is not a local path, so it cannot be a
            JSON key.

    @tags: #scope/view-preference #action/write #side-effect/filesystem
    """
    path = _local_path(path_or_uri)
    if path is None:
        raise ValueError(f"Cannot store a view preference for non-local path: {path_or_uri}")
    store = _load_store(store_path)
    store[path] = bool(enabled)
    _write_store(store, store_path)
    try:
        Gio.File.new_for_path(path).set_attribute_string(
            METADATA_KEY,
            "true" if enabled else "false",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
    except Exception:
        # Metadata daemons are optional. The JSON write above already stuck.
        return



def _preference_flags(
    ignore_metadata: Optional[bool],
    inherit_viewer: Optional[bool],
) -> tuple[bool, bool]:
    """Fill flags that the caller left as None.

    Reads ``org.nemo.preferences`` and never writes it. The Settings
    object is created once and reused; ``get_boolean`` still sees live
    values. Tests pass both flags, so they do not depend on the user
    schema. If Settings is unavailable, a missing flag is false.

    @tags: #scope/view-preference #scope/shared #action/read #type/helper
        #side-effect/gsettings #lifecycle/init
    """
    global _PREFERENCE_SETTINGS
    if ignore_metadata is not None and inherit_viewer is not None:
        return bool(ignore_metadata), bool(inherit_viewer)

    loaded_ignore = False
    loaded_inherit = False
    try:
        settings = _PREFERENCE_SETTINGS
        if settings is None:
            settings = Gio.Settings.new("org.nemo.preferences")
            _PREFERENCE_SETTINGS = settings
        loaded_ignore = bool(settings.get_boolean("ignore-view-metadata"))
        loaded_inherit = bool(settings.get_boolean("inherit-folder-viewer"))
    except Exception:
        # Do not cache a failed open: the schema may appear later.
        loaded_ignore = False
        loaded_inherit = False

    if ignore_metadata is None:
        ignore_metadata = loaded_ignore
    if inherit_viewer is None:
        inherit_viewer = loaded_inherit
    return bool(ignore_metadata), bool(inherit_viewer)


def _explicit_preference(path: str, store: dict) -> Optional[bool]:
    """Read explicit Miller view preference for one folder path.

    @tags: #scope/view-preference #action/read
    """
    attribute = _read_view_attribute(path)
    # Only the tokens we write are explicit. Anything else falls through to
    # JSON so a foreign attribute cannot block parent inheritance.
    if attribute == "true":
        return True
    if attribute == "false":
        return False
    value = store.get(path)
    if isinstance(value, bool):
        return value
    return None


def _read_view_attribute(path: str, key: str = METADATA_KEY) -> Optional[str]:
    """Read one folder attribute.

    ``key`` defaults to ``METADATA_KEY``. A missing attribute or
    ``GLib.Error`` means the folder has no explicit value. Callers that
    only treat ``true`` and ``false`` as choices ignore every other string.

    @tags: #scope/view-preference #action/read #side-effect/filesystem
    """
    try:
        info = Gio.File.new_for_path(path).query_info(
            key,
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
    except GLib.Error:
        return None
    if not info.has_attribute(key):
        return None
    return info.get_attribute_string(key)


def _resolve_store_path(store_path: Optional[str]) -> str:
    """Return the JSON store path, defaulting to ``DEFAULT_STORE_PATH``.

    @tags: #scope/view-preference #type/helper
    """
    if store_path:
        return store_path
    import miller.data as data
    return data.DEFAULT_STORE_PATH


def _load_store(store_path: Optional[str]) -> dict:
    """Load the folder view preference JSON store.

    Caches by file signature (inode, mtime_ns, size). Callers get a shallow
    copy so mutating the returned dict cannot poison the cache. Missing or
    invalid JSON returns ``{}`` and is not cached.

    @tags: #scope/view-preference #scope/shared #action/read #type/helper
        #side-effect/filesystem
    """
    path = _resolve_store_path(store_path)
    try:
        st = os.stat(path)
    except OSError:
        return {}
    signature = (st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _STORE_CACHE.get(path)
    if cached is not None and cached[:3] == signature:
        return dict(cached[3])
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        _STORE_CACHE.pop(path, None)
        return {}
    if not isinstance(raw, dict):
        _STORE_CACHE.pop(path, None)
        return {}
    store = {str(key): value for key, value in raw.items() if isinstance(value, bool)}
    _STORE_CACHE[path] = (*signature, store)
    return dict(store)


def _write_store(data: dict, store_path: Optional[str]) -> None:
    """Atomically persist the folder view preference JSON store.

    Drops the cache entry for ``path`` because ``os.replace`` installs a
    new inode that would otherwise keep serving the pre-write dict until
    the next stat mismatch.

    @tags: #scope/view-preference #action/clear #action/write #side-effect/filesystem
    """
    path = _resolve_store_path(store_path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    # Replace in one step so a crash cannot leave a half-written JSON file
    # that the next resolve would treat as "no preferences".
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
    _STORE_CACHE.pop(path, None)
