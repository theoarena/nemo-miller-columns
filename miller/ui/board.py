"""Horizontal Miller board: a chain of columns plus a file preview.

The board is an event box so Nemo can drop it into a view slot. Inside it,
a scrolled window reports a 100px minimum in both axes. Nemo's geometry
negotiation would otherwise refuse to shrink the window below the sum of
the column widths. The natural size still follows the columns.

Selecting a directory appends one column and points the active path at
that child, so the host path bar follows the folder that just opened.
Selecting a file, nothing, or several rows drops the columns to the right
and points the active path at the column that holds the selection.
``focused_index`` is the directory Nemo was asked to show. The accent
stays on the column the user is acting in, and keyboard focus follows
the accent.

Same-window folder changes are the host's job via ``on_active_path_changed``.
This module does not call ``open_in_same_window`` and does not start a
file-manager process: doing either from a selection handler re-enters Nemo.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from miller.data import FileEntry
from miller.theme import bind_accent_ink, install_theme
from miller.ui.column import MillerColumn
from miller.ui.preview import PreviewPane

# One frame at 60Hz. Rapid cursor moves collapse into a single chain update.
# @tags: #type/constant #scope/navigation
_SELECTION_DELAY_MS = 16


class FlexScrolledWindow(Gtk.ScrolledWindow):
    """Scrolled window whose minimum size stays small enough for Nemo.

    Vertical scrolling is disabled on the board (columns scroll themselves),
    which would otherwise publish the child's full width as the minimum.
    Reporting 100px lets the user shrink the window; the columns scroll
    horizontally inside this widget.

    @tags: #scope/widget #type/widget #scope/slot-lifecycle
    """

    def do_get_preferred_width(self):
        """do_get_preferred_width.

        @tags: #scope/widget #type/widget #scope/slot-lifecycle
        """
        _minimum, natural = Gtk.ScrolledWindow.do_get_preferred_width(self)
        return (100, max(100, natural))

    def do_get_preferred_height(self):
        """do_get_preferred_height.

        @tags: #scope/widget #type/widget #scope/slot-lifecycle
        """
        _minimum, natural = Gtk.ScrolledWindow.do_get_preferred_height(self)
        return (100, max(100, natural))

    def do_get_preferred_width_for_height(self, height):
        """do_get_preferred_width_for_height.

        @tags: #scope/widget #type/widget #scope/slot-lifecycle
        """
        _minimum, natural = Gtk.ScrolledWindow.do_get_preferred_width_for_height(self, height)
        return (100, max(100, natural))

    def do_get_preferred_height_for_width(self, width):
        """do_get_preferred_height_for_width.

        @tags: #scope/widget #type/widget #scope/slot-lifecycle
        """
        _minimum, natural = Gtk.ScrolledWindow.do_get_preferred_height_for_width(self, width)
        return (100, max(100, natural))


class MillerColumnsView(Gtk.EventBox):
    """The column chain and the preview, left to right.

    ``columns`` is the live chain. ``focused_index`` is the directory Nemo
    was asked to show. A click moves it: a folder points at the opened
    column, and a file points at the column that holds the selection.
    The accent is the column the user is acting in, and a click leaves
    it there. The preview is not a column; it is packed at the end of
    the strip only while a file is selected.

    Attributes:
        columns: Live ``MillerColumn`` chain, left to right.
        preview_pane: File summary. Unparented until a file is selected.
        focused_index: Index of the directory Nemo was asked to show.
        target_window: Host window, stored for the extension. Unused for
            navigation so a column change cannot re-enter Nemo.

    @tags: #scope/widget #type/widget #scope/navigation
    """

    def __init__(
        self,
        initial_path: str,
        target_window: Optional[Gtk.Window] = None,
        on_active_path_changed: Optional[Callable[[str], None]] = None,
        ) -> None:
        """Wire scroller, preview slot, and the initial column chain.

        @tags: #lifecycle/init #scope/widget #scope/navigation #action/bind
        """
        super().__init__()
        self.get_style_context().add_class("mc-root")
        self.set_hexpand(True)
        self.set_vexpand(True)
        install_theme()
        bind_accent_ink(self)

        self.target_window = target_window
        self.on_active_path_changed = on_active_path_changed
        self.columns: list[MillerColumn] = []
        self.focused_index = 0
        self._suspend_chain = False
        self._select_timer = 0
        self._pending: Optional[tuple[MillerColumn, Optional[FileEntry]]] = None
        self._scroll_source = 0
        self._scrolling = False
        self._hscroll_changed_id = 0
        self._notified_path: Optional[str] = None
        self._dead = False
        self._current_column: Optional[MillerColumn] = None

        self.preview_pane = PreviewPane()
        self._preview_scroll = Gtk.ScrolledWindow()
        self._preview_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._preview_scroll.set_vexpand(True)
        self._preview_scroll.set_hexpand(True)
        self._preview_scroll.set_halign(Gtk.Align.FILL)
        self._preview_scroll.get_style_context().add_class("mc-scroll")
        self._preview_scroll.add(self.preview_pane)

        self.scroller = FlexScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scroller.set_hexpand(True)
        self.scroller.set_vexpand(True)
        self.scroller.set_halign(Gtk.Align.FILL)
        self.scroller.set_valign(Gtk.Align.FILL)
        self.scroller.get_style_context().add_class("mc-scroll")

        self.columns_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.columns_box.set_hexpand(True)
        self.columns_box.set_vexpand(True)
        self.scroller.add(self.columns_box)
        self.add(self.scroller)
        # "changed" fires inside size-allocate, before paint, including when
        # the window is resized. Value changes do not emit it, so jumping
        # to the end here does not loop.
        hadjustment = self.scroller.get_hadjustment()
        if hadjustment is not None:
            self._hscroll_changed_id = hadjustment.connect("changed", self._on_hscroll_changed)

        self.connect("destroy", self._on_destroy)
        self._append_column(os.path.abspath(os.path.expanduser(initial_path)))
        self.focused_index = 0
        self._mark_current_column(self.columns[0])
        self._notify_active_path()
        self.focus_active_column()
        self._queue_scroll()

    def set_root_path(self, path: str) -> None:
        """Focus ``path`` if it is already a column, otherwise start over.

        An existing column keeps everything to its left and its immediate
        child. Anything deeper, and the preview, is dropped. A path that
        is not in the chain replaces the chain with one new root column.
        The active-path callback runs only when the focused directory changes.

        Args:
            path: Directory to focus. Normalized with ``os.path.abspath``.
    
        @tags: #scope/navigation #action/bind #scope/widget
        """
        path = os.path.abspath(os.path.expanduser(path))
        self._cancel_pending()
        for index, column in enumerate(self.columns):
            if column.directory_path != path:
                continue
            self.focused_index = index
            # Keep the immediate child (index + 1). Deeper columns belong to
            # a selection we are no longer standing on.
            self._prune_from(index + 2)
            self._unparent_preview()
            self._mark_current_column(self.columns[self.focused_index])
            self.focus_active_column()
            self._queue_scroll()
            self._notify_active_path()
            return

        self._clear_columns()
        self._unparent_preview()
        self._append_column(path)
        self.focused_index = 0
        self._mark_current_column(self.columns[0])
        self.focus_active_column()
        self._queue_scroll()
        self._notify_active_path()

    def get_active_path(self) -> str:
        """Return the focused column's directory."""
        return self.columns[self.focused_index].directory_path

    def focus_active_column(self) -> None:
        """Grab keyboard focus on a row in the column the user is acting in.

        That is the accent column. ``focused_index`` is the directory Nemo
        was asked to show, and a click does move it. Restoring focus from
        ``focused_index`` after a tab switch handed Up/Down to the root
        column, which pruned the chain. Tab restore has to keep using the
        accent.

        ``Gtk.ListBox`` does not take focus. The column focuses its selected
        row, or the first real row, or the empty placeholder.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        column = self._current_column
        if column not in self.columns:
            if self.focused_index < 0 or self.focused_index >= len(self.columns):
                return
            column = self.columns[self.focused_index]
        column.focus_row()

    def flush_selection(self) -> None:
        """Apply the pending selection now.

        Column clicks are delayed by ``_SELECTION_DELAY_MS`` so a fast key
        repeat does not rebuild the chain on every row. Tests and any host
        that needs the chain immediately cancel that timer and apply the
        latest selection.
    
        @tags: #scope/navigation #action/bind #scope/widget
        """
        pending = self._pending
        self._cancel_pending()
        if pending is None:
            return
        self._apply_selection(pending[0], pending[1])

    def _scroll_to_end(self) -> None:
        """Keep the right edge of the strip in view.

        The chain grows to the right and the preview lives there. Anchoring
        on the focused column left the newest column clipped after a resize.
        Does nothing until the view is realized: the adjustment is not
        meaningful before the first allocate.

        @tags: #scope/widget #action/render
        """
        if self._dead or self._scrolling or not self.get_realized():
            return
        adjustment = self.scroller.get_hadjustment()
        if adjustment is None:
            return
        lower = adjustment.get_lower()
        value = adjustment.get_upper() - adjustment.get_page_size()
        if value < lower:
            value = lower
        self._scrolling = True
        try:
            adjustment.set_value(value)
        finally:
            self._scrolling = False

    def _on_hscroll_changed(self, _adjustment: Gtk.Adjustment) -> None:
        """_on_hscroll_changed.

        @tags: #scope/widget #action/bind #action/render
        """
        self._scroll_to_end()

    def _on_column_select(self, column: MillerColumn, entry: Optional[FileEntry]) -> None:
        """_on_column_select.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        # Auto-select while stepping into a column must not schedule a chain
        # update. The flag is only set around that select, so a later click
        # still goes through the timer.
        if self._suspend_chain or self._dead or getattr(column, "_dead", False):
            return
        # The accent follows the click immediately. The column chain still
        # waits for the selection timer so a key repeat does not rebuild it.
        self._mark_current_column(column)
        self._pending = (column, entry)
        if self._select_timer:
            GLib.source_remove(self._select_timer)
        self._select_timer = GLib.timeout_add(_SELECTION_DELAY_MS, self._on_select_timeout)

    def _on_select_timeout(self) -> bool:
        """_on_select_timeout.

        @tags: #scope/navigation #action/bind #lifecycle/destroy
        """
        pending = self._pending
        self._select_timer = 0
        self._pending = None
        if pending is not None and not self._dead:
            self._apply_selection(pending[0], pending[1])
        return False

    def _apply_selection(self, column: MillerColumn, entry: Optional[FileEntry]) -> None:
        """Open, preview, or clear the chain to the right of ``column``.

        ``focused_index`` follows the directory Nemo should show: the child
        when a folder opens, otherwise ``column`` itself. The accent and
        keyboard focus stay where the click left them. Right, Left, and a
        root change are what move those.

        A path already reported is ignored by ``_notify_active_path``, so
        choosing the folder that is already open does not ask the host again.

        @tags: #scope/navigation #scope/widget #scope/file-ops #action/bind #action/navigate #action/render #action/clear #type/helper
        """
        if column not in self.columns or getattr(column, "_dead", False):
            return
        index = self.columns.index(column)
        # More than one row is a summary, not a folder to open. The column
        # reports None for that case; the entries are still on the column.
        entries = column.get_selected_entries()
        active_index = index
        if len(entries) > 1:
            self._prune_from(index + 1)
            self._unparent_preview()
            self._show_selection_summary(entries)
        elif entry is not None and entry.is_dir:
            target = os.path.abspath(entry.path)
            # Re-selecting the folder that is already open must not stack
            # another column for the same path.
            if (
                index + 1 < len(self.columns)
                and self.columns[index + 1].directory_path == target
            ):
                self._prune_from(index + 2)
                self._unparent_preview()
            else:
                self._prune_from(index + 1)
                self._unparent_preview()
                self._append_column(target)
            active_index = index + 1
        else:
            self._prune_from(index + 1)
            self._unparent_preview()
            if entry is not None:
                self._show_preview(entry.path)
        # Set this after the prune. ``_prune_from`` falls back to the old
        # index when the accent column is gone, and a new index here would
        # paint the accent on the opened column.
        self.focused_index = active_index
        self._notify_active_path()

    def _on_nav_right(self, column: MillerColumn, entry: FileEntry) -> None:
        """Enter a folder column from the keyboard (Right or Enter).

        The first child row is highlighted without notifying the board so a
        folder does not open its own children until the user asks. A file does
        run ``_apply_selection`` so the preview follows that selection at once.
        An empty folder stays visible and keyboard focus stays on the parent
        row, because there is no row to enter. The active path still follows
        the opened column: ``_apply_selection`` already reported it.

        @tags: #scope/navigation #scope/widget #scope/file-ops #action/bind #action/navigate #action/render #action/clear #type/helper
        """
        if entry is None or not entry.is_dir or column not in self.columns:
            return
        self._cancel_pending()
        self._apply_selection(column, entry)
        if column not in self.columns:
            return
        child_index = self.columns.index(column) + 1
        if child_index >= len(self.columns):
            return
        child = self.columns[child_index]
        if child.get_selected() is None:
            self._suspend_chain = True
            try:
                entered = child._select_first_real()
            finally:
                self._suspend_chain = False
            if not entered:
                return
        selected = child.get_selected()
        if selected is not None and not selected.is_dir:
            self._apply_selection(child, selected)
        self.focused_index = child_index
        self._mark_current_column(child)
        self._notify_active_path()
        self.focus_active_column()
        self._queue_scroll()

    def _on_nav_left(self, column: MillerColumn) -> None:
        """_on_nav_left.

        @tags: #scope/navigation #action/bind #scope/widget
        """
        if column not in self.columns:
            return
        self._cancel_pending()
        index = self.columns.index(column)
        if index == 0:
            self._climb_to_parent(column)
            return
        self.focused_index = index - 1
        # Drop grandchildren of the column we landed on. Its immediate child
        # stays so the path we just left is still on screen.
        self._prune_from(self.focused_index + 2)
        self._mark_current_column(self.columns[self.focused_index])
        self._notify_active_path()
        self.focus_active_column()
        self._queue_scroll()

    def _climb_to_parent(self, column: MillerColumn) -> None:
        """_climb_to_parent.

        @tags: #scope/navigation #action/render #scope/widget
        """
        parent_path = os.path.dirname(column.directory_path)
        # The filesystem root's parent is itself, and a missing parent cannot
        # be listed. Both have to fail the climb or we would loop on "/".
        if parent_path == column.directory_path or not os.path.isdir(parent_path):
            return
        previous_name = os.path.basename(column.directory_path)
        parent_column = self._make_column(parent_path)
        if previous_name.startswith("."):
            parent_column.set_show_hidden(True)
        self.columns.insert(0, parent_column)
        self.columns_box.pack_start(parent_column, False, False, 0)
        self.columns_box.reorder_child(parent_column, 0)
        parent_column.show_all()
        self._suspend_chain = True
        try:
            parent_column.select_name(previous_name)
        finally:
            self._suspend_chain = False
        self.focused_index = 0
        self._prune_from(2)
        self._mark_current_column(self.columns[0])
        self._notify_active_path()
        self.focus_active_column()
        self._queue_scroll()

    def _make_column(self, path: str) -> MillerColumn:
        """_make_column.

        @tags: #scope/widget #action/render #lifecycle/init
        """
        return MillerColumn(
            path,
            on_select=self._on_column_select,
            on_activate_dir=self._on_nav_right,
            on_nav_left=self._on_nav_left,
            on_nav_right=self._on_nav_right,
            show_hidden=False,
        )

    def _append_column(self, path: str) -> MillerColumn:
        """_append_column.

        @tags: #scope/navigation #action/render #scope/widget
        """
        column = self._make_column(path)
        self.columns.append(column)
        self.columns_box.pack_start(column, False, False, 0)
        column.show_all()
        self._queue_scroll()
        return column

    def _prune_from(self, index: int) -> None:
        """_prune_from.

        @tags: #scope/navigation #lifecycle/destroy #scope/widget
        """
        while len(self.columns) > index:
            column = self.columns.pop()
            parent = column.get_parent()
            if parent is not None:
                parent.remove(column)
            column.destroy()
        self._fallback_current_column()

    def _mark_current_column(self, column: MillerColumn) -> None:
        """Paint the accent on ``column`` and clear it everywhere else.

        Breadcrumb columns keep a neutral selection. Exactly one column,
        the one the user is acting in, takes the theme accent.

        @tags: #scope/widget #action/render #scope/navigation
        """
        self._current_column = column
        for item in self.columns:
            style = item.get_style_context()
            if item is column:
                style.add_class("mc-column-current")
            elif style.has_class("mc-column-current"):
                style.remove_class("mc-column-current")

    def _fallback_current_column(self) -> None:
        """Move the accent to the focused column when the marked one is gone.

        @tags: #scope/navigation #action/render #scope/widget
        """
        current = self._current_column
        if current is not None and current in self.columns:
            return
        if 0 <= self.focused_index < len(self.columns):
            self._mark_current_column(self.columns[self.focused_index])
            return
        self._current_column = None

    def _clear_columns(self) -> None:
        """_clear_columns.

        @tags: #scope/navigation #lifecycle/destroy #scope/widget
        """
        self._prune_from(0)

    def _unparent_preview(self) -> None:
        """_unparent_preview.

        @tags: #scope/file-ops #action/clear #scope/widget
        """
        self.preview_pane.update_file(None)
        parent = self._preview_scroll.get_parent()
        if parent is not None:
            parent.remove(self._preview_scroll)

    def _show_preview(self, path: str) -> None:
        """_show_preview.

        @tags: #scope/file-ops #scope/widget #action/render
        """
        if self._preview_scroll.get_parent() is None:
            self.columns_box.pack_start(self._preview_scroll, True, True, 0)
        self.preview_pane.update_file(path)
        self._preview_scroll.show_all()

    def _show_selection_summary(self, entries) -> None:
        """Show the multi-selection summary in the preview slot.

        Args:
            entries: Selected entries from the column.

        @tags: #scope/file-ops #scope/widget #action/render
        """
        if self._preview_scroll.get_parent() is None:
            self.columns_box.pack_start(self._preview_scroll, True, True, 0)
        self.preview_pane.update_selection(entries)
        self._preview_scroll.show_all()

    def _notify_active_path(self) -> None:
        """_notify_active_path.

        @tags: #scope/navigation #action/navigate
        """
        if not self.columns:
            return
        path = self.get_active_path()
        if path == self._notified_path:
            return
        self._notified_path = path
        if self.on_active_path_changed is not None:
            self.on_active_path_changed(path)

    def _cancel_pending(self) -> None:
        """_cancel_pending.

        @tags: #scope/navigation #lifecycle/destroy
        """
        if self._select_timer:
            GLib.source_remove(self._select_timer)
            self._select_timer = 0
        self._pending = None

    def _queue_scroll(self) -> None:
        """_queue_scroll.

        @tags: #scope/widget #action/bind
        """
        if self._scroll_source or self._dead:
            return
        self._scroll_source = GLib.idle_add(self._on_scroll_idle)

    def _on_scroll_idle(self) -> bool:
        """_on_scroll_idle.

        @tags: #scope/widget #action/render #lifecycle/destroy
        """
        self._scroll_source = 0
        self._scroll_to_end()
        return False

    def _on_destroy(self, *_args) -> None:
        """_on_destroy.

        @tags: #scope/widget #lifecycle/destroy
        """
        self._dead = True
        self._cancel_pending()
        if self._hscroll_changed_id:
            hadjustment = self.scroller.get_hadjustment()
            if hadjustment is not None:
                hadjustment.disconnect(self._hscroll_changed_id)
            self._hscroll_changed_id = 0
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
            self._scroll_source = 0
