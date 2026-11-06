"""List, selection, and directory monitor for one Miller column.

Mixed into ``MillerColumn``. The column reports selection upward and reloads
itself after a directory change settles. A typed prefix hides rows in place;
the directory is not listed again on each key.
"""

from __future__ import annotations

import os
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk, Pango

from miller.data import FileEntry, list_directory
from miller.i18n import _, ngettext
from miller.ui.dnd import attach_drag_and_drop

_ROW_ICON_SIZE = 16
_EMBLEM_ICON_SIZE = 8
# Nemo draws the first emblem at the bottom-right, then the other corners.
_EMBLEM_CORNERS = (
    (Gtk.Align.END, Gtk.Align.END),
    (Gtk.Align.START, Gtk.Align.END),
    (Gtk.Align.START, Gtk.Align.START),
    (Gtk.Align.END, Gtk.Align.START),
)
# Nemo finishes a drop after we return. Wait for the directory event to settle.
_MONITOR_DEBOUNCE_MS = 200


def _item_count_label(count: int) -> str:
    """Count badge. The catalog supplies the plural forms.

    @tags: #scope/widget #action/render #type/helper
    """
    return ngettext("%d item", "%d items", count) % count


def _folder_title(path: str) -> str:
    """Basename shown in the column header. The filesystem root is ``/``.

    @tags: #scope/navigation #action/read #type/helper
    """
    absolute = os.path.abspath(path)
    if absolute == os.path.sep:
        return os.path.sep
    return os.path.basename(absolute)


def _directory_row_icon(entry: FileEntry) -> Gtk.Widget:
    """Folder face for one column row.

    ``icon_name`` stays ``folder-symbolic`` for preview and drag. The row
    paints the colored file when Nemo stored one, otherwise the filled
    theme folder. Emblems are drawn on top only when the directory has any.

    @tags: #scope/widget #action/render #type/helper #scope/file-ops
    """
    image = Gtk.Image()
    if entry.icon_file:
        # FileIcon scales with set_pixel_size. set_from_file would keep the
        # 48px PNG and ignore the row size.
        image.set_from_gicon(
            Gio.FileIcon.new(Gio.File.new_for_path(entry.icon_file)),
            Gtk.IconSize.MENU,
        )
    else:
        image.set_from_gicon(
            Gio.ThemedIcon.new_from_names(list(entry.folder_icon_names or ("folder",))),
            Gtk.IconSize.MENU,
        )
    image.set_pixel_size(_ROW_ICON_SIZE)
    badges = _emblem_badges(entry.emblems)
    if not badges:
        return image
    overlay = Gtk.Overlay()
    overlay.set_size_request(_ROW_ICON_SIZE, _ROW_ICON_SIZE)
    overlay.add(image)
    for badge in badges:
        overlay.add_overlay(badge)
    return overlay


def _emblem_badges(names: tuple[str, ...]) -> list[Gtk.Image]:
    """Up to four corner badges. A name missing from the theme is skipped.

    @tags: #scope/widget #action/render #action/read #type/helper #scope/file-ops
    """
    theme = Gtk.IconTheme.get_default()
    if theme is None:
        return []
    badges: list[Gtk.Image] = []
    corners = iter(_EMBLEM_CORNERS)
    for name in names:
        if not theme.has_icon(name):
            continue
        try:
            halign, valign = next(corners)
        except StopIteration:
            break
        badge = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU)
        badge.set_pixel_size(_EMBLEM_ICON_SIZE)
        badge.set_halign(halign)
        badge.set_valign(valign)
        badges.append(badge)
    return badges


