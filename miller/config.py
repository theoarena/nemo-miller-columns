"""Timing and layout knobs for Miller Columns.

Tune delays, debounce, paint chunks, and widget sizes here. Stylesheet
tokens stay in ``miller.theme``. View preference keys and the JSON store
path stay in ``miller.preference``.
"""

from __future__ import annotations

# Host waits this long after Right / folder click before asking Nemo to
# navigate, so key repeat does not open every folder on the way.
#
# @tags: #type/constant #format/int #scope/navigation #scope/shared #action/navigate
NAVIGATE_DELAY_MS = 180

# Board waits this long after Up/Down onto a directory before listing the
# child column. Clicks skip this timer; keyboard file rows use
# ``KEYBOARD_PREVIEW_DELAY_MS`` instead.
#
# @tags: #type/constant #format/int #scope/navigation #scope/widget #scope/shared #action/bind
KEYBOARD_DIR_DELAY_MS = 180

# Board waits this long after Up/Down onto a file before building the
# preview. Clicks, multi-select, Right, and Enter skip this timer.
#
# @tags: #type/constant #format/int #scope/navigation #scope/widget #scope/shared #action/bind
KEYBOARD_PREVIEW_DELAY_MS = 180

# Column waits for directory-monitor events to settle before one reload.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #action/bind #side-effect/filesystem
MONITOR_DEBOUNCE_MS = 180

# Milliseconds between row-build slices so the main loop can paint and
# poll keys between chunks.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #action/render #complexity/idle
ROW_BUILD_INTERVAL_MS = 16

# Rows appended per paint slice on the GTK thread.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #action/render #complexity/high
ROW_BUILD_CHUNK = 40

# Preferred width of one directory column. Stylesheet floor is separate.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #type/widget
COLUMN_WIDTH = 240

# Bounds for a user-resized column. The stylesheet ``min-width`` must match
# ``COLUMN_MIN_WIDTH`` or GTK would refuse to shrink below it.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #type/widget
COLUMN_MIN_WIDTH = 140

# @tags: #type/constant #format/int #scope/widget #scope/shared #type/widget
COLUMN_MAX_WIDTH = 600

# Width in pixels of the draggable strip on a column's right edge.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #type/widget
COLUMN_RESIZE_HANDLE_WIDTH = 6

# Minimum reported by the board scrolled window so Nemo can shrink.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #type/widget
BOARD_MIN_SIZE = 100

# Fixed preview summary width; the pane may be wider.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #type/widget
PREVIEW_CONTENT_WIDTH = 360

# Margin around the preview summary box.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #type/widget
PREVIEW_CONTENT_MARGIN = 24

# Max image size inside the preview pane.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #type/widget
IMAGE_FIT_WIDTH = 320
IMAGE_FIT_HEIGHT = 240

# Pixbuf corner clip; keep in sync with `.mc-preview-image` border-radius.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #action/render
IMAGE_CORNER_RADIUS = 12

# Accent tile behind a large preview icon.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #action/render #type/widget
TILE_SIZE = 160

# Large preview / menu-fallback icon pixel size.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #action/render
LARGE_ICON = 96

# Metadata caption column width in the preview.
#
# @tags: #type/constant #format/int #scope/widget #scope/file-ops #scope/shared #action/render
CAPTION_WIDTH = 128

# Column list row and search chip icons.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #action/render
ROW_ICON_SIZE = 16

# Emblem badges drawn on directory row overlays.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #action/render
EMBLEM_ICON_SIZE = 8

# Context-menu and Open With item icons.
#
# @tags: #type/constant #format/int #scope/widget #scope/shared #action/render
MENU_ICON_SIZE = 16
