"""Nemo extension that packs Miller Columns into the active window slot.

Nemo asks every ``LocationWidgetProvider`` for a widget on each navigation
and drops whatever comes back into ``extra_location_widgets``. The dashboard
extension collapses that box, so this provider always returns None and packs
``MillerColumnsView`` (a ``Gtk.EventBox``) directly onto the slot instead.

A ``show`` handler on the slot's ``Gtk.Overlay`` hides it again while Miller
is active. The hide is scheduled at ``GLib.PRIORITY_HIGH_IDLE``, before GTK
paints. The dashboard helper skips its own ``show()`` when the slot is
already marked ``_miller_active``.

Arrow keys move the board immediately. Nemo is asked to follow once, after
``_NAVIGATE_DELAY_MS`` of quiet, and only while that slot is the active tab.
The ``get_widget`` call that comes back from that request is an echo and
does not move the board.

Navigation, the session flag, and the board live on the slot. Switching
tabs restores that slot instead of rebuilding whichever slot currently
holds a view. ``~/.dashboard`` is handled before any of that packing. The
home panel owns that location.

Used by: nemo-python, loaded from ``nemo_miller_columns.py``.
See also: ``miller.ui.board.MillerColumnsView``, ``miller.data``.
"""

from __future__ import annotations

import os
import sys

# This process may be on Wayland. Importing Gtk there can block in the
# display connection. XWayland is selected here, once, before gi loads.
# The column widgets do not choose a backend.
if os.environ.get("GDK_BACKEND") == "wayland":
    os.environ["GDK_BACKEND"] = "x11"

# A symlink under ~/.local/share/nemo-python/extensions/ must still import
# the ``miller`` package that lives next to the real file.
REAL_FILE_PATH = os.path.realpath(__file__)
BASE_DIR = os.path.dirname(REAL_FILE_PATH)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Nemo", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Nemo

import miller.data as data
from miller.i18n import _, install
from miller.ui.board import MillerColumnsView

# Frozen toolbar copy. Screen readers get this as the button name.
_TOOLTIP = "Miller Columns (Ctrl+4)"

_ICON_COLUMNS = "view-columns-symbolic"
_ICON_FALLBACK = "view-dual-symbolic"

# Nemo's own icon, list, and compact buttons. Miller is inserted after the
# last of these so it sits in the view group.
# @tags: #type/constant #scope/slot-lifecycle #scope/widget
_NATIVE_VIEW_ICONS = frozenset({
    "view-grid-symbolic",
    "view-list-symbolic",
    "view-compact-symbolic",
})

# @tags: #type/constant #scope/view-preference
_ENABLE_KEYS = frozenset({Gdk.KEY_4, Gdk.KEY_KP_4})
# @tags: #type/constant #scope/view-preference
_DISABLE_KEYS = frozenset({
    Gdk.KEY_1,
    Gdk.KEY_KP_1,
    Gdk.KEY_2,
    Gdk.KEY_KP_2,
    Gdk.KEY_3,
    Gdk.KEY_KP_3,
})

# Key repeat would otherwise ask Nemo to open every folder the highlight
# passes through. The board moves on the key; Nemo follows after a pause.
# @tags: #type/constant #scope/navigation
_NAVIGATE_DELAY_MS = 150


def _hide_overlay_later(overlay: Gtk.Overlay) -> bool:
    """Hide ``overlay`` before GTK paints and do not run again.

    GTK drops a ``hide()`` that runs inside the ``show`` handler, so this
    runs from idle. The caller schedules it at ``GLib.PRIORITY_HIGH_IDLE``,
    which is earlier than ``Gdk.PRIORITY_REDRAW``. A default idle runs after
    paint, and the native view is on screen for one frame.

    Returning false removes the source. ``Widget.hide`` returns None, which
    would leave the idle pending.

    @tags: #scope/slot-lifecycle #action/toggle #type/helper #lifecycle/destroy
    """
    overlay.hide()
    return False


def _on_overlay_show(overlay: Gtk.Overlay, slot: Gtk.Box) -> None:
    """Schedule a hide when Miller still owns this slot.

    The dashboard helper calls ``show()`` on every overlay while restoring
    a normal folder. Hiding synchronously here does not stick. The idle
    uses ``GLib.PRIORITY_HIGH_IDLE`` so the hide lands before
    ``Gdk.PRIORITY_REDRAW``.

    @tags: #scope/slot-lifecycle #action/bind #type/helper
    """
    if getattr(slot, "_miller_active", False):
        GLib.idle_add(
            _hide_overlay_later,
            overlay,
            priority=GLib.PRIORITY_HIGH_IDLE,
        )


