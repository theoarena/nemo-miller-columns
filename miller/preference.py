"""Miller view preference for a folder.

The JSON file is what tests and headless runs can point at a temp path,
and the Gio metadata attribute is what other processes can see on the
folder. Both are our own key. The file manager's built-in view setting
is never written.
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

# Home panel treats this directory as a special location. Compared as a
# real path so a URI and a trailing slash still match.
# @tags: #type/constant #scope/navigation
DASHBOARD_PATH: str = os.path.abspath(os.path.expanduser("~/.dashboard"))

# JSON map of absolute path -> bool. ``store_path`` overrides this so tests
# never touch the user's real config.
# @tags: #type/constant #scope/view-preference #side-effect/filesystem
DEFAULT_STORE_PATH: str = os.path.abspath(
    os.path.expanduser("~/.config/nemo-miller-columns/folder_views.json")
)



def is_dashboard_uri(uri_or_path: str) -> bool:
    """Return whether ``uri_or_path`` is the Home dashboard directory.

    Accepts a local path, a ``file://`` URI, or a trailing slash. A child
    such as ``~/.dashboard/extra`` is not the dashboard itself.

    Args:
        uri_or_path: Path or URI to test.

    Returns:
        True only for the dashboard directory.

    @tags: #scope/navigation #action/read
    """
    local = _local_path(uri_or_path)
    if local is None:
        return False
    return local == DASHBOARD_PATH


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

    Reads ``org.nemo.preferences`` and never writes it. Tests pass both
    flags, so they do not depend on the user schema. If Settings is
    unavailable, a missing flag is false.

    @tags: #scope/view-preference #action/read #side-effect/gsettings
    """
    if ignore_metadata is not None and inherit_viewer is not None:
        return bool(ignore_metadata), bool(inherit_viewer)

    loaded_ignore = False
    loaded_inherit = False
    try:
        settings = Gio.Settings.new("org.nemo.preferences")
        loaded_ignore = bool(settings.get_boolean("ignore-view-metadata"))
        loaded_inherit = bool(settings.get_boolean("inherit-folder-viewer"))
    except Exception:
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


def _read_view_attribute(path: str) -> Optional[str]:
    """Read ``METADATA_KEY`` from folder extended attributes.

    @tags: #scope/view-preference #action/read #side-effect/filesystem
    """
    try:
        info = Gio.File.new_for_path(path).query_info(
            METADATA_KEY,
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
    except GLib.Error:
        return None
    if not info.has_attribute(METADATA_KEY):
        return None
    return info.get_attribute_string(METADATA_KEY)


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

    @tags: #scope/view-preference #action/read #side-effect/filesystem
    """
    path = _resolve_store_path(store_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, bool)}


def _write_store(data: dict, store_path: Optional[str]) -> None:
    """Atomically persist the folder view preference JSON store.

    @tags: #scope/view-preference #action/write #side-effect/filesystem
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
