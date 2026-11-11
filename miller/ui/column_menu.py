"""Context menu and inline rename for one Miller column.

Mixed into ``MillerColumn``. Menu labels stay importable from
``miller.ui.column``.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from miller.data import FileEntry, MenuTarget, rename_entry
from miller.i18n import _
from miller.ui.nemo_items import action_items, extension_menu_items, scripts_menu_item
from miller.ui.open_with import open_with_menu_item


def _rename_selection_end(entry: FileEntry) -> int:
    """Return the character end index to pre-select when renaming ``entry``.

    Directories, extensionless files, and leading-dot files without a second
    extension return ``-1`` so ``Gtk.Editable.select_region(0, -1)`` selects
    the entire name. Regular files with an extension return ``len(stem)`` so
    only the filename stem is selected.

    @tags: #scope/file-ops #scope/widget #action/read #type/helper #side-effect/pure-function
    """
    if entry.is_dir:
        return -1
    stem, ext = os.path.splitext(entry.name)
    if stem and ext:
        return len(stem)
    return -1


def _column_module():
    """Return the column module after import, so test rebinds are visible.

    ``show_item_properties`` is replaced on ``miller.ui.column`` by the UI
    tests. Reading it here, not from ``miller.data``, keeps that patch.

    @tags: #type/helper #action/read
    """
    import miller.ui.column as column_module

    return column_module

# English msgids, in menu order. Handler maps keep these strings;
# ``_()`` runs when the MenuItem is created. The ellipsis is the single
# typographic character so the item does not wrap.
# @tags: #type/constant #scope/widget
CONTEXT_MENU_LABELS = (
    "Open",
    "Open With",
    "Open in New Tab",
    "Open in New Window",
    "New Folder",
    "Cut",
    "Copy",
    "Paste",
    "Rename\u2026",
    "Move to Trash",
    "Copy Path",
    "Properties",
)

# Blank-area menu. Dynamic actions/scripts/extensions sit between Paste
# and Properties when that section is non-empty.
# @tags: #type/constant #scope/widget
BACKGROUND_MENU_LABELS = ("New Folder", "Paste", "Properties")

# These need exactly one row. A multi-selection has no single path to rename,
# copy, or open in a tab. Properties accepts every selected path.
# @tags: #type/constant #scope/widget #scope/file-ops
_SINGLE_SELECTION_LABELS = frozenset({
    "Rename\u2026",
    "Copy Path",
    "Open in New Tab",
    "Open in New Window",
})


class ColumnMenuMixin:
    """Right-click menu and the inline rename entry."""

    def begin_rename(self) -> None:
        """Replace the selected name with an entry prefilled with that name.

        Enter commits through ``commit_rename``. Escape restores the label.
        No selection, or the empty placeholder, does nothing. An active
        search is cleared first so the entry is not sitting on a hidden row.
        Only the filename stem is selected for regular files with an
        extension; folders, dotfiles, and extensionless files select all.

        @tags: #scope/file-ops #scope/navigation #action/render #action/clear #scope/widget
        """
        self._set_search_query("")
        entry = self.get_selected()
        row = self._row_named(entry.name) if entry is not None else None
        if row is None:
            return
        selection_end = _rename_selection_end(entry)
        existing = getattr(row, "rename_widget", None)
        if existing is not None:
            existing.grab_focus()
            existing.select_region(0, selection_end)
            self._schedule_rename_focus_restore(row, existing, selection_end)
            return

        label = row.name_label
        rename_entry = Gtk.Entry()
        rename_entry.set_has_frame(False)
        rename_entry.get_style_context().add_class("mc-rename-entry")
        rename_entry.set_text(entry.name)
        rename_entry.set_hexpand(True)
        box = row.get_child()
        box.remove(label)
        box.pack_start(rename_entry, True, True, 0)
        box.reorder_child(rename_entry, 1)
        row.rename_widget = rename_entry
        rename_entry.connect("activate", self._on_rename_activate)
        rename_entry.connect("key-press-event", self._on_rename_key)
        rename_entry.show()
        rename_entry.grab_focus()
        # grab_focus() applies gtk-entry-select-on-focus and selects (0, -1),
        # so select_region must run after it.
        rename_entry.select_region(0, selection_end)
        self._schedule_rename_focus_restore(row, rename_entry, selection_end)

    def _schedule_rename_focus_restore(
        self,
        row: Gtk.ListBoxRow,
        rename_widget: Gtk.Entry,
        selection_end: int,
    ) -> None:
        """Ensure focus and stem selection survive context-menu popdown on idle.

        Activating "Rename…" from a ``Gtk.Menu`` runs before the menu finishes
        popping down; its ungrab/deactivate cycle can move window focus back to
        the row or clear the entry selection.

        @tags: #scope/file-ops #scope/navigation #action/bind #scope/widget #type/helper
        """
        def restore() -> bool:
            if getattr(self, "_dead", False) or getattr(row, "rename_widget", None) is not rename_widget:
                return False
            toplevel = self.get_toplevel()
            if isinstance(toplevel, Gtk.Window) and toplevel.get_focus() is not rename_widget:
                rename_widget.grab_focus()
                rename_widget.select_region(0, selection_end)
            return False

        GLib.idle_add(restore)

    def commit_rename(self, new_name: str) -> None:
        """Rename the selection to ``new_name``, reload, and select it.

        ``ValueError`` (empty name, slash, NUL) leaves the row as it was.
        A backend rejection does the same: the inline entry closes and the
        original name stays.

        Args:
            new_name: New basename, not a path.
    
        @tags: #scope/file-ops #action/write #scope/widget
        """
        if self._rename_guard:
            return
        self._rename_guard = True
        try:
            entry = self.get_selected()
            if entry is None:
                self._end_rename_widget()
                return
            try:
                new_path = rename_entry(entry.path, new_name)
            except (ValueError, GLib.Error):
                self._end_rename_widget()
                return
            self._end_rename_widget()
            self.refresh()
            self.select_name(os.path.basename(new_path))
        finally:
            self._rename_guard = False

    def _rename_active(self) -> bool:
        """Return whether an inline rename entry is on screen.

        @tags: #scope/file-ops #action/read #type/helper
        """
        for row in self.listbox.get_children():
            if getattr(row, "rename_widget", None) is not None:
                return True
        return False

    def _on_rename_activate(self, rename_entry: Gtk.Entry) -> None:
        """_on_rename_activate.

        @tags: #scope/file-ops #action/bind #scope/widget
        """
        self.commit_rename(rename_entry.get_text())

    def _on_rename_key(self, _rename_entry: Gtk.Entry, event: Gdk.EventKey) -> bool:
        """_on_rename_key.

        @tags: #scope/file-ops #action/bind #scope/widget
        """
        if event.keyval == Gdk.KEY_Escape:
            self._end_rename_widget()
            return True
        return False

    def _end_rename_widget(self) -> None:
        """Restore the name label and put focus back on the row if needed.

        Escape, invalid names, and the commit path all destroy the entry.
        Without restoring focus, the next arrow key would miss the column.

        @tags: #scope/file-ops #scope/navigation #action/render #action/bind #scope/widget
        """
        toplevel = self.get_toplevel()
        focused = toplevel.get_focus() if isinstance(toplevel, Gtk.Window) else None
        had_focus = False
        for row in self.listbox.get_children():
            rename_widget = getattr(row, "rename_widget", None)
            if rename_widget is None:
                continue
            if focused is rename_widget:
                had_focus = True
            parent = rename_widget.get_parent()
            if parent is not None:
                parent.remove(rename_widget)
            rename_widget.destroy()
            row.rename_widget = None
            label = getattr(row, "name_label", None)
            box = row.get_child()
            if label is not None and label.get_parent() is None and box is not None:
                box.pack_start(label, True, True, 0)
                box.reorder_child(label, 1)
                label.show()
        if had_focus:
            self.focus_row()
        if self._monitor_deferred and not self._rename_active():
            self._monitor_deferred = False
            self._schedule_directory_reload()

    def _popup_context_menu(self, event: Gdk.EventButton, background: bool = False) -> None:
        """Show the context menu at the pointer for row or background click.

        @tags: #scope/widget #scope/file-ops #action/render
        """
        if self._context_menu is not None:
            self._context_menu.destroy()
        menu = self._build_context_menu(background=background)
        self._context_menu = menu
        menu.show_all()
        menu.popup(None, None, None, None, event.button, event.time)

    def _build_context_menu(self, background: bool = False) -> Gtk.Menu:
        """Build the context menu for the current selection or the folder.

        “Open With” is a submenu built at popup time, so the app list
        matches the files that are selected now. Nemo actions, scripts,
        and extension items are inserted at the frozen anchors.

        Args:
            background: True when the click missed every row.

        Returns:
            A ``Gtk.Menu`` that has not been shown.

        @tags: #scope/widget #scope/file-ops #action/render
        """
        menu = Gtk.Menu()
        target = self._menu_target(background)
        if background:
            self._append_fixed_item(menu, "New Folder", True)
            self._append_fixed_item(menu, "Paste", True)
            dynamic = list(action_items(self, target))
            scripts = scripts_menu_item(self, target)
            if scripts is not None:
                dynamic.append(scripts)
            dynamic.extend(extension_menu_items(self, target, True))
            self._append_section(menu, dynamic)
            self._append_fixed_item(menu, "Properties", True)
            return menu
        for label in CONTEXT_MENU_LABELS:
            if label == "New Folder":
                dynamic = list(action_items(self, target))
                scripts = scripts_menu_item(self, target)
                if scripts is not None:
                    dynamic.append(scripts)
                self._append_section(menu, dynamic)
            if label == "Properties":
                self._append_section(menu, extension_menu_items(self, target, False))
            self._append_fixed_item(menu, label, False)
        return menu

    def _menu_target(self, background: bool) -> MenuTarget:
        """Selection paths, or none when the click was on empty space.

        @tags: #scope/widget #scope/file-ops #action/read #type/helper
        """
        paths = [] if background else [entry.path for entry in self.get_selected_entries()]
        return MenuTarget(paths=paths, parent_dir=self.directory_path)

    def _append_fixed_item(self, menu: Gtk.Menu, label: str, background: bool) -> None:
        """Append one built-in menu row with sensitivity rules applied.

        @tags: #scope/widget #scope/file-ops #action/render #type/helper
        """
        if label == "Open With":
            item = open_with_menu_item(self)
        else:
            item = Gtk.MenuItem(label=_(label))
            item.connect("activate", self._on_menu_activate, label, background)
        item.set_sensitive(self._menu_item_sensitive(label, background))
        menu.append(item)

    def _append_section(self, menu: Gtk.Menu, items: list) -> None:
        """Insert a separator and ``items`` only when the section is non-empty.

        @tags: #scope/widget #action/render #type/helper
        """
        if not items:
            return
        menu.append(Gtk.SeparatorMenuItem())
        for item in items:
            menu.append(item)

    def _menu_item_sensitive(self, label: str, background: bool = False) -> bool:
        """Return whether ``label`` applies to the current selection.

        Rename, copy path, and open-in-tab need one row. Properties
        stays on when one or more rows are selected. “Open With” needs
        files only. New folder and paste do not need a selection.
        The background menu only offers New Folder, Paste, and
        Properties of the folder.

        Args:
            label: English msgid from the menu constants.

        Returns:
            True when the item should be clickable.

        @tags: #scope/widget #scope/file-ops #action/read
        """
        if background:
            return label in BACKGROUND_MENU_LABELS
        entries = self.get_selected_entries()
        if label in ("New Folder", "Paste"):
            return True
        if not entries:
            return False
        if label == "Open With":
            return all(not entry.is_dir for entry in entries)
        if len(entries) > 1 and label in _SINGLE_SELECTION_LABELS:
            return False
        if label == "Open in New Tab":
            return self.open_in_new_tab is not None
        if label == "Open in New Window":
            return self.open_in_new_window is not None
        return True

    def _on_menu_activate(self, _item: Gtk.MenuItem, label: str, background: bool = False) -> None:
        """Dispatch a built-in context-menu label to the matching handler.

        @tags: #scope/widget #action/bind #scope/file-ops
        """
        if background and label == "Properties":
            _column_module().show_item_properties([self.directory_path])
            return
        actions = {
            "Open": self._menu_open,
            "Open in New Tab": self._menu_new_tab,
            "Open in New Window": self._menu_new_window,
            "New Folder": self._menu_new_folder,
            "Cut": self._menu_cut,
            "Copy": self._menu_copy,
            "Paste": self.paste_clipboard,
            "Rename\u2026": self.begin_rename,
            "Move to Trash": self.delete_selected,
            "Copy Path": self._menu_copy_path,
            "Properties": self._menu_properties,
        }
        action = actions.get(label)
        if action is not None:
            action()

    def _menu_open(self) -> None:
        """Open the selection the same way Enter does.

        @tags: #scope/navigation #action/navigate #scope/widget
        """
        self._activate_selected()

    def _menu_new_tab(self) -> None:
        """_menu_new_tab.

        @tags: #scope/navigation #action/navigate #scope/widget
        """
        entry = self.get_selected()
        if entry is None or self.open_in_new_tab is None:
            return
        self.open_in_new_tab(entry.path)

    def _menu_new_window(self) -> None:
        """_menu_new_window.

        @tags: #scope/navigation #action/navigate #scope/widget
        """
        entry = self.get_selected()
        if entry is None or self.open_in_new_window is None:
            return
        self.open_in_new_window(entry.path)

    def _menu_new_folder(self) -> None:
        """_menu_new_folder.

        @tags: #scope/file-ops #action/write #scope/widget
        """
        self.create_folder()

    def _menu_cut(self) -> None:
        """_menu_cut.

        @tags: #scope/file-ops #action/write #side-effect/clipboard #scope/widget
        """
        self.copy_selected(True)

    def _menu_copy(self) -> None:
        """_menu_copy.

        @tags: #scope/file-ops #action/write #side-effect/clipboard #scope/widget
        """
        self.copy_selected(False)

    def _menu_copy_path(self) -> None:
        """_menu_copy_path.

        @tags: #scope/file-ops #action/write #side-effect/clipboard #scope/widget
        """
        entry = self.get_selected()
        if entry is None:
            return
        try:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(entry.path, -1)
        except Exception:
            return

    def _menu_properties(self) -> None:
        """Open Nemo's stock properties window for the selection.

        @tags: #scope/file-ops #scope/widget #action/render
        """
        entries = self.get_selected_entries()
        if not entries:
            return
        _column_module().show_item_properties([entry.path for entry in entries])
