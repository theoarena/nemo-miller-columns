"""Directory listing, view preference, clipboard, and in-window navigation.

Implementations live in ``miller.listing``, ``miller.preference``,
``miller.clipboard``, ``miller.transfer``, ``miller.session``,
``miller.nemo_actions``, and ``miller.nemo_extensions``. Names
are re-exported here so callers and tests keep a single module object.

The column UI renders what ``list_directory`` and ``get_file_metadata``
return. View preference is stored twice on purpose: the JSON file is what
tests and headless runs can point at a temp path, and the Gio metadata
attribute is what other processes can see on the folder. Both are our own
key. The file manager's built-in view setting is never written.

Folder changes stay inside the current process. Emitting a navigation
signal on idle avoids re-entering Nemo's view switch from a click handler,
and it avoids starting another file-manager process (that path opens a
second window and has crashed Nemo 6 on Wayland).

Drag-and-drop copies and moves go through ``org.Nemo.FileOperations`` on
the session bus. Properties use ``org.freedesktop.FileManager1``. Both
calls are asynchronous: a synchronous call from inside Nemo's main loop
deadlocks on Nemo's own bus name.

``show_file_preview`` re-export drives Space-driven nemo-preview from the
extension and column pass-through path.

@tags: #scope/shared #scope/file-ops #action/read #type/extension
"""

from miller.clipboard import (
    copy_to_clipboard,
    format_gnome_copied_files,
    format_uri_list,
    parse_gnome_copied_files,
    paste_from_clipboard,
)
from miller.listing import (
    FileEntry,
    format_size,
    get_file_metadata,
    list_directory,
)
from miller.preference import (
    DASHBOARD_PATH,
    DEFAULT_STORE_PATH,
    METADATA_KEY,
    _preference_flags,
    is_dashboard_uri,
    resolve_miller_view_preferred,
    set_miller_view_preferred,
)
from miller.session import (
    launch_with_app,
    open_file_default,
    open_in_same_window,
    recommended_apps,
    show_file_preview,
    show_item_properties,
)
from miller.nemo_actions import (
    MenuTarget,
    NemoAction,
    ScriptNode,
    action_visible,
    expand,
    load_actions,
    load_scripts,
    run_action,
    run_script,
)
from miller.nemo_extensions import extension_items
from miller.transfer import (
    _call_nemo_file_operations,
    _name_has_owner,
    _nemo_bus_available,
    _unique_link,
    choose_drop_action,
    create_subfolder,
    filter_drop_uris,
    rename_entry,
    same_filesystem,
    transfer_uris,
    trash_entry,
)

__all__ = [
    "DASHBOARD_PATH",
    "DEFAULT_STORE_PATH",
    "METADATA_KEY",
    "FileEntry",
    "MenuTarget",
    "NemoAction",
    "ScriptNode",
    "action_visible",
    "expand",
    "extension_items",
    "load_actions",
    "load_scripts",
    "run_action",
    "run_script",
    "choose_drop_action",
    "copy_to_clipboard",
    "create_subfolder",
    "filter_drop_uris",
    "format_gnome_copied_files",
    "format_size",
    "format_uri_list",
    "get_file_metadata",
    "is_dashboard_uri",
    "launch_with_app",
    "list_directory",
    "open_file_default",
    "open_in_same_window",
    "parse_gnome_copied_files",
    "paste_from_clipboard",
    "recommended_apps",
    "rename_entry",
    "same_filesystem",
    "show_file_preview",
    "show_item_properties",
    "transfer_uris",
    "resolve_miller_view_preferred",
    "set_miller_view_preferred",
    "trash_entry",
]