class MillerColumnsExtension(GObject.GObject, Nemo.LocationWidgetProvider):
    """Packs the Miller column board onto a Nemo window slot.

    The board is not returned from ``get_widget``. Nemo would parent a
    returned widget in ``extra_location_widgets``, and the dashboard helper
    treats every non-toolbar ``Gtk.Box`` in that slot as that box.

    Attributes:
        None. nemo-python keeps one extension instance for every window.
        Navigation and the session flag live on each slot. The window
        keeps the toolbar, the slot that last reported a location, and a
        flag that ignores the native view radio while a tab is becoming
        active.

    @tags: #scope/slot-lifecycle #type/extension
    """

    def __init__(self) -> None:
        super().__init__()
        install()

    def get_widget(self, uri: str, window: Gtk.Window | None):
        """Prepare the slot for ``uri`` and return no location widget.

        Called when the location really changes, not on a tab switch. The
        slot is the one that just emitted ``location-changed``. A dashboard
        URI, a non-directory, or a folder whose preference is off leaves
        the native view in place on that slot only.

        The session flag is per slot and is only read here. Navigation
        must not overwrite a choice the user made with the toolbar or
        Ctrl+1 through Ctrl+4 on this slot. A new slot has no session
        flag, so it starts from the folder preference.

        A directory this slot just asked Nemo to open is an echo. The
        echo updates ``_miller_location`` and leaves the board where the
        user already moved it. Any other directory cancels a pending
        arrow navigation and repositions the board.

        The toolbar follows the active slot. A location change on a
        background tab does not flip it.

        Args:
            uri: Location Nemo is showing, as a URI or a local path.
            window: Host window. None when Nemo has not attached one.

        Returns:
            Always None. The column view, when shown, is packed on the slot.

        @tags: #scope/slot-lifecycle #scope/view-preference #scope/navigation #action/toggle
        """
        if window is None:
            return None
        self._ensure_toolbar(window)
        self._bind_window(window)
        for existing in self._slots(window):
            self._bind_slot(existing)
        reported = self._uri_path(uri)
        slot = self._slot_for_uri(window, reported)
        if data.is_dashboard_uri(uri):
            path = None
        else:
            path = self._local_directory(uri)
        # Echo: Nemo reporting a path this slot already emitted. Anything
        # else, including the dashboard, is the user going somewhere themselves.
        inflight = self._navigation_inflight(slot) if slot is not None else set()
        echo = path is not None and path in inflight
        if echo:
            inflight.discard(path)
        elif slot is not None:
            self._reset_navigation(slot)
        if path is None:
            if data.is_dashboard_uri(uri):
                self._leave_dashboard(window, slot)
            else:
                self._leave_non_directory(window, slot)
            return None
        if slot is None:
            return None
        slot._miller_location = path
        # An echo must not clobber the folder the keys have already moved
        # to. The toolbar reads the active slot's _miller_uri.
        if not echo:
            slot._miller_uri = path
        enabled = data.resolve_miller_view_preferred(
            path,
            inherited_state=getattr(slot, "_miller_session_enabled", None),
        )
        if enabled:
            self._activate(slot, path, window, reposition=not echo)
        else:
            self._deactivate(slot)
        if slot is self._active_slot(window):
            self._set_toggle_active(window, enabled)
        return None

    def handle_window_key(self, window: Gtk.Window, event) -> bool:
        """Switch the view from a window-level key press.

        Ctrl+4 (and keypad 4) turns Miller on for a normal directory.
        Ctrl+1, Ctrl+2, and Ctrl+3 turn it off and let Nemo's own view
        buttons win. The same chords on ``~/.dashboard`` or a non-directory
        are consumed and change nothing, matching the native view buttons
        on the home panel.

        Args:
            window: Window that received the event.
            event: Key event. ``state`` and ``keyval`` are read.

        Returns:
            True when Control is held and the key is 1, 2, 3, or 4.
            False for every other event so the focused column still sees it.

        @tags: #scope/view-preference #scope/widget #action/toggle
        """
        state = getattr(event, "state", 0) or 0
        # View switching is a window chord. Without Control the key belongs
        # to the focused column.
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        keyval = getattr(event, "keyval", 0)
        if keyval in _ENABLE_KEYS:
            if self._is_normal_directory(window):
                self._apply_user_choice(window, True)
            return True
        if keyval in _DISABLE_KEYS:
            if self._is_normal_directory(window):
                self._apply_user_choice(window, False)
            return True
        return False

    def _bind_window(self, window: Gtk.Window) -> None:
        """Connect the key handler and notebook page tracking once.

        A new tab is inserted into a ``Gtk.Notebook`` before Nemo loads
        it. ``page-added`` binds that slot so its ``location-changed`` is
        seen on the first load. Slots already in the tree are bound from
        ``get_widget``.

        @tags: #scope/widget #scope/navigation #action/bind #lifecycle/init
        """
        if getattr(window, "_miller_keys_bound", False):
            return
        window.connect(
            "key-press-event",
            lambda widget, event: self.handle_window_key(widget, event),
        )
        for widget in self._walk(window):
            if isinstance(widget, Gtk.Notebook):
                self._bind_notebook(widget)
        window._miller_keys_bound = True

    def _bind_notebook(self, notebook: Gtk.Notebook) -> None:
        """Bind slots added to ``notebook`` before they load a location.

        @tags: #scope/slot-lifecycle #action/bind
        """
        if getattr(notebook, "_miller_pages_bound", False):
            return
        notebook._miller_pages_bound = True
        notebook.connect("page-added", self._on_page_added)

    def _on_page_added(self, _notebook: Gtk.Notebook, child: Gtk.Widget, _page_num: int) -> None:
        """Bind the slot that was just inserted as a tab.

        @tags: #scope/slot-lifecycle #action/bind
        """
        if self._is_slot(child):
            self._bind_slot(child)
            return
        for widget in self._walk(child):
            if self._is_slot(widget):
                self._bind_slot(widget)

    def _bind_slot(self, slot: Gtk.Box) -> None:
        """Connect location, active, and destroy handlers once.

        A slot starts without ``_miller_session_enabled``. The first
        directory it shows follows the folder preference, not another
        tab's toolbar choice.

        ``location-changed`` runs immediately before ``get_widget`` and
        is the only synchronous way to know which slot is navigating.
        ``destroy`` drops the navigation timer so it cannot fire on a
        widget that is already gone.

        @tags: #scope/slot-lifecycle #scope/navigation #action/bind #lifecycle/destroy
        """
        if getattr(slot, "_miller_slot_bound", False):
            return
        slot._miller_slot_bound = True
        slot._miller_nav_source = 0
        slot._miller_nav_target = None
        slot._miller_nav_inflight = set()
        slot._miller_location = None
        slot.connect("location-changed", self._on_slot_location_changed)
        slot.connect("active", self._on_slot_active)
        slot.connect("inactive", self._on_slot_inactive)
        slot.connect("destroy", self._on_slot_destroy)

    def _on_slot_location_changed(self, slot: Gtk.Box, _from_uri: str | None, to_uri: str | None) -> None:
        """Remember which slot is navigating and the path it is opening.

        ``get_widget`` consumes this on the next call. Tab switches do
        not emit ``location-changed``, so they cannot steal the hint.

        @tags: #scope/navigation #scope/slot-lifecycle #action/bind
        """
        slot._miller_reported_path = self._uri_path(to_uri or "")
        window = slot.get_toplevel()
        if isinstance(window, Gtk.Window):
            window._miller_changed_slot = slot

    def _on_slot_active(self, slot: Gtk.Box) -> None:
        """Restore this slot's toolbar state and any paused navigation.

        Nemo's ``active`` class handler then flips the shared
        Icon/List/Compact radio to this tab's view. That flip is not a
        user choice. ``_miller_view_sync`` makes ``_on_native_toggled``
        ignore it. The flag stays set until a low-priority idle, which
        is after that class handler returns.

        @tags: #scope/slot-lifecycle #scope/navigation #action/toggle
        """
        window = slot.get_toplevel()
        if not isinstance(window, Gtk.Window):
            return
        window._miller_active_slot = slot
        window._miller_view_sync = True
        GLib.idle_add(
            self._clear_view_sync,
            window,
            priority=GLib.PRIORITY_LOW,
        )
        self._set_toggle_active(window, bool(getattr(slot, "_miller_active", False)))
        if getattr(slot, "_miller_view", None) is not None:
            GLib.idle_add(self._restore_focus, slot)
        target = getattr(slot, "_miller_nav_target", None)
        if target:
            self._schedule_navigation(slot, target)

    def _restore_focus(self, slot: Gtk.Box) -> bool:
        """Put keyboard focus back on the board's selected row.

        A tab switch grabs focus after ``active`` returns: Nemo focuses
        the hidden native view, then ``GtkNotebook`` restores the page's
        last focus child. A grab made inside ``active`` is overwritten
        and the arrows go to the invisible view, so the grab waits for
        an idle after the switch.

        Returns:
            False, so the idle source does not repeat.

        @tags: #scope/slot-lifecycle #scope/navigation #action/bind
        """
        view = getattr(slot, "_miller_view", None)
        window = slot.get_toplevel()
        if (
            view is not None
            and getattr(slot, "_miller_active", False)
            and isinstance(window, Gtk.Window)
            and getattr(window, "_miller_active_slot", None) is slot
        ):
            view.focus_active_column()
        return False

    def _clear_view_sync(self, window: Gtk.Window) -> bool:
        """Drop the tab-switch radio guard once ``active`` has finished.

        The native radio flips inside Nemo's ``active`` class handler.
        That handler is still pending when this idle is queued, because
        ``active`` runs user handlers first. Clearing the flag here, at
        ``GLib.PRIORITY_LOW``, leaves it set for those toggles and then
        lets a later click count as a user choice.

        Returns:
            False, so the idle source does not repeat.

        @tags: #scope/view-preference #action/clear
        """
        window._miller_view_sync = False
        return False

    def _on_slot_inactive(self, slot: Gtk.Box) -> None:
        """Pause navigation while this slot is not the one Nemo will open.

        ``open_in_same_window`` drives the active slot's location bar.
        Firing it for a background tab would move the visible tab. The
        pending target stays put and is scheduled again from ``active``.

        @tags: #scope/navigation #action/clear
        """
        self._disarm_navigation(slot)

    def _on_slot_destroy(self, slot: Gtk.Box) -> None:
        """Drop the navigation timer when the slot goes away.

        @tags: #scope/navigation #lifecycle/destroy #action/clear
        """
        self._disarm_navigation(slot)

    def _navigation_inflight(self, slot: Gtk.Box) -> set:
        """Return the set of paths already handed to Nemo for this slot.

        ``get_widget`` treats a path in this set as the echo of our own
        navigation. A missing set is created so callers can add to it.

        @tags: #scope/navigation #action/read #action/write #type/helper
        """
        inflight = getattr(slot, "_miller_nav_inflight", None)
        if inflight is None:
            inflight = set()
            slot._miller_nav_inflight = inflight
        return inflight

    def _schedule_navigation(self, slot: Gtk.Box, path: str) -> None:
        """Restart the navigation timer for ``path`` on ``slot``.

        Each arrow replaces the previous target. Nemo hears only the
        directory that is still current when ``_NAVIGATE_DELAY_MS``
        elapses. A background slot keeps the target and does not start
        a timer: ``open_in_same_window`` would move the visible tab.

        @tags: #scope/navigation #action/navigate #action/clear #type/helper
        """
        self._disarm_navigation(slot)
        slot._miller_nav_target = os.path.abspath(path)
        window = slot.get_toplevel()
        if isinstance(window, Gtk.Window) and slot is not self._active_slot(window):
            return
        slot._miller_nav_source = GLib.timeout_add(
            _NAVIGATE_DELAY_MS,
            self.flush_navigation,
            slot,
        )

    def flush_navigation(self, slot: Gtk.Box) -> bool:
        """Send the pending directory to Nemo, if it is not already there.

        Mirrors ``MillerColumnsView.flush_selection``: tests and deactivate
        call this instead of waiting out ``_NAVIGATE_DELAY_MS``. A target
        equal to ``_miller_location`` is dropped. That is Right and then
        Left back to the folder Nemo already shows.

        A slot that is not active does not open. The target stays pending
        so the next ``active`` can send it. Opening always drives the
        visible tab's location bar.

        Returns:
            False, so a timeout source that invoked this does not repeat.

        @tags: #scope/navigation #action/navigate #action/read #action/clear #type/helper
        """
        self._disarm_navigation(slot)
        target = getattr(slot, "_miller_nav_target", None)
        slot._miller_nav_target = None
        if not target:
            return False
        target = os.path.abspath(target)
        location = getattr(slot, "_miller_location", None)
        if location and os.path.abspath(location) == target:
            return False
        window = slot.get_toplevel()
        if not isinstance(window, Gtk.Window):
            return False
        if slot is not self._active_slot(window):
            slot._miller_nav_target = target
            return False
        self._navigation_inflight(slot).add(target)
        data.open_in_same_window(target, window)
        return False

    def _disarm_navigation(self, slot: Gtk.Box) -> None:
        """Remove the navigation timer and leave the pending target alone.

        This method is also used from the timeout callback. Removing the
        source that is dispatching the call warns, and returning false
        already drops it. An outside caller still has to remove a timer
        that has not fired.

        @tags: #scope/navigation #action/clear
        """
        source = getattr(slot, "_miller_nav_source", 0) or 0
        slot._miller_nav_source = 0
        if not source:
            return
        current = GLib.main_current_source()
        current_id = current.get_id() if current is not None else 0
        if source != current_id:
            GLib.source_remove(source)

    def _reset_navigation(self, slot: Gtk.Box) -> None:
        """Drop a pending navigation and any path still waiting for an echo.

        An external location change (sidebar, path bar, Back) wins over a
        target the keys had not sent yet.

        @tags: #scope/navigation #action/clear
        """
        self._disarm_navigation(slot)
        slot._miller_nav_target = None
        inflight = getattr(slot, "_miller_nav_inflight", None)
        if inflight is not None:
            inflight.clear()

    def _ensure_toolbar(self, window: Gtk.Window) -> None:
        """Insert the Miller toggle after Nemo's view buttons, once.

        Nemo 6 keeps icon, list, and compact as flat toggles inside a box,
        inside a tool item. Their icons are not on the tool item, so a
        scan of toolbar items never sees them.

        A window with no toolbar, or a toolbar with none of the native view
        icons, is left unchanged.

        @tags: #scope/slot-lifecycle #scope/widget #action/render
        """
        if getattr(window, "_miller_toggle", None) is not None:
            return
        toolbar = self._find_toolbar(window)
        if toolbar is None:
            return
        native = [
            widget
            for widget in self._walk(toolbar)
            if self._tool_icon_name(widget) in _NATIVE_VIEW_ICONS
        ]
        if not native:
            return
        last = native[-1]
        box = last.get_parent()
        if not isinstance(box, Gtk.Box):
            return
        index = box.get_children().index(last)
        icon = (
            _ICON_COLUMNS
            if Gtk.IconTheme.get_default().has_icon(_ICON_COLUMNS)
            else _ICON_FALLBACK
        )
        button = Gtk.ToggleButton()
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
        button.get_style_context().add_class(Gtk.STYLE_CLASS_FLAT)
        button.set_can_focus(False)
        button.set_tooltip_text(_(_TOOLTIP))
        box.pack_start(button, False, False, 0)
        box.reorder_child(button, index + 1)
        button.show_all()
        window._miller_toggle = button
        window._miller_native_buttons = native
        button.connect(
            "toggled",
            lambda toggle, host=window: self._on_miller_toggled(host, toggle),
        )
        for item in native:
            if getattr(item, "_miller_native_bound", False):
                continue
            item._miller_native_bound = True
            item.connect(
                "toggled",
                lambda toggle, host=window: self._on_native_toggled(host, toggle),
            )

    def _on_miller_toggled(self, window: Gtk.Window, button: Gtk.ToggleButton) -> None:
        """Apply a click on the Miller button.

        Programmatic ``set_active`` holds ``_miller_button_lock`` and must
        not be stored as a user choice. A dashboard URI or a non-directory
        cannot host the view, so the button is forced back off.

        @tags: #scope/view-preference #scope/slot-lifecycle #action/toggle
        """
        if getattr(window, "_miller_button_lock", False):
            return
        if not self._is_normal_directory(window):
            if button.get_active():
                self._set_toggle_active(window, False)
            return
        self._apply_user_choice(window, button.get_active())

    def _on_native_toggled(self, window: Gtk.Window, button) -> None:
        """Turn Miller off when a native view button becomes active.

        The dashboard location ignores these buttons the same way Nemo's
        own view buttons do nothing there. A button that is turning off
        is the other half of a radio group and is not a new choice.

        Tab activation sets ``_miller_view_sync`` while Nemo syncs the
        native radio to the incoming tab. That toggle is ignored so it
        cannot store a preference or tear a board down.

        @tags: #scope/view-preference #scope/slot-lifecycle #action/toggle
        """
        if getattr(window, "_miller_view_sync", False):
            return
        if getattr(window, "_miller_button_lock", False):
            return
        if not button.get_active():
            return
        if not self._is_normal_directory(window):
            return
        # The proxy was detached so it could look off. Activate the radio
        # now, or Nemo keeps the previous view when this button is clicked.
        action = getattr(button, "_miller_related_action", None)
        if action is not None:
            action.activate()
        self._apply_user_choice(window, False)

    def _apply_user_choice(self, window: Gtk.Window, enabled: bool) -> None:
        """Store one explicit view choice and show or hide the columns.

        The session flag changes only here (toolbar or Ctrl+1..Ctrl+4),
        and only on the active slot. Another tab keeps its own flag.
        The per-folder record is written only when Nemo is not ignoring
        view metadata. This method does not write GSettings.

        @tags: #scope/view-preference #scope/slot-lifecycle #action/write
        """
        enabled = bool(enabled)
        slot = self._active_slot(window)
        if slot is not None:
            slot._miller_session_enabled = enabled
        path = getattr(slot, "_miller_uri", None) if slot is not None else None
        if path and self._is_normal_directory(window):
            ignore_metadata, _inherit_viewer = data._preference_flags(None, None)
            if not ignore_metadata:
                data.set_miller_view_preferred(path, enabled)
            if slot is not None:
                if enabled:
                    self._activate(slot, path, window)
                else:
                    self._deactivate(slot)
        self._set_toggle_active(window, enabled and self._is_normal_directory(window))

    def _set_toggle_active(self, window: Gtk.Window, active: bool) -> None:
        """Sync the toolbar button without re-entering its toggled handler.

        The native view buttons are proxies for one ``GtkRadioAction`` group.
        ``set_active(False)`` on the current proxy snaps straight back, so
        while Miller is on the proxy is detached and only then cleared.
        The action itself stays put until a view button is clicked.

        @tags: #scope/widget #action/bind
        """
        active = bool(active)
        self._sync_native_view_buttons(window, suppressed=active)
        button = getattr(window, "_miller_toggle", None)
        if button is None or button.get_active() == active:
            return
        window._miller_button_lock = True
        try:
            button.set_active(active)
        finally:
            window._miller_button_lock = False

    def _sync_native_view_buttons(self, window: Gtk.Window, suppressed: bool) -> None:
        """Show only the Miller button, or hand the view buttons back to Nemo.

        Detach happens before ``set_active(False)``. Doing it the other way
        round lets the radio action turn the same button on again, and that
        second ``toggled`` is read as "leave Miller".
        ``set_related_action(None)`` is rejected by the binding, so the
        property is cleared instead. Putting the action back stamps its
        label onto the button; the label is cleared again so the toolbar
        stays icon-only.

        @tags: #scope/widget #scope/view-preference #action/toggle
        """
        buttons = getattr(window, "_miller_native_buttons", None) or []
        window._miller_button_lock = True
        try:
            for button in buttons:
                if suppressed:
                    held = getattr(button, "_miller_related_action", None)
                    getter = getattr(button, "get_related_action", None)
                    if held is None and getter is not None:
                        action = getter()
                        if action is not None:
                            button._miller_related_action = action
                            button.set_property("related-action", None)
                    if button.get_active():
                        button.set_active(False)
                    continue
                action = getattr(button, "_miller_related_action", None)
                if action is None:
                    continue
                # Reconnect copies the action's label ("Ícones") and hides
                # the image. Nemo clears that label after the first connect.
                button.set_use_action_appearance(False)
                button.set_related_action(action)
                button.set_property("label", None)
                button.set_always_show_image(True)
                button._miller_related_action = None
        finally:
            window._miller_button_lock = False

    def _leave_dashboard(self, window: Gtk.Window, slot: Gtk.Box | None) -> None:
        """Drop Miller on the slot that opened the panel.

        Other tabs keep their boards. The widget sitting in ``extra_box``
        belongs to the dashboard extension. This path does not hide,
        remove, or pack into that box. The toolbar moves only when this
        slot is the one on screen.

        @tags: #scope/slot-lifecycle #scope/navigation #action/toggle
        """
        if slot is not None:
            if getattr(slot, "_miller_active", False):
                self._deactivate(slot)
            slot._miller_uri = data.DASHBOARD_PATH
        if slot is None or slot is self._active_slot(window):
            self._set_toggle_active(window, False)

    def _leave_non_directory(self, window: Gtk.Window, slot: Gtk.Box | None) -> None:
        """Remove Miller from the slot that left a normal directory.

        Other tabs are left alone. The toolbar is cleared only when the
        slot is the active one.

        @tags: #scope/slot-lifecycle #scope/navigation #action/toggle
        """
        if slot is not None:
            slot._miller_uri = None
            self._deactivate(slot)
        if slot is None or slot is self._active_slot(window):
            self._set_toggle_active(window, False)

    def _activate(
        self,
        slot: Gtk.Box,
        path: str,
        window: Gtk.Window,
        reposition: bool = True,
    ) -> None:
        """Show Miller for ``path`` on ``slot``, reusing the existing board.

        Nemo calls ``get_widget`` again after an in-window navigation. A new
        board would wipe the column chain the user just built, so an
        existing ``_miller_view`` only moves its root.

        ``reposition`` is false for the echo of a navigation this extension
        just sent. The board is already standing on the folder the user
        moved to; ``set_root_path`` would pull focus back and prune the
        column they opened next.

        Args:
            slot: Nemo window slot that hosts the board.
            path: Directory to show.
            window: Host window passed through to the board.
            reposition: When false, leave an existing board where it is.

        @tags: #scope/slot-lifecycle #action/toggle #scope/navigation
        """
        path = os.path.abspath(path)
        view = getattr(slot, "_miller_view", None)
        created = view is None
        if created:
            # The constructor emits the current path. Matching _miller_uri
            # makes that emission a no-op instead of a second navigation.
            slot._miller_uri = path
            view = MillerColumnsView(
                path,
                target_window=window,
                on_active_path_changed=lambda changed, host=slot: self._on_active_path(host, changed),
            )
            slot._miller_view = view
            # EventBox, not Box. A Box wrapper would be detected as
            # extra_location_widgets and collapsed by the dashboard helper.
            slot.pack_start(view, True, True, 0)
            self._attach_hidden_sync(view)
        elif reposition:
            # set_root_path emits too. Set the uri first so the callback
            # does not schedule Nemo to open the folder it just opened.
            slot._miller_uri = path
            view.set_root_path(path)
        overlay = self._slot_overlay(slot)
        self._guard_overlay(overlay, slot)
        # The guard hides a show that arrives while this flag is true, so
        # the flag has to be set before the first hide and cleared before
        # a later show that means "restore the native view".
        slot._miller_active = True
        if overlay is not None:
            overlay.hide()
        # An existing board is already visible. Columns it appends call
        # show_all themselves. Showing the whole board again is redundant.
        if created:
            view.show_all()
        # A background tab must not pull the keyboard off the visible one.
        # Switching back focuses the board from the active handler.
        if slot is self._active_slot(window):
            view.focus_active_column()

    def _deactivate(self, slot: Gtk.Box) -> None:
        """Destroy the board on ``slot`` and show the native overlay again.

        A pending arrow navigation is sent first. Turning the view off
        then opens the native folder the columns were showing, which may
        be ahead of the location Nemo last finished opening.

        @tags: #scope/slot-lifecycle #action/toggle #lifecycle/destroy
        """
        self.flush_navigation(slot)
        slot._miller_active = False
        view = getattr(slot, "_miller_view", None)
        if view is not None:
            view.hide()
            if view.get_parent() is slot:
                slot.remove(view)
            view.destroy()
            slot._miller_view = None
        overlay = self._slot_overlay(slot)
        if overlay is not None:
            overlay.set_hexpand(True)
            overlay.set_vexpand(True)
            overlay.show()

    def _guard_overlay(self, overlay: Gtk.Overlay | None, slot: Gtk.Box) -> None:
        """Connect the overlay show guard once.

        @tags: #scope/slot-lifecycle #action/bind
        """
        if overlay is None or getattr(overlay, "_miller_show_guard", False):
            return
        overlay._miller_show_guard = True
        overlay.connect("show", _on_overlay_show, slot)

    def _on_active_path(self, slot: Gtk.Box, path: str) -> None:
        """Remember the focused directory and ask Nemo to follow.

        The board reports its initial path immediately. That report matches
        the slot's ``_miller_uri`` and must not navigate. A later directory
        is stored on the slot at once, so the toolbar and keys act on the
        folder the user sees while this slot is active. Nemo is asked
        after ``_NAVIGATE_DELAY_MS``.

        @tags: #scope/navigation #action/navigate
        """
        path = os.path.abspath(path)
        current = getattr(slot, "_miller_uri", None)
        if current and os.path.abspath(current) == path:
            return
        slot._miller_uri = path
        self._schedule_navigation(slot, path)

    def _attach_hidden_sync(self, view: MillerColumnsView) -> None:
        """Keep column dotfile visibility aligned with Nemo's preference.

        Columns are born with ``show_hidden`` false and Ctrl+H flips only
        the focused column. The same wrapper installs ``file_click_policy``
        so the column modules stay free of GSettings.

        @tags: #scope/view-preference #action/bind #side-effect/gsettings
        """
        if getattr(view, "_miller_hidden_sync", False):
            return
        view._miller_hidden_sync = True
        original_make = view._make_column

        def make_column(path: str, _original=original_make) -> object:
            """Wrap column construction to sync show-hidden preference.

            @tags: #scope/view-preference #action/bind #side-effect/gsettings
            """
            column = _original(path)
            self._wrap_column_key(view, column)
            column.set_show_hidden(self._show_hidden_preference())
            column.file_click_policy = self._click_policy
            return column

        view._make_column = make_column
        for column in list(view.columns):
            self._wrap_column_key(view, column)
            column.set_show_hidden(self._show_hidden_preference())
            column.file_click_policy = self._click_policy
        self._watch_show_hidden(view)

    def _wrap_column_key(self, view: MillerColumnsView, column) -> None:
        """Publish Ctrl+H from this column to Nemo and the sibling columns.

        @tags: #scope/view-preference #action/bind #side-effect/gsettings
        """
        if getattr(column, "_miller_key_wrapped", False):
            return
        column._miller_key_wrapped = True
        original = column.handle_key

        def handle_key(keyval: int, state: int = 0, _column=column, _original=original):
            """Forward keys and publish Ctrl+H to GSettings and sibling columns.

            @tags: #scope/view-preference #action/bind #side-effect/gsettings
            """
            handled = _original(keyval, state)
            modifiers = 0 if state is None else state
            control = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)
            shift = bool(modifiers & Gdk.ModifierType.SHIFT_MASK)
            # Ctrl+H toggles only this column. Shift would be a different
            # chord, so it must not write the file-manager preference.
            if handled and control and not shift and keyval in (Gdk.KEY_h, Gdk.KEY_H):
                self._publish_show_hidden(view, _column)
            return handled

        column.handle_key = handle_key

    def _publish_show_hidden(self, view: MillerColumnsView, source) -> None:
        """Write ``show-hidden-files`` and copy the value to other columns.

        A missing schema skips the write. The sibling columns still follow
        the column the user just toggled.

        @tags: #scope/view-preference #action/write #side-effect/gsettings #scope/widget
        """
        value = bool(source.show_hidden)
        try:
            settings = Gio.Settings.new("org.nemo.preferences")
            settings.set_boolean("show-hidden-files", value)
        except Exception:
            pass
        for column in list(view.columns):
            if column is source or getattr(column, "_dead", False):
                continue
            column.set_show_hidden(value)

    def _watch_show_hidden(self, view: MillerColumnsView) -> None:
        """Push ``show-hidden-files`` into the live columns until the view dies.

        @tags: #scope/view-preference #action/bind #side-effect/gsettings #lifecycle/destroy
        """
        try:
            settings = Gio.Settings.new("org.nemo.preferences")
            handler_id = settings.connect(
                "changed::show-hidden-files",
                self._on_show_hidden_changed,
                view,
            )
        except Exception:
            return
        view._miller_hidden_settings = settings
        view._miller_hidden_handler = handler_id

        def on_destroy(_widget, _settings=settings, _handler=handler_id) -> None:
            """Disconnect GSettings handler when the board is destroyed.

            @tags: #scope/view-preference #lifecycle/destroy #action/bind
            """
            try:
                _settings.disconnect(_handler)
            except Exception:
                return

        view.connect("destroy", on_destroy)

    def _on_show_hidden_changed(self, settings: Gio.Settings, _key: str, view: MillerColumnsView) -> None:
        """Apply a preference change to columns that are still on screen.

        @tags: #scope/view-preference #action/bind #side-effect/gsettings #scope/widget
        """
        if getattr(view, "_dead", False):
            return
        try:
            value = bool(settings.get_boolean("show-hidden-files"))
        except Exception:
            return
        for column in list(getattr(view, "columns", ())):
            if getattr(column, "_dead", False):
                continue
            column.set_show_hidden(value)

    def _show_hidden_preference(self) -> bool:
        """Return Nemo's show-hidden-files flag, or false when it cannot be read.

        @tags: #scope/view-preference #action/read #side-effect/gsettings
        """
        try:
            settings = Gio.Settings.new("org.nemo.preferences")
            return bool(settings.get_boolean("show-hidden-files"))
        except Exception:
            return False

    def _click_policy(self) -> str:
        """Return Nemo's click-policy, or ``double`` when it cannot be read.

        Columns call this with no arguments. ``single`` opens a file on
        the first click. Anything else, including a missing schema, keeps
        the double-click default. The column modules do not open GSettings.

        @tags: #scope/view-preference #action/read #side-effect/gsettings
        """
        try:
            settings = Gio.Settings.new("org.nemo.preferences")
            value = settings.get_string("click-policy")
        except Exception:
            return "double"
        if value in ("single", "double"):
            return value
        return "double"

    def _uri_path(self, uri: str) -> str | None:
        """Return the absolute local path of ``uri``, or None.

        Unlike ``_local_directory`` this does not require a directory.
        ``location-changed`` and ``get_widget`` both use it so a reported
        path can be matched to the URI Nemo passes in.

        @tags: #scope/navigation #action/read #type/helper
        """
        if not uri:
            return None
        if "://" in uri:
            path = Gio.File.new_for_uri(uri).get_path()
            if not path:
                return None
            return os.path.abspath(path)
        return os.path.abspath(os.path.expanduser(uri))

    def _local_directory(self, uri: str) -> str | None:
        """Return an absolute local directory, or None for anything else.

        ``trash://`` and ``recent://`` have no local path. A path that is
        not a directory cannot host a column.

        @tags: #scope/navigation #action/read #type/helper
        """
        path = self._uri_path(uri)
        if path is None or not os.path.isdir(path):
            return None
        return path

    def _is_normal_directory(self, window: Gtk.Window) -> bool:
        """Return whether the active slot can show Miller columns.

        The dashboard path is a directory on disk and is still rejected:
        the home panel owns it. A missing URI (trash, recent) is rejected
        with the same answer.

        @tags: #scope/navigation #scope/view-preference #action/read
        """
        slot = self._active_slot(window)
        path = getattr(slot, "_miller_uri", None) if slot is not None else None
        if not path or data.is_dashboard_uri(path):
            return False
        return os.path.isdir(path)

    def _find_toolbar(self, window: Gtk.Window) -> Gtk.Toolbar | None:
        """Find the first toolbar in the window widget tree.

        @tags: #scope/slot-lifecycle #action/find #type/helper
        """
        for widget in self._walk(window):
            if isinstance(widget, Gtk.Toolbar):
                return widget
        return None

    def _slots(self, window: Gtk.Window) -> list:
        """Collect every Nemo slot widget in the window.

        @tags: #scope/slot-lifecycle #action/find #type/helper
        """
        return [widget for widget in self._walk(window) if self._is_slot(widget)]

    def _slot_for_uri(self, window: Gtk.Window, path: str | None) -> Gtk.Box | None:
        """Return the slot whose location just became ``path``.

        ``location-changed`` stashes the slot on the window immediately
        before ``get_widget``. That hint wins when the reported path
        matches, and is then cleared so the next navigation cannot reuse
        it. A hint that does not match is cleared too: it belonged to
        this call. Otherwise the slot that already reported ``path``
        wins, with the active slot preferred when two tabs show the same
        folder. With no report, the active slot is the one on screen.

        @tags: #scope/slot-lifecycle #action/find
        """
        changed = getattr(window, "_miller_changed_slot", None)
        if changed is not None and self._reported_path_matches(changed, path):
            window._miller_changed_slot = None
            return changed
        if changed is not None:
            window._miller_changed_slot = None
        matches = [
            slot
            for slot in self._slots(window)
            if self._reported_path_matches(slot, path)
        ]
        if matches:
            active = self._active_slot(window)
            for slot in matches:
                if slot is active:
                    return slot
            return matches[0]
        return self._active_slot(window)

    def _reported_path_matches(self, slot: Gtk.Box, path: str | None) -> bool:
        """Return whether ``slot`` last reported ``path``.

        @tags: #scope/navigation #action/read #type/helper
        """
        if not path:
            return False
        reported = getattr(slot, "_miller_reported_path", None)
        if not reported:
            return False
        return os.path.abspath(reported) == os.path.abspath(path)

    def _active_slot(self, window: Gtk.Window) -> Gtk.Box | None:
        """Return the slot the toolbar and key chords should act on.

        The last slot that emitted ``active`` wins. Before any tab has
        been activated, use the current page of the notebook that holds
        the focus, then the first slot in the window.

        @tags: #scope/slot-lifecycle #action/find
        """
        slots = self._slots(window)
        tracked = getattr(window, "_miller_active_slot", None)
        if tracked is not None and any(tracked is slot for slot in slots):
            return tracked
        focus = window.get_focus() if window is not None else None
        widget = focus
        while widget is not None:
            if isinstance(widget, Gtk.Notebook):
                index = widget.get_current_page()
                if index >= 0:
                    found = self._slot_from_page(widget.get_nth_page(index))
                    if found is not None:
                        return found
                break
            widget = widget.get_parent()
        if slots:
            return slots[0]
        return None

    def _slot_from_page(self, page: Gtk.Widget | None) -> Gtk.Box | None:
        """Return the slot packed as ``page``, or the first slot inside it.

        @tags: #scope/slot-lifecycle #action/find #type/helper
        """
        if page is None:
            return None
        if self._is_slot(page):
            return page
        for widget in self._walk(page):
            if self._is_slot(widget):
                return widget
        return None

    def _walk(self, root: Gtk.Widget):
        """Yield widgets in tree order, without entering the Miller board.

        The board contains ordinary boxes. Walking into it would not match
        a slot type name, and it would also observe widgets the extension
        does not own.

        @tags: #scope/slot-lifecycle #action/walk #type/helper
        """
        stack = [root]
        while stack:
            widget = stack.pop()
            yield widget
            if isinstance(widget, MillerColumnsView):
                continue
            if isinstance(widget, Gtk.Container):
                stack.extend(reversed(widget.get_children()))

    def _is_slot(self, widget: Gtk.Widget) -> bool:
        """Return whether ``widget`` is a Nemo window slot.

        Slots are boxes whose GObject type name contains ``Slot``. The
        column strip is a box too, but its type name does not.

        @tags: #scope/slot-lifecycle #type/helper
        """
        if not isinstance(widget, Gtk.Box):
            return False
        try:
            name = GObject.type_name(widget)
        except Exception:
            return False
        return bool(name) and "Slot" in name

    def _slot_overlay(self, slot: Gtk.Box) -> Gtk.Overlay | None:
        """Return the slot's native view overlay, if present.

        @tags: #scope/slot-lifecycle #type/helper
        """
        for child in slot.get_children():
            if isinstance(child, Gtk.Overlay):
                return child
        return None

    def _tool_icon_name(self, item: Gtk.Widget) -> str | None:
        """Return the icon name painted on a toolbar button.

        Nemo 6 stores that name on the button image. ``Image.get_icon_name``
        returns ``(name, size)``, which is not one of the view-icon strings.
        Widgets whose ``get_icon_name`` is not the tool-button method, such
        as the location entry, are skipped.

        @tags: #scope/widget #type/helper
        """
        getter = getattr(item, "get_icon_name", None)
        # The location entry lives in this toolbar. Entry.get_icon_name
        # needs an icon position; calling it with none aborts get_widget
        # before the key handler is bound, so Ctrl+4 never arrives.
        if getter is not None and not isinstance(item, Gtk.Entry):
            try:
                name = getter()
            except TypeError:
                name = None
            if isinstance(name, str) and name:
                return name
        for accessor in ("get_icon_widget", "get_image"):
            widget_getter = getattr(item, accessor, None)
            if widget_getter is None:
                continue
            widget = widget_getter()
            if not isinstance(widget, Gtk.Image):
                continue
            named = widget.get_icon_name()
            if isinstance(named, tuple):
                named = named[0]
            if named:
                return named
        return None
