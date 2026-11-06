"""One directory column in the Miller chain.

The column lists a single folder and reports selection upward. It does not
open sibling columns: the board owns the chain. Hidden files are a local
toggle because the host (not this widget) writes the Nemo preference.

Rows, selection, and the directory monitor live in ``column_list``.
Keyboard and pointer handling live in ``column_input``. The context menu
and inline rename live in ``column_menu``.

The list selects several rows. A directory monitor reloads the column after
Nemo finishes a drop, because that work returns before the files move.
Paste into exactly one selected folder targets that directory; otherwise the
paste goes to this column's directory. The column that lists the destination
reloads and selects the pasted basename when the row exists, or keeps
``_pending_paste_name`` until the directory monitor reload catches up.
The first listing is asynchronous so opening a column never blocks the GTK
main loop on large directories, and the column can cancel in-flight work on
destroy. Later reloads stay synchronous so delete, paste, and rename still see
the new rows before those methods return.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

# ``copy_to_clipboard``, ``open_file_default`` and ``show_item_properties``
# stay bound here: UI tests replace them, and the mixins read this module
# back at call time.
from miller.data import (
    FileEntry,
    copy_to_clipboard,
    create_subfolder,
    open_file_default,
    paste_from_clipboard,
    show_item_properties,
    trash_entry,
)
from miller.i18n import _
from miller.ui.column_input import ColumnInputMixin
from miller.ui.column_list import ColumnListMixin
from miller.ui.column_menu import BACKGROUND_MENU_LABELS, CONTEXT_MENU_LABELS, ColumnMenuMixin

# Background menu labels are re-exported for tests and host integrations.
# @tags: #type/constant #scope/widget
__all__ = ["BACKGROUND_MENU_LABELS", "CONTEXT_MENU_LABELS", "MillerColumn"]

# About one icon column of a file manager. The stylesheet floor is 220;
# the request keeps a new column from opening at the label's natural width.
_COLUMN_WIDTH = 240


class MillerColumn(Gtk.Box, ColumnListMixin, ColumnInputMixin, ColumnMenuMixin):
    """A single folder: header, scrolling list, keyboard and context menu.

    Selection is reported through ``on_select`` and is not debounced here.
    The board coalesces those calls. ``show_hidden`` never writes GSettings.

    Attributes:
        directory_path: Absolute path of the folder this column lists.
        open_in_new_tab: Host callback, or None when the menu item is inert.
        open_in_new_window: Host callback, or None when the menu item is inert.
        show_hidden: Whether dotfiles are currently listed.
        file_click_policy: Hook returning ``"single"`` or ``"double"``.
            ``None`` matches ``"double"``: a file click selects and previews,
            and the second click opens. ``"single"`` opens the file on the
            first release. Folders always open a column on one click.
        _space_passthrough: True only while a leading Space is still being
            dispatched; suppresses ListBox row activation so the key can
            reach the window for nemo-preview.

    @tags: #scope/widget #scope/view-preference #scope/navigation #scope/file-ops #type/widget #action/bind
    """


    def __init__(
        self,
        directory_path: str,
        on_select: Callable[[MillerColumn, Optional[FileEntry]], None],
        on_activate_dir: Callable[[MillerColumn, FileEntry], None],
        on_nav_left: Callable[[MillerColumn], None],
        on_nav_right: Callable[[MillerColumn, FileEntry], None],
        show_hidden: bool = False,
        ) -> None:
        """Build header, list, keys, and directory monitor for one folder.

        @tags: #lifecycle/init #scope/widget #scope/navigation #action/bind #scope/file-ops #type/widget #complexity/high
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.directory_path = os.path.abspath(os.path.expanduser(directory_path))
        self.on_select = on_select
        self.on_activate_dir = on_activate_dir
        self.on_nav_left = on_nav_left
        self.on_nav_right = on_nav_right
        self.show_hidden = bool(show_hidden)
        self.open_in_new_tab: Optional[Callable[[str], None]] = None
        self.open_in_new_window: Optional[Callable[[str], None]] = None
        self.file_click_policy: Optional[Callable[[], str]] = None

        self._rebuilding = False
        self._dead = False
        self._suppress_select = False
        # Gtk.ListBox selects the first row when it gains focus. That is not
        # a user choice, so those signals are dropped unless this is set
        # around our own select_row call.
        self._selection_from_user = False
        # True only while Up/Down is selecting a row. The board waits before
        # listing that directory; a click leaves this clear and opens at once.
        self._keyboard_select = False
        # 1 for a plain click, 2+ once a multi-press has started. The file
        # policy opens on the first release only.
        self._primary_presses = 0
        self._rename_guard = False
        self._context_menu: Optional[Gtk.Menu] = None
        self._press_row: Optional[Gtk.ListBoxRow] = None
        self._anchor_row: Optional[Gtk.ListBoxRow] = None
        self._monitor = None
        self._monitor_changed_id = 0
        self._monitor_source = 0
        self._monitor_deferred = False
        # Typed prefix for this column only. Empty lists the whole directory.
        self._search_query = ""
        # True only while a leading Space is still being dispatched. The
        # ListBox maps that key to row activation; the guard reads this.
        self._space_passthrough = False
        # Basename to select once a paste Nemo has not finished yet shows up.
        self._pending_paste_name: str | None = None

        self.get_style_context().add_class("mc-column")
        self.set_size_request(_COLUMN_WIDTH, -1)
        self.set_hexpand(False)
        self.set_vexpand(True)
        self.set_valign(Gtk.Align.FILL)

        self._build_header()
        self._build_list()
        self.connect("key-press-event", self._on_key_press)
        self.connect("destroy", self._on_destroy)
        # The first listing must not block the GTK thread. Destroy cancels it.
        self._load_cancellable = None
        self._loading = False
        self._after_load: list[Callable[[], None]] = []
        self._load_idle = 0
        self._load_generation = 0
        self._pending_entries: list[FileEntry] = []
        self._chunk_index = 0
        self._load_async()
        self._start_directory_monitor()

    def delete_selected(self) -> None:
        """Move every selected entry to the trash and reload.

        A false return from ``trash_entry`` leaves that row in place. The
        file is not unlinked: failing the trash must not become a delete.
        When every trash fails, the list is left as it was.

        @tags: #scope/file-ops #action/write #scope/widget
        """
        entries = self.get_selected_entries()
        if not entries:
            return
        # Every entry is trashed. ``any()`` would stop after the first success.
        trashed = False
        for entry in entries:
            if trash_entry(entry.path):
                trashed = True
        if not trashed:
            return
        self.refresh()
        self.on_select(self, None)

    def create_folder(self, name: str | None = None) -> str:
        """Create a subfolder, select it, and start an inline rename.

        When ``name`` is omitted, the catalog supplies ``New Folder``.
        When ``name`` is taken, the next free ``"{name} 2"``, ``"{name} 3"``,
        ... is used. The returned path is the directory that was created,
        which may differ from ``name`` after that disambiguation.

        Args:
            name: Preferred basename. None uses the translated default.

        Returns:
            Absolute path of the new directory.

        Raises:
            ValueError: ``name`` is not a single path component.
            GLib.Error: The backend rejected the create.
    
        @tags: #scope/file-ops #scope/navigation #action/write #action/clear #scope/widget
        """
        # The new row has to be on screen for the inline rename that follows.
        self._set_search_query("")
        chosen = self._unique_folder_name(_("New Folder") if name is None else name)
        created = create_subfolder(self.directory_path, chosen)
        self.refresh()
        self.select_name(os.path.basename(created))
        self.begin_rename()
        return created

    def copy_selected(self, cut: bool = False) -> None:
        """Put every selected path on the clipboard.

        Args:
            cut: True to advertise a cut instead of a copy.

        @tags: #scope/file-ops #action/write #side-effect/clipboard #scope/widget
        """
        entries = self.get_selected_entries()
        if not entries:
            return
        copy_to_clipboard([entry.path for entry in entries], cut=cut)

    def paste_clipboard(self) -> None:
        """Paste into the target directory, reload, and select the new name.

        A single selected folder receives the files (Paste Into Folder).
        A file, several rows, or an empty selection uses this column.
        The column that lists the target selects the last pasted name
        when the row exists, or on the next directory reload.

        @tags: #scope/file-ops #scope/navigation #scope/widget #action/read #action/write #action/bind #side-effect/clipboard #side-effect/filesystem #type/helper #complexity/high
        """
        dest = self._paste_directory()
        try:
            pasted = paste_from_clipboard(dest)
        except Exception as error:
            # ``paste_from_clipboard`` should not raise; this is a last-resort guard.
            print(f"[miller] Failed to paste: {error}")
            return
        column = self._column_listing(dest)
        if column is None:
            return
        column._select_pasted(pasted)

    def _paste_directory(self) -> str:
        """Return the directory that should receive a paste.

        One selected folder is the destination. Anything else pastes
        into the folder this column is listing.

        @tags: #scope/file-ops #scope/navigation #scope/widget #action/read #side-effect/clipboard #type/helper
        """
        entries = self.get_selected_entries()
        # A file, a multi-selection, or no row has no folder to paste into.
        if len(entries) == 1 and entries[0].is_dir:
            return entries[0].path
        return self.directory_path

    def _column_listing(self, directory: str):
        """Return the open column that lists ``directory``, if any.

        @tags: #scope/file-ops #scope/navigation #scope/widget #action/find #side-effect/clipboard #type/helper
        """
        target = os.path.abspath(directory)
        if os.path.abspath(self.directory_path) == target:
            return self
        from miller.ui.board import MillerColumnsView

        widget = self.get_parent()
        while widget is not None and not isinstance(widget, MillerColumnsView):
            widget = widget.get_parent()
        if widget is None:
            return None
        for column in widget.columns:
            if os.path.abspath(column.directory_path) == target:
                return column
        return None

    def _select_pasted(self, pasted: list[str]) -> None:
        """Reload and select the last pasted basename.

        Nemo creates the file after this call returns. The name is kept
        until a directory reload sees the row.

        @tags: #scope/file-ops #scope/navigation #scope/widget #action/read #action/bind #action/render #side-effect/filesystem #side-effect/clipboard #type/helper
        """
        self.refresh()
        if not pasted:
            return
        name = os.path.basename(pasted[-1])
        row = self._row_named(name)
        if row is not None and self._name_matches(name):
            self._pending_paste_name = None
            self.select_name(name)
            return
        self._pending_paste_name = name

    def _unique_folder_name(self, name: str) -> str:
        """_unique_folder_name.

        @tags: #scope/file-ops #action/read #type/helper
        """
        candidate = name
        number = 2
        while os.path.exists(os.path.join(self.directory_path, candidate)):
            candidate = f"{name} {number}"
            number += 1
            if number > 10000:
                break
        return candidate