class ColumnListMixin:
    """Rows, selection state, and the directory monitor."""

    def refresh(self) -> None:
        """Reload the directory. Selection is cleared and not announced.

        The rebuild swallows ``row-selected`` so emptying the list does not
        look like the user cleared the selection. Callers that need the
        board to follow (delete, paste, rename) select again afterwards.
        Destroying the focused row clears window focus, so ownership is
        checked before the rebuild and restored afterwards.

        @tags: #scope/file-ops #scope/navigation #action/read #action/bind #action/render #scope/widget
        """
        had_focus = self._owns_focus()
        self._rebuilding = True
        try:
            self.listbox.unselect_all()
            for row in list(self.listbox.get_children()):
                self.listbox.remove(row)
                row.destroy()
            entries = list_directory(self.directory_path, show_hidden=self.show_hidden)
            if entries:
                for entry in entries:
                    self.listbox.add(self._make_row(entry))
            else:
                self.listbox.add(self._make_empty_row())
        finally:
            self._rebuilding = False
        self.listbox.show_all()
        # show_all would otherwise undo a filter that was already applied.
        self._sync_search_chrome()
        if had_focus:
            self.focus_row()

    def get_selected_entries(self) -> list[FileEntry]:
        """Return the selected real rows, in list order.

        The empty-folder placeholder is not a file and is omitted. Rows
        hidden by the column search are omitted too, so copy and trash
        cannot see a name the user cannot see. Order follows the list, not
        the order the user clicked.

        Returns:
            Selected entries. Empty when nothing real is selected.

        @tags: #scope/file-ops #scope/navigation #action/read #scope/widget
        """
        chosen = set(self.listbox.get_selected_rows())
        entries = []
        for row in self.listbox.get_children():
            if row not in chosen:
                continue
            entry = getattr(row, "file_entry", None)
            if entry is None or not self._name_matches(entry.name):
                continue
            entries.append(entry)
        return entries

    def get_selected(self) -> Optional[FileEntry]:
        """Return the only selected entry.

        Several rows, no row, or the empty placeholder all return None.
        Rename, Right, and open-in-tab depend on that: they have no single
        path to act on.

        Returns:
            The entry when exactly one real row is selected, else None.

        @tags: #scope/file-ops #scope/navigation #action/read #scope/widget
        """
        entries = self.get_selected_entries()
        if len(entries) == 1:
            return entries[0]
        return None

    def focus_row(self) -> None:
        """Move keyboard focus onto a row in this column.

        ``Gtk.ListBox`` itself is not focusable. Focus goes to the selected
        row when that row still matches the search, otherwise the first
        visible row, otherwise the empty-folder placeholder. A search that
        hides every row focuses the column itself so Left and Escape still
        arrive. The search label stays unfocusable.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        selected = self.listbox.get_selected_row()
        row = selected if self._is_visible_real_row(selected) else None
        if row is None:
            visible = self._visible_real_rows()
            row = visible[0] if visible else None
        if row is None:
            for child in self.listbox.get_children():
                if getattr(child, "file_entry", None) is None:
                    row = child
                    break
        if row is not None:
            self.set_can_focus(False)
            # The row is already selected. A focus grab can emit
            # selected-rows-changed, and the guard would treat that as a
            # cursor move and clear the selection the reload just restored.
            keep = row.is_selected()
            if keep:
                self._suppress_select = True
            try:
                row.grab_focus()
            finally:
                if keep:
                    self._suppress_select = False
            return
        if self._search_query:
            self.set_can_focus(True)
            self.grab_focus()

    def select_name(self, name: str) -> None:
        """Select the row named ``name`` and run the normal select path.

        A second call for the row that is already selected still notifies.
        ``Gtk.ListBox`` would otherwise swallow it, and the board would not
        hear a repeated click on the same folder.

        Args:
            name: Basename to select. Unknown names are ignored.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        for row in self.listbox.get_children():
            entry = getattr(row, "file_entry", None)
            if entry is None or entry.name != name:
                continue
            selected = self.get_selected_entries()
            if len(selected) == 1 and selected[0].name == name:
                self._anchor_row = row
                self.on_select(self, entry)
                if self._owns_focus():
                    row.grab_focus()
            else:
                self._user_select_row(row)
            return

    def set_show_hidden(self, show: bool) -> None:
        """Show or hide dotfiles in this column and reload.

        Does not write the file-manager preference. The host pushes the
        setting into each column when it wants them to agree.

        Args:
            show: True to list names that start with a dot.
    
        @tags: #scope/view-preference #action/toggle #scope/widget
        """
        self.show_hidden = bool(show)
        self.refresh()

    def _select_first_real(self) -> bool:
        """Select the first real row without notifying the board.

        Used when stepping into a column: the row should look selected, but
        the selection signal would open yet another column.

        Returns:
            True when a real row was selected. False for an empty folder.

        @tags: #scope/navigation #action/bind #scope/widget #type/helper
        """
        for row in self._visible_real_rows():
            self._set_selection([row], notify=False)
            self._anchor_row = row
            return True
        return False

    def _owns_focus(self) -> bool:
        """Return True when the window focus is inside this column.

        Checked before destroying rows. Removing the focused widget clears
        window focus, so the test must happen first.

        @tags: #scope/navigation #action/read #scope/widget #type/helper
        """
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            return False
        focused = toplevel.get_focus()
        if focused is None:
            return False
        current = focused
        while current is not None:
            if current is self:
                return True
            current = current.get_parent()
        return False

    def _build_header(self) -> None:
        """_build_header.

        @tags: #scope/widget #action/render
        """
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.get_style_context().add_class("mc-column-header")

        self.title_label = Gtk.Label(label=_folder_title(self.directory_path))
        self.title_label.get_style_context().add_class("mc-column-title")
        self.title_label.set_halign(Gtk.Align.START)
        self.title_label.set_hexpand(True)
        self.title_label.set_xalign(0)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)

        self.count_label = Gtk.Label(label=_item_count_label(0))
        self.count_label.get_style_context().add_class("mc-column-count")
        self.count_label.set_halign(Gtk.Align.END)

        header.pack_start(self.title_label, True, True, 0)
        header.pack_end(self.count_label, False, False, 0)
        self.pack_start(header, False, False, 0)

    def _build_list(self) -> None:
        """_build_list.

        @tags: #scope/widget #action/render
        """
        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("mc-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.listbox.set_activate_on_single_click(False)
        self.listbox.set_vexpand(True)
        self.listbox.connect("selected-rows-changed", self._on_selected_rows_changed)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("activate-cursor-row", self._on_activate_cursor_row)
        self.listbox.connect("key-press-event", self._on_key_press)
        self.listbox.connect("button-press-event", self._on_button_press)
        self.listbox.connect("button-release-event", self._on_button_release)

        self.list_scroll = Gtk.ScrolledWindow()
        self.list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scroll.set_vexpand(True)
        self.list_scroll.get_style_context().add_class("mc-scroll")
        self.list_scroll.add(self.listbox)
        # The chip floats over the first rows, directly under the header.
        # It does not take focus: keyboard focus stays on a row. The box
        # paints the column color; a bare label was shading the theme
        # background into a black slab.
        self.search_label = Gtk.Label()
        self.search_label.set_halign(Gtk.Align.START)
        self.search_label.set_valign(Gtk.Align.CENTER)
        self.search_label.set_can_focus(False)
        self.search_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.search_label.set_max_width_chars(24)
        search_icon = Gtk.Image.new_from_icon_name("edit-find-symbolic", Gtk.IconSize.MENU)
        search_icon.set_pixel_size(_ROW_ICON_SIZE)
        search_icon.set_valign(Gtk.Align.CENTER)
        search_icon.get_style_context().add_class("mc-column-search-icon")
        self.search_chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_chip.get_style_context().add_class("mc-column-search")
        self.search_chip.set_halign(Gtk.Align.START)
        self.search_chip.set_valign(Gtk.Align.START)
        self.search_chip.set_can_focus(False)
        self.search_chip.set_no_show_all(True)
        self.search_chip.pack_start(search_icon, False, False, 0)
        self.search_chip.pack_start(self.search_label, False, False, 0)
        self.search_chip.hide()
        self.search_label.hide()
        self._search_im = Gtk.IMContextSimple()
        self._search_im.connect("commit", self._on_search_commit)
        self.listbox.connect("realize", self._on_search_realize)
        self.listbox.connect("unrealize", self._on_search_unrealize)
        self.listbox.set_filter_func(self._filter_row, None)
        overlay = Gtk.Overlay()
        overlay.set_vexpand(True)
        overlay.add(self.list_scroll)
        overlay.add_overlay(self.search_chip)
        self.pack_start(overlay, True, True, 0)
        attach_drag_and_drop(self)

    def _on_activate_cursor_row(self, listbox: Gtk.ListBox) -> None:
        """Stop Space from activating the row so the key can still bubble.

        ListBox maps Space onto this signal. Returning True from key-press
        would swallow the event; stopping only this emission leaves Return
        and double-click on the normal open path.

        @tags: #scope/widget #scope/navigation #scope/file-ops #action/bind #action/clear #type/helper
        """
        if getattr(self, "_space_passthrough", False):
            listbox.stop_emission_by_name("activate-cursor-row")

    def _name_matches(self, name: str) -> bool:
        """Return whether ``name`` passes the column prefix.

        Matching is ``casefold`` plus ``startswith``, so ``Readme`` matches
        ``rea`` and a character in the middle of the name does not. An empty
        query matches every name. The same test feeds the list filter, the
        selection, and the arrow keys, so a hidden row cannot stay selected.

        @tags: #scope/navigation #action/read #type/helper #scope/widget #side-effect/pure-function
        """
        query = self._search_query.casefold()
        if not query:
            return True
        return name.casefold().startswith(query)

    def _filter_row(self, row: Gtk.ListBoxRow, _user_data=None) -> bool:
        """Show placeholder rows and names that match the prefix.

        @tags: #scope/navigation #scope/widget #action/read #action/render #type/helper
        """
        entry = getattr(row, "file_entry", None)
        if entry is None:
            return True
        return self._name_matches(entry.name)

    def _is_visible_real_row(self, row) -> bool:
        """Return whether ``row`` is a file or folder the search still shows.

        @tags: #scope/navigation #scope/widget #action/read #type/helper
        """
        entry = getattr(row, "file_entry", None)
        if entry is None:
            return False
        return self._name_matches(entry.name)

    def _visible_real_rows(self) -> list:
        """Return real rows that pass the current prefix.

        @tags: #scope/navigation #scope/widget #action/read #type/helper
        """
        return [row for row in self._real_rows() if self._name_matches(row.file_entry.name)]

    def _sync_search_chrome(self) -> None:
        """Show the query chip and the count of rows the user can see.

        The chip stays hidden while the query is empty. The badge then
        counts the directory, not a filter of it. The chip is an overlay, so
        the list reserves the chip's height as top padding. Margin would sit
        outside the list and show the scrolled window's color instead.

        @tags: #scope/navigation #scope/widget #action/render #action/toggle
        """
        self.listbox.invalidate_filter()
        query = self._search_query
        if query:
            self.search_label.set_text(query)
            # no_show_all makes show_all skip the chip and its children.
            # Drop it for this show, then put it back so a later column
            # show_all cannot reveal an empty query.
            self.search_chip.set_no_show_all(False)
            self.search_chip.show_all()
            self.search_chip.set_no_show_all(True)
            _minimum, natural = self.search_chip.get_preferred_height()
            margin = self.search_chip.get_style_context().get_margin(Gtk.StateFlags.NORMAL)
            self._set_search_list_padding(natural + margin.top + margin.bottom)
            self.count_label.set_text(_item_count_label(len(self._visible_real_rows())))
            return
        self.search_chip.hide()
        self.search_label.hide()
        self._set_search_list_padding(0)
        self.count_label.set_text(_item_count_label(len(self._real_rows())))

    def _set_search_list_padding(self, top: int) -> None:
        """Inset the rows by ``top`` pixels, or restore the stylesheet.

        The gap has to be padding on the list. Top margin is outside the
        list's background, so it shows the scrolled window instead of the
        column.

        Args:
            top: Pixels of padding above the first row. Zero removes the
                override and the stylesheet's own padding applies again.

        @tags: #scope/widget #action/render #scope/navigation
        """
        self.listbox.set_margin_top(0)
        context = self.listbox.get_style_context()
        provider = getattr(self, "_search_padding_provider", None)
        if top <= 0:
            if provider is not None:
                context.remove_provider(provider)
                self._search_padding_provider = None
            return
        if provider is None:
            provider = Gtk.CssProvider()
            context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            self._search_padding_provider = provider
        provider.load_from_data(f".mc-list {{ padding-top: {int(top)}px; }}".encode("utf-8"))

    def _drop_filtered_selection(self) -> None:
        """Unselect rows the prefix just hid, and tell the board if that changed.

        A folder that disappears must prune the columns to its right. The
        board already does that when the selection becomes empty. Rows that
        still match stay selected, so an open child column stays put.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        current = [
            row
            for row in self.listbox.get_selected_rows()
            if getattr(row, "file_entry", None) is not None
        ]
        kept = [row for row in current if self._name_matches(row.file_entry.name)]
        if [row.file_entry.name for row in kept] == [row.file_entry.name for row in current]:
            return
        self._set_selection(kept, notify=True)

    def _set_search_query(self, query: str) -> None:
        """Replace the prefix and hide rows that no longer match.

        The directory is not read again. Focus follows a visible row when
        this column already has the keyboard, and does not jump the
        selection onto the first remaining name.

        Args:
            query: New prefix. Empty restores the directory.

        @tags: #scope/navigation #action/write #action/bind #action/render #action/clear #scope/widget
        """
        if query == self._search_query:
            return
        had_focus = self._owns_focus()
        self._search_query = query
        self._sync_search_chrome()
        self._drop_filtered_selection()
        if not had_focus:
            return
        # Hiding the focused row clears window focus. A row that is still
        # selected already has the keyboard; grabbing it again makes GTK
        # treat the focus as a new selection and the guard drops it.
        selected = self.listbox.get_selected_row()
        if self._is_visible_real_row(selected):
            if not self._owns_focus():
                selected.grab_focus()
            return
        self.focus_row()

    def _make_row(self, entry: FileEntry) -> Gtk.ListBoxRow:
        """_make_row.

        @tags: #scope/widget #action/render #scope/file-ops
        """
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("mc-row")
        row.file_entry = entry
        row.rename_widget = None
        row.set_tooltip_text(entry.name)
        row.get_accessible().set_name(entry.name)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if entry.is_dir:
            icon = _directory_row_icon(entry)
        else:
            icon = Gtk.Image.new_from_icon_name(
                entry.icon_name or "text-x-generic-symbolic",
                Gtk.IconSize.MENU,
            )
            icon.set_pixel_size(_ROW_ICON_SIZE)
        name = Gtk.Label(label=entry.name)
        name.set_halign(Gtk.Align.START)
        name.set_hexpand(True)
        name.set_xalign(0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(24)
        row.name_label = name

        box.pack_start(icon, False, False, 0)
        box.pack_start(name, True, True, 0)
        if entry.is_dir:
            chevron = Gtk.Image.new_from_icon_name("pan-end-symbolic", Gtk.IconSize.MENU)
            chevron.set_pixel_size(_ROW_ICON_SIZE)
            chevron.get_style_context().add_class("mc-chevron")
            box.pack_end(chevron, False, False, 0)
        row.add(box)
        return row

    def _make_empty_row(self) -> Gtk.ListBoxRow:
        """_make_empty_row.

        @tags: #scope/widget #action/render
        """
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        row.file_entry = None
        label = Gtk.Label(label=_("Empty folder"))
        label.get_style_context().add_class("mc-empty")
        label.set_halign(Gtk.Align.CENTER)
        label.set_hexpand(True)
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        row.add(label)
        return row

    def _on_selected_rows_changed(self, _listbox: Gtk.ListBox) -> None:
        """Reject a selection GTK made while focusing, otherwise notify.

        ``row-selected`` does not fire for every row once the list allows
        several. Realizing a column still selects the first row, and that
        is not a click, so the same guard lives on this signal.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        if self._rebuilding or self._dead or self._suppress_select:
            return
        if not self._selection_from_user:
            if self.listbox.get_selected_rows():
                self._suppress_select = True
                try:
                    self.listbox.unselect_all()
                finally:
                    self._suppress_select = False
            return
        self._emit_selection()

    def _user_select_row(self, row: Gtk.ListBoxRow) -> None:
        """Select only ``row`` and keep keyboard focus when we already own it.

        Mouse clicks focus the clicked row themselves. Up/Down and
        ``select_name`` after a reload need the explicit grab so focus does
        not stay on a destroyed or previous row. Multiple mode adds a row
        on ``select_row``, so the previous selection is cleared first.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        self._set_selection([row], notify=True)
        if self._owns_focus():
            row.grab_focus()

    def _set_selection(self, rows, notify: bool) -> None:
        """Replace the selection, then notify once.

        ``select_row`` in multiple mode adds a row. Clearing first is what
        makes a plain arrow collapse to one row. The signal during that
        clear would tell the board the selection is empty and prune the chain.

        Args:
            rows: Rows that should remain selected.
            notify: When true, emit ``on_select`` after the list settles.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        self._suppress_select = True
        try:
            self.listbox.unselect_all()
            for row in rows:
                self.listbox.select_row(row)
        finally:
            self._suppress_select = False
        if notify:
            self._emit_selection()

    def _emit_selection(self) -> None:
        """Tell the board about the selection.

        One real row passes that entry and becomes the shift-anchor.
        Several rows pass None; the board reads ``get_selected_entries``
        and shows the summary instead of opening a folder.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        entries = self.get_selected_entries()
        if len(entries) == 1:
            self._anchor_row = self._row_named(entries[0].name)
            self.on_select(self, entries[0])
            return
        self.on_select(self, None)

    def _real_rows(self) -> list:
        """Return list rows that represent a file or folder.

        @tags: #scope/widget #action/read #type/helper
        """
        return [
            row
            for row in self.listbox.get_children()
            if getattr(row, "file_entry", None) is not None
        ]

    def _row_named(self, name: str):
        """Return the real row called ``name``, or None.

        @tags: #scope/widget #action/read #type/helper
        """
        for row in self._real_rows():
            if row.file_entry.name == name:
                return row
        return None

    def _focused_row(self):
        """Return the focused ``ListBoxRow``, if focus is on one.

        @tags: #scope/navigation #action/read #type/helper
        """
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            return None
        focused = toplevel.get_focus()
        while focused is not None:
            if isinstance(focused, Gtk.ListBoxRow):
                return focused
            focused = focused.get_parent()
        return None

    def _end_user_selection(self) -> bool:
        """_end_user_selection.

        @tags: #scope/navigation #type/helper
        """
        self._selection_from_user = False
        return False

    def _on_destroy(self, *_args) -> None:
        """_on_destroy.

        @tags: #scope/widget #lifecycle/destroy
        """
        self._dead = True
        self._stop_directory_monitor()
        if self._context_menu is not None:
            self._context_menu.destroy()
            self._context_menu = None

    def _start_directory_monitor(self) -> None:
        """Watch this directory so a drop Nemo finishes later shows up.

        @tags: #scope/file-ops #lifecycle/init #scope/widget
        """
        self._stop_directory_monitor()
        try:
            monitor = Gio.File.new_for_path(self.directory_path).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES,
                None,
            )
        except GLib.Error as error:
            print(f"[miller] Failed to watch {self.directory_path}: {error}")
            return
        self._monitor = monitor
        self._monitor_changed_id = monitor.connect("changed", self._on_directory_changed)

    def _stop_directory_monitor(self) -> None:
        """Cancel the directory watch and any pending reload.

        @tags: #scope/file-ops #lifecycle/destroy #scope/widget
        """
        if self._monitor_source:
            GLib.source_remove(self._monitor_source)
            self._monitor_source = 0
        monitor = self._monitor
        self._monitor = None
        if monitor is None:
            return
        if self._monitor_changed_id:
            monitor.disconnect(self._monitor_changed_id)
            self._monitor_changed_id = 0
        monitor.cancel()

    def _on_directory_changed(self, _monitor, _file, _other, _event_type) -> None:
        """Debounce a directory event into one reload.

        @tags: #scope/file-ops #action/bind #scope/widget
        """
        if self._dead:
            return
        self._schedule_directory_reload()

    def _schedule_directory_reload(self) -> None:
        """Schedule a selection-preserving reload after the debounce.

        @tags: #scope/file-ops #action/bind #type/helper
        """
        if self._dead:
            return
        if self._monitor_source:
            GLib.source_remove(self._monitor_source)
        self._monitor_source = GLib.timeout_add(_MONITOR_DEBOUNCE_MS, self._on_monitor_timeout)

    def _on_monitor_timeout(self) -> bool:
        """Run the debounced reload unless a rename entry is open.

        Returns:
            False, so the timeout source is removed.

        @tags: #scope/file-ops #action/read #lifecycle/destroy
        """
        self._monitor_source = 0
        if self._dead:
            return False
        if self._rename_active():
            self._monitor_deferred = True
            return False
        self._reload_keeping_selection()
        return False

    def _reload_keeping_selection(self) -> None:
        """Reload and reselect the same names.

        An unchanged selection does not notify the board. A redundant
        signal would prune the columns to the right. A name that disappeared
        does notify, with whatever is still selected. The reload waits while
        a rename entry is open so it does not destroy that entry.

        @tags: #scope/file-ops #scope/navigation #action/read #action/bind #action/render #scope/widget
        """
        if self._dead:
            return
        if self._rename_active():
            self._monitor_deferred = True
            return
        previous = [
            row.file_entry.name
            for row in self.listbox.get_selected_rows()
            if getattr(row, "file_entry", None) is not None
        ]
        anchor_entry = getattr(self._anchor_row, "file_entry", None)
        anchor_name = anchor_entry.name if anchor_entry is not None else None
        self.refresh()
        wanted = set(previous)
        rows = [
            row
            for row in self._visible_real_rows()
            if row.file_entry.name in wanted
        ]
        self._set_selection(rows, notify=False)
        anchor_row = self._row_named(anchor_name) if anchor_name else None
        if anchor_row is not None and not self._name_matches(anchor_name):
            anchor_row = None
        self._anchor_row = anchor_row
        if self._anchor_row is None and rows:
            self._anchor_row = rows[0]
        current = {entry.name for entry in self.get_selected_entries()}
        if current != wanted:
            self._emit_selection()
        if self._owns_focus():
            self.focus_row()
