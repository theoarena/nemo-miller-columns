"""Keyboard and pointer handling for one Miller column.

Mixed into ``MillerColumn``. ``handle_key`` is callable without a GDK event
so tests can drive the column directly. Printable keys append to that
column's prefix; the input method commits composed characters such as
dead-key accents.
"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk


def _column_module():
    """Return the column module after import, so test rebinds are visible.

    ``open_file_default`` is replaced on ``miller.ui.column`` by the UI
    tests. Reading it here, not from ``miller.data``, keeps that patch.

    @tags: #type/helper #action/read
    """
    import miller.ui.column as column_module

    return column_module


def _ctrl_toggle_hidden(column) -> None:
    """Flip the column's hidden-file listing.

    @tags: #scope/widget #action/bind #type/helper
    """
    column.set_show_hidden(not column.show_hidden)


def _ctrl_copy(column) -> None:
    """Copy the selection.

    @tags: #scope/file-ops #action/write #side-effect/clipboard #type/helper
    """
    column.copy_selected(False)


def _ctrl_cut(column) -> None:
    """Cut the selection.

    @tags: #scope/file-ops #action/write #side-effect/clipboard #type/helper
    """
    column.copy_selected(True)


def _ctrl_paste(column) -> None:
    """Paste using the same destination rules as ``MillerColumn.paste_clipboard``.

    One selected folder receives the items; otherwise the column's listed
    directory does.

    @tags: #scope/file-ops #scope/widget #action/write #action/bind #side-effect/clipboard #type/helper
    """
    column.paste_clipboard()


def _ctrl_select_all(column) -> None:
    """Select every real row.

    @tags: #scope/widget #action/bind #type/helper
    """
    column._select_all_real()


# Plain Ctrl+letter. Shift is excluded: Ctrl+Shift+N is a different shortcut,
# and a shifted letter must not fall through into copy or paste.
_CTRL_KEY_ACTIONS = {
    Gdk.KEY_h: _ctrl_toggle_hidden,
    Gdk.KEY_H: _ctrl_toggle_hidden,
    Gdk.KEY_c: _ctrl_copy,
    Gdk.KEY_C: _ctrl_copy,
    Gdk.KEY_x: _ctrl_cut,
    Gdk.KEY_X: _ctrl_cut,
    Gdk.KEY_v: _ctrl_paste,
    Gdk.KEY_V: _ctrl_paste,
    Gdk.KEY_a: _ctrl_select_all,
    Gdk.KEY_A: _ctrl_select_all,
}


class ColumnInputMixin:
    """Keys, clicks, activation, and the right-edge resize handle."""

    def _bind_resize_handle(self) -> None:
        """Connect pointer events on ``self.resize_handle``.

        @tags: #scope/widget #action/bind #lifecycle/init
        """
        handle = self.resize_handle
        handle.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        handle.connect("enter-notify-event", self._on_resize_enter)
        handle.connect("leave-notify-event", self._on_resize_leave)
        handle.connect("button-press-event", self._on_resize_press)
        handle.connect("motion-notify-event", self._on_resize_motion)
        handle.connect("button-release-event", self._on_resize_release)

    def _set_resize_cursor(self, active: bool) -> None:
        """Show the horizontal-resize cursor and highlight on the handle, or restore both.

        The ``--hot`` class is toggled explicitly instead of relying on GTK's
        ``:hover`` state, which an EventBox does not always receive.

        @tags: #scope/widget #action/render #type/helper
        """
        context = self.resize_handle.get_style_context()
        if active:
            context.add_class("mc-column-resize-handle--hot")
        else:
            context.remove_class("mc-column-resize-handle--hot")
        window = self.resize_handle.get_window()
        if window is None:
            return
        if not active:
            window.set_cursor(None)
            return
        display = window.get_display()
        cursor = Gdk.Cursor.new_from_name(display, "col-resize")
        if cursor is None:
            cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.SB_H_DOUBLE_ARROW)
        window.set_cursor(cursor)

    def _on_resize_enter(self, _widget, _event) -> bool:
        """@tags: #scope/widget #action/bind"""
        self._set_resize_cursor(True)
        return False

    def _on_resize_leave(self, _widget, _event) -> bool:
        """@tags: #scope/widget #action/bind"""
        if not self._resize_dragging:
            self._set_resize_cursor(False)
        return False

    def _notify_resize_state(self, active: bool) -> None:
        """@tags: #scope/widget #action/bind #type/helper"""
        if self.on_resize_state is not None:
            self.on_resize_state(active)

    def _on_resize_press(self, _widget, event) -> bool:
        """Start a drag on a primary press; reset the width on a double press.

        The double press is delivered after the first press already started a
        drag, so it ends that drag first. The scroll freeze stays on for the
        reset allocation and is released on idle.

        @tags: #scope/widget #action/bind #action/write #format/int
        """
        if event.button != 1:
            return False
        if event.type == Gdk.EventType.BUTTON_PRESS:
            self._resize_dragging = True
            self._resize_start_x_root = event.x_root
            self._resize_start_width = self.get_column_width()
            self._notify_resize_state(True)
            return True
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self._resize_dragging = False
            self.reset_column_width()
            if self.on_width_changed is not None:
                self.on_width_changed(self, None)
            GLib.idle_add(self._release_resize_state)
            return True
        return False

    def _on_resize_motion(self, _widget, event) -> bool:
        """Live-resize while the primary button is held.

        @tags: #scope/widget #action/render #format/int
        """
        if not self._resize_dragging:
            return False
        delta = event.x_root - self._resize_start_x_root
        self.set_column_width(round(self._resize_start_width + delta))
        return True

    def _on_resize_release(self, _widget, event) -> bool:
        """Finish the drag, persist if the width changed, unfreeze on idle.

        @tags: #scope/widget #action/bind #action/write #format/int
        """
        if event.button != 1 or not self._resize_dragging:
            return False
        self._resize_dragging = False
        # The pointer may have left the strip during the drag; drop the highlight then.
        if event.x < 0 or event.x >= self.resize_handle.get_allocated_width():
            self._set_resize_cursor(False)
        if self.get_column_width() != self._resize_start_width and self.on_width_changed:
            self.on_width_changed(self, self.get_column_width())
        GLib.idle_add(self._release_resize_state)
        return True

    def _release_resize_state(self) -> bool:
        """Idle: release the board's scroll freeze once allocation settled.

        @tags: #scope/widget #action/bind #type/helper
        """
        self._notify_resize_state(False)
        return False

    def handle_key(self, keyval: int, state: Gdk.ModifierType = 0) -> bool:
        """Handle one key for this column.

        ``state`` may carry modifiers besides the ones we care about, so
        Control and Shift are tested with a mask. Arrow keys move within
        the column; they do not cross columns except Right on a directory
        and Left / BackSpace, which ask the board to move. BackSpace edits
        the search prefix while one is active, and only then falls through
        to Left. A printable character with no Control or Alt appends to
        that prefix. A leading Space does not: the window uses that key
        for file preview, so the prefix starts only after a real character.
        Space typed after that prefix is part of the name. An inline rename
        owns the keyboard until it closes.

        Args:
            keyval: GDK key value.
            state: Modifier mask. Extra flags are ignored.

        Returns:
            True when the key was consumed.
    
        @tags: #scope/navigation #scope/file-ops #action/bind #action/write #action/clear #scope/widget #type/helper
        """
        if self._rename_active():
            return False
        if state is None:
            state = 0
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # Ctrl+Shift+N is the new-folder shortcut. Checked before the plain
        # Ctrl letter keys so Shift does not fall through into paste/copy.
        if control and shift and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.create_folder()
            return True
        if control and not shift:
            action = _CTRL_KEY_ACTIONS.get(keyval)
            if action is not None:
                action(self)
                return True

        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up, Gdk.KEY_Down, Gdk.KEY_KP_Down):
            delta = -1 if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) else 1
            if shift and not control:
                self._extend_selection(delta)
            else:
                self._move_selection(delta)
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            entry = self.get_selected()
            # Right on a file or an empty column stays put. Left still navigates.
            if entry is not None and entry.is_dir:
                self.on_nav_right(self, entry)
            return True
        if keyval == Gdk.KEY_BackSpace and self._search_query:
            self._set_search_query(self._search_query[:-1])
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace):
            self.on_nav_left(self)
            return True
        if keyval == Gdk.KEY_Escape and self._search_query:
            self._search_im.reset()
            self._set_search_query("")
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            self._activate_selected()
            return True
        if keyval == Gdk.KEY_F2:
            if self.get_selected() is not None:
                self.begin_rename()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and not control:
            self.delete_selected()
            return True
        if self._append_printable(keyval, state):
            return True
        return False

    def _is_command_key(self, keyval: int, state: Gdk.ModifierType) -> bool:
        """Return whether ``handle_key`` consumes this key before the search.

        Printable characters and a BackSpace that edits the prefix are not
        commands. Those go through the input method first so a dead key can
        compose.

        @tags: #scope/navigation #scope/file-ops #action/read #type/helper #scope/widget #side-effect/pure-function
        """
        if state is None:
            state = 0
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if control and shift and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            return True
        if control and not shift and keyval in _CTRL_KEY_ACTIONS:
            return True
        if keyval in (
            Gdk.KEY_Up,
            Gdk.KEY_KP_Up,
            Gdk.KEY_Down,
            Gdk.KEY_KP_Down,
            Gdk.KEY_Right,
            Gdk.KEY_KP_Right,
            Gdk.KEY_Left,
            Gdk.KEY_KP_Left,
            Gdk.KEY_Return,
            Gdk.KEY_KP_Enter,
            Gdk.KEY_ISO_Enter,
            Gdk.KEY_F2,
        ):
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and not control:
            return True
        if keyval == Gdk.KEY_BackSpace and not self._search_query:
            return True
        if keyval == Gdk.KEY_Escape and self._search_query:
            return True
        return False

    def _append_printable(self, keyval: int, state: Gdk.ModifierType) -> bool:
        """Append one plain character to the column prefix.

        Control and Alt stay out so a shortcut is never also a search.
        A Space on an empty prefix stays out for the same reason: the
        window uses that key for file preview, and accepting it would
        start a search instead. ``keyval_to_unicode`` is what tests drive.
        Composed characters arrive through ``_on_search_commit`` instead.

        @tags: #scope/navigation #scope/file-ops #action/write #action/bind #scope/widget #type/helper
        """
        if state is None:
            state = 0
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        code = Gdk.keyval_to_unicode(keyval)
        if not code:
            return False
        text = chr(code)
        if not text.isprintable():
            return False
        if text == " " and not self._search_query:
            return False
        self._set_search_query(self._search_query + text)
        return True

    def _on_search_commit(self, _im, text: str) -> None:
        """Append the character the input method composed.

        A dead key followed by a letter never shows up as a single
        ``handle_key`` character. This commit is that character, and the
        key handler must not append again for the same event.

        @tags: #scope/navigation #action/write #action/bind #scope/widget
        """
        if self._rename_active() or not text:
            return
        self._set_search_query(self._search_query + text)

    def _on_search_realize(self, widget: Gtk.Widget) -> None:
        """Point the input method at the list window once it exists.

        @tags: #scope/navigation #scope/widget #action/bind #lifecycle/init
        """
        window = widget.get_window()
        if window is not None:
            self._search_im.set_client_window(window)

    def _on_search_unrealize(self, _widget: Gtk.Widget) -> None:
        """Drop the input-method window when the list goes away.

        @tags: #scope/navigation #scope/widget #action/bind #lifecycle/destroy
        """
        self._search_im.set_client_window(None)

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        """Route shortcuts, then let a dead key compose before a plain letter.

        A leading Space is left unconsumed so the window can still run
        another handler or the preview fallback. Consuming it here would
        hide the key from every parent.

        @tags: #scope/navigation #scope/widget #scope/file-ops #action/bind #type/helper
        """
        if self._rename_active():
            return False
        keyval = event.keyval
        state = event.state or 0
        # Escape clears a prefix even if a dead key is pending. BackSpace
        # cancels that dead key before it deletes a character.
        if keyval == Gdk.KEY_Escape and self._search_query:
            return self.handle_key(keyval, state)
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Escape):
            if self._search_im.filter_keypress(event):
                return True
            return self.handle_key(keyval, state)
        if self._is_command_key(keyval, state):
            return self.handle_key(keyval, state)
        # A leading Space must not reach the input method. That method
        # would commit " " and consume the key. The ListBox would then
        # activate the row. The flag stops that activation without
        # consuming the event, so it can still bubble to the window.
        if (
            keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space)
            and not (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK))
            and not self._search_query
        ):
            self._space_passthrough = True
            toplevel = self.get_toplevel()
            if isinstance(toplevel, Gtk.Window):
                toplevel._miller_space_column = self
            GLib.idle_add(self._clear_space_passthrough)
            return self.handle_key(keyval, state)
        if self._search_im.filter_keypress(event):
            return True
        return self.handle_key(keyval, state)

    def _clear_space_passthrough(self) -> bool:
        """Drop the Space guard after the rest of this event has run.

        The flag must not survive into Return or a double-click. Idle
        runs after GTK finishes dispatching the key. The window class
        handler consumes Space even when we return False, so the
        connect_after fallback may not run; if the marker is still
        ours, run that fallback here.

        @tags: #scope/widget #scope/navigation #scope/file-ops #action/clear #action/bind #action/navigate #type/helper #lifecycle/init
        """
        self._space_passthrough = False
        toplevel = self.get_toplevel()
        preview = getattr(toplevel, "_miller_space_preview", None)
        if (
            isinstance(toplevel, Gtk.Window)
            and getattr(toplevel, "_miller_space_column", None) is self
            and preview is not None
        ):
            preview()
        return False

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Open the activated row, unless this activation came from Space.

        @tags: #scope/navigation #scope/file-ops #action/navigate #scope/widget #action/bind #action/clear
        """
        # Space still reaches this signal through a second path besides
        # activate-cursor-row. Return and double-click do not set the flag.
        if getattr(self, "_space_passthrough", False):
            return
        entry = getattr(row, "file_entry", None)
        if entry is None or self._dead:
            return
        if entry.is_dir:
            self.on_activate_dir(self, entry)
            return
        # Single-click already opened the file on release. Activation is the
        # second press of a double-click, and opening here would launch twice.
        if self._click_policy() == "single":
            return
        _column_module().open_file_default(entry.path)

    def _click_policy(self) -> str:
        """Return ``single`` or ``double`` from the host hook.

        ``None`` stays on double-click so a column with no hook keeps the
        file-manager default: select on the first click, open on the second.

        @tags: #scope/navigation #scope/view-preference #action/read #type/helper
        """
        hook = self.file_click_policy
        if hook is None:
            return "double"
        return "single" if hook() == "single" else "double"

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        """Handle primary press bookkeeping and open the context menu on right click.

        @tags: #scope/widget #scope/navigation #scope/view-preference #scope/file-ops #action/bind #action/render #type/helper #complexity/high
        """
        if event.button == 1 and event.type in (
            Gdk.EventType._2BUTTON_PRESS,
            Gdk.EventType._3BUTTON_PRESS,
        ):
            self._primary_presses = 3 if event.type == Gdk.EventType._3BUTTON_PRESS else 2
            return False
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        row = self.listbox.get_row_at_y(int(event.y))
        self._press_row = row if row is not None and getattr(row, "file_entry", None) is not None else None
        # ListBox commits the row on release, not on press. An idle queued
        # here runs in between and the selection is thrown away, so the flag
        # stays set until the release handler clears it. A drag begin clears
        # the flag itself, because that release never arrives.
        if event.button == 1:
            self._selection_from_user = True
            self._primary_presses = 1
            # Shift+arrow reads the focused row. A click that only changes
            # the selection would otherwise extend from the previous cursor.
            if self._press_row is not None:
                self._press_row.grab_focus()
            return False
        if event.button != 3:
            return False
        if self._press_row is None:
            self._set_selection([], notify=True)
            self._popup_context_menu(event, background=True)
            return True
        if not self._press_row.is_selected():
            self._user_select_row(self._press_row)
        self._popup_context_menu(event)
        return True

    def _on_button_release(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        """_on_button_release.

        @tags: #scope/widget #action/bind #scope/navigation
        """
        if event.button != 1:
            return False
        GLib.idle_add(self._end_user_selection)
        self._maybe_open_file(event)
        return False

    def _maybe_open_file(self, event: Gdk.EventButton) -> None:
        """Open a file on the first plain release when policy is single.

        Ctrl/Shift clicks change the selection instead of launching. A
        second release belongs to the double-click and must not open again.
        Folders are not opened here; the selection handler appends a column.

        @tags: #scope/navigation #action/navigate #scope/file-ops
        """
        if self._primary_presses != 1 or event.type != Gdk.EventType.BUTTON_RELEASE:
            return
        state = event.state or 0
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            return
        if self._click_policy() != "single":
            return
        row = self.listbox.get_row_at_y(int(event.y))
        entry = getattr(row, "file_entry", None) if row is not None else None
        if entry is None or entry.is_dir:
            return
        _column_module().open_file_default(entry.path)

    def _activate_selected(self) -> None:
        """Open the selection.

        One folder asks the board to move right. Anything else opens each
        file and skips folders, including a mix of both.

        @tags: #scope/navigation #action/navigate #scope/widget
        """
        entries = self.get_selected_entries()
        if not entries:
            return
        if len(entries) == 1 and entries[0].is_dir:
            self.on_nav_right(self, entries[0])
            return
        for entry in entries:
            if not entry.is_dir:
                _column_module().open_file_default(entry.path)

    def _move_selection(self, delta: int) -> None:
        """Move the single selection by ``delta`` rows.

        ``_keyboard_select`` is set only around the select so the board can
        wait before listing a directory or building a file preview. Clicks
        and ``select_name`` leave the flag clear and open at once. The
        highlight itself is not delayed. Keypad Up/Down use this same path.

        Args:
            delta: ``-1`` toward the top, ``1`` toward the bottom.

        @tags: #scope/navigation #action/read #action/bind #scope/widget #type/helper #action/write
        """
        rows = self._visible_real_rows()
        if not rows:
            return
        current = self.listbox.get_selected_row()
        if current not in rows:
            target = rows[0] if delta > 0 else rows[-1]
        else:
            index = rows.index(current) + delta
            if index < 0 or index >= len(rows):
                return
            target = rows[index]
        self._keyboard_select = True
        try:
            self._user_select_row(target)
        finally:
            self._keyboard_select = False

    def _extend_selection(self, delta: int) -> None:
        """Grow the selection from the anchor toward ``delta``.

        The anchor is the row that was selected alone. Shift+arrow moves
        the other end. The board is told only when the set of rows changes.

        Args:
            delta: ``-1`` toward the top of the list, ``1`` toward the bottom.

        @tags: #scope/navigation #scope/file-ops #scope/widget #action/read #action/bind #type/helper #complexity/high
        """
        rows = self._visible_real_rows()
        if not rows:
            return
        anchor = self._anchor_row if self._anchor_row in rows else None
        if anchor is None:
            selected = [row for row in rows if row.is_selected()]
            anchor = selected[0] if selected else rows[0]
            self._anchor_row = anchor
        focused = self._focused_row()
        # Focus can still sit on a row the click just unselected. That row
        # is not an end of the selection, so it must not collapse the range.
        if focused in rows and focused.is_selected():
            cursor = focused
        else:
            selected = [row for row in rows if row.is_selected()]
            if delta > 0 and selected:
                cursor = selected[-1]
            elif selected:
                cursor = selected[0]
            else:
                cursor = anchor
        index = rows.index(cursor) + delta
        index = max(0, min(len(rows) - 1, index))
        cursor = rows[index]
        start = rows.index(anchor)
        lo, hi = (start, index) if start <= index else (index, start)
        chosen = rows[lo:hi + 1]
        current = [row for row in rows if row.is_selected()]
        if chosen != current:
            self._set_selection(chosen, notify=True)
        if self._owns_focus():
            cursor.grab_focus()

    def _select_all_real(self) -> None:
        """Select every visible real row. The empty placeholder stays unselected.

        A search hides the other names, and selecting them would copy or
        trash files the column is not showing.

        @tags: #scope/navigation #action/read #action/bind #scope/widget #type/helper
        """
        rows = self._visible_real_rows()
        if not rows:
            return
        if self._anchor_row not in rows:
            self._anchor_row = rows[0]
        self._set_selection(rows, notify=True)
