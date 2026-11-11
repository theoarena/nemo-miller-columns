"""GTK stylesheet for the Miller column UI.

Rules use only named theme colors so a dark theme and a light theme share
one stylesheet. The current column paints its selection as the theme accent
at 75% over ``@theme_base_color``. ``@theme_selected_fg_color`` was made for
the opaque accent, so on a light base that wash can be a pastel under white
text. ``bind_accent_ink`` adds ``mc-accent-ink`` on the board only when that
token contrasts better than ``@theme_fg_color`` on the washed color.

``install_theme`` is safe to call from every extension entry point: a second
call does not stack another provider on the screen.
"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

# Kept so a second install_theme() call can return without adding another
# provider. StyleContext stacks providers, and a duplicate is painful to remove.
# @tags: #type/constant #scope/widget
_installed_provider = None  # type: Gtk.CssProvider | None

# Painted in CSS and composited in ``selected_row_uses_accent_ink``. One
# number, or the class would answer for a different color than the row.
# @tags: #type/constant #scope/widget
_CURRENT_SELECTION_ALPHA = 0.75

# On ``mc-root`` when the opaque-accent ink wins on the washed accent.
# @tags: #type/constant #scope/widget
ACCENT_INK_CLASS = "mc-accent-ink"

_THEME_COLOR_NAMES = (
    "theme_base_color",
    "theme_selected_bg_color",
    "theme_fg_color",
    "theme_selected_fg_color",
)

# ``add_class`` emits ``style-updated``. The handler would re-enter and
# toggle the class forever.
_applying_accent_ink = set()  # type: set[int]

# Set on a board that already listens for ``style-updated``.
_BOUND_FLAG = "_mc_accent_ink_bound"


def build_css() -> str:
    """Return the Miller column stylesheet.

    Every selector hangs off an ``mc-`` class so these rules cannot restyle
    the rest of the Nemo window. Colors are theme tokens; a hardcoded hex
    would freeze the dark theme into the light palette. The current row
    defaults to ``@theme_fg_color``. ``mc-accent-ink`` on the board switches
    that row and its chevron to ``@theme_selected_fg_color``.

    Returns:
        A GTK 3 CSS document.

    @tags: #scope/widget #action/render #type/helper
    """
    css = """
.mc-root {
  background-color: @theme_base_color;
  color: @theme_fg_color;
}

.mc-scroll {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
}

.mc-column {
  min-width: 140px;
  background-color: @theme_base_color;
  color: @theme_fg_color;
  border-right-width: 1px;
  border-right-style: solid;
  border-right-color: alpha(@theme_fg_color, 0.12);  
}

.mc-column-resize-handle {
  background-color: transparent;
}

.mc-column-resize-handle:hover,
.mc-column-resize-handle:active,
.mc-column-resize-handle.mc-column-resize-handle--hot {
  background-color: @theme_selected_bg_color;
}

.mc-column-header {
  background-color: shade(@theme_bg_color, 0.96);
  color: @theme_fg_color;
  padding: 6px 10px;
  border-bottom-width: 1px;
  border-bottom-style: solid;
  border-bottom-color: alpha(@theme_fg_color, 0.065);
}

.mc-column-title {
  color: @theme_fg_color;
  font-weight: normal;
}

.mc-column-count {
  color: alpha(@theme_fg_color, 0.65);
}

.mc-column-search {
  background-color: @theme_base_color;
  color: @theme_fg_color;
  padding-top: 2px;
  padding-bottom: 2px;
  padding-left: 6px;
  padding-right: 8px;
  margin-top: 6px;
  margin-bottom: 2px;
  margin-left: 8px;
  margin-right: 8px;
  border-radius: 3px;
  border-width: 1px;
  border-style: solid;
  border-color: alpha(@theme_fg_color, 0.22);
}

.mc-column-search-icon {
  color: alpha(@theme_fg_color, 0.55);
}

.mc-list {
  background-color: @theme_base_color;
  color: @theme_fg_color;
  padding: 2pt;
}

.mc-row {
  background-color: @theme_base_color;
  color: @theme_fg_color;
  padding: 6pt 8pt;
  border-radius: 2pt;
  border-width: 0;
  outline-width: 0;
}

.mc-row+.mc-row{
  margin-top: 2px;
}

.mc-row:hover {
  background-color: alpha(@theme_selected_bg_color, 0.075);
  color: @theme_fg_color;
}

.mc-row:selected {
  background-color: alpha(@theme_selected_bg_color, 0.25);
  color: @theme_fg_color;
}

.mc-column-current .mc-row:selected {
  background-color: alpha(@theme_selected_bg_color, __SELECTION_ALPHA__);
  color: @theme_fg_color;
}

.mc-column-current .mc-row:selected label {
  color: @theme_fg_color;
}

.mc-root.__ACCENT_INK__ .mc-column-current .mc-row:selected,
.mc-root.__ACCENT_INK__ .mc-column-current .mc-row:selected label {
  color: @theme_selected_fg_color;
}

.mc-row:selected .mc-chevron {
  color: @theme_fg_color;
}

.mc-root.__ACCENT_INK__ .mc-column-current .mc-row:selected .mc-chevron {
  color: @theme_selected_fg_color;
}

.mc-chevron {
  color: alpha(@theme_fg_color, 0.55);
}

.mc-preview {
  background-color: @theme_base_color;
  color: @theme_fg_color;
}

.mc-preview-content {
  background-color: @theme_base_color;
  color: @theme_fg_color;  
}

.mc-preview-image {
  /* Black in both themes: a fg-colored shadow turns into a glow on dark. */
  border-radius: 12px;
  margin: 8px 8px 20px;
  box-shadow: 0 4px 16.1638px 0 alpha(rgb(0, 0, 0), 0.14), 0 2px 7.83618px 0 alpha(rgb(0, 0, 0), 0.15);
}

.mc-preview-text {
  background-color: shade(@theme_bg_color, 0.97);
  color: @theme_fg_color;
  border-radius: 8px;
  border-width: 1px;
  border-style: solid;
  border-color: alpha(@theme_fg_color, 0.16);
  padding: 8px;
  font-family: monospace;
}

.mc-preview-tile {
  border-radius: 16px;
}

.mc-preview-tile-accent {
  background-color: @theme_selected_bg_color;
  color: @theme_selected_fg_color;
}

.mc-preview-title {
  color: @theme_fg_color;
  font-weight: bold;
  font-size: 13pt;
}

.mc-preview-subtitle {
  color: alpha(@theme_fg_color, 0.7);
}

.mc-preview-section {
  color: alpha(@theme_fg_color, 0.65);
  font-weight: bold;
}

.mc-meta {
  background-color: @theme_base_color;
  color: @theme_fg_color;
  border-bottom-width: 1px;
  border-bottom-style: solid;
  border-bottom-color: alpha(@theme_fg_color, 0.12);
  padding-top: 6px;
  padding-bottom: 6px;
}

.mc-meta-label {
  color: alpha(@theme_fg_color, 0.65);
  min-width: 128px;
}

.mc-meta-value {
  color: @theme_fg_color;
}

.mc-empty {
  background-color: @theme_base_color;
  color: alpha(@theme_fg_color, 0.55);
}
"""
    return (
        css.replace("__SELECTION_ALPHA__", "%g" % _CURRENT_SELECTION_ALPHA)
        .replace("__ACCENT_INK__", ACCENT_INK_CLASS)
    )


def selected_row_uses_accent_ink(base, selected_bg, theme_fg, selected_fg) -> bool:
    """Whether the current row should use the opaque-accent text token.

    The row is painted with ``selected_bg`` at ``_CURRENT_SELECTION_ALPHA``
    over ``base``. ``selected_fg`` is the theme's ink for the opaque accent.
    It wins only when that ink contrasts more than ``theme_fg`` on the
    washed color. A tie keeps ``theme_fg``, the ink of every other row.

    Args:
        base: ``@theme_base_color`` under the row.
        selected_bg: ``@theme_selected_bg_color``, before the wash.
        theme_fg: ``@theme_fg_color``.
        selected_fg: ``@theme_selected_fg_color``.

    Returns:
        True when the board should carry ``mc-accent-ink``.

    @tags: #type/helper #action/render #scope/widget
    """
    surface = _composite_over(selected_bg, base, _CURRENT_SELECTION_ALPHA)
    accent = _contrast_ratio(selected_fg, surface)
    plain = _contrast_ratio(theme_fg, surface)
    return accent > plain


def accent_ink_for_context(context) -> bool:
    """Read the four theme colors and decide the current-row ink.

    A missing token leaves the class off. ``@theme_fg_color`` is already
    the color of the unselected rows, so the column stays readable.

    Returns:
        True when ``context`` should carry ``mc-accent-ink``.

    @tags: #type/helper #action/render #scope/widget
    """
    colors = [_lookup_theme_color(context, name) for name in _THEME_COLOR_NAMES]
    if any(color is None for color in colors):
        return False
    base, selected_bg, theme_fg, selected_fg = colors
    return selected_row_uses_accent_ink(base, selected_bg, theme_fg, selected_fg)


def bind_accent_ink(widget: Gtk.Widget) -> None:
    """Keep ``mc-accent-ink`` on ``widget`` in step with the theme.

    Call this on the board (``mc-root``). The choice is one per theme, and
    the stylesheet keys off that ancestor. ``style-updated`` covers a theme
    change. Toggling the class emits the same signal; the re-entry stops
    once the class matches.

    @tags: #scope/widget #action/bind #action/render
    """
    if getattr(widget, _BOUND_FLAG, False):
        _apply_accent_ink(widget)
        return
    setattr(widget, _BOUND_FLAG, True)
    widget.connect("style-updated", _on_style_updated)
    _apply_accent_ink(widget)


def _on_style_updated(widget: Gtk.Widget) -> None:
    """Re-read the theme after GTK restyles ``widget``.

    @tags: #scope/widget #action/render #action/bind
    """
    _apply_accent_ink(widget)


def _apply_accent_ink(widget: Gtk.Widget) -> None:
    """Add or remove ``mc-accent-ink`` when the contrast choice changed.

    @tags: #scope/widget #action/render
    """
    key = id(widget)
    if key in _applying_accent_ink:
        return
    context = widget.get_style_context()
    wants = accent_ink_for_context(context)
    has = context.has_class(ACCENT_INK_CLASS)
    if wants == has:
        return
    _applying_accent_ink.add(key)
    try:
        if wants:
            context.add_class(ACCENT_INK_CLASS)
        else:
            context.remove_class(ACCENT_INK_CLASS)
    finally:
        _applying_accent_ink.discard(key)


def _lookup_theme_color(context, name: str):
    """Return a named theme color, or None when this context has no such token.

    @tags: #type/helper #action/render
    """
    found, color = context.lookup_color(name)
    if not found:
        return None
    return color


def _composite_over(src: Gdk.RGBA, dst: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
    """Blend ``src`` over ``dst`` the way CSS ``alpha()`` paints a background.

    GTK stores theme colors in the sRGB channels Cairo blends. Linearizing
    before the blend would describe a different pixel than the row.

    @tags: #type/helper #action/render
    """
    mixed = Gdk.RGBA()
    mixed.red = src.red * alpha + dst.red * (1.0 - alpha)
    mixed.green = src.green * alpha + dst.green * (1.0 - alpha)
    mixed.blue = src.blue * alpha + dst.blue * (1.0 - alpha)
    mixed.alpha = 1.0
    return mixed


def _contrast_ratio(fg: Gdk.RGBA, bg: Gdk.RGBA) -> float:
    """WCAG contrast of ``fg`` on ``bg``.

    Luminance is linear. The channels passed in are still the sRGB values
    ``_composite_over`` produced.

    @tags: #type/helper #action/render
    """
    lighter = _relative_luminance(fg)
    darker = _relative_luminance(bg)
    if darker > lighter:
        lighter, darker = darker, lighter
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: Gdk.RGBA) -> float:
    """Relative luminance of an sRGB ``Gdk.RGBA``.

    @tags: #type/helper #action/render
    """
    red = _srgb_channel_to_linear(color.red)
    green = _srgb_channel_to_linear(color.green)
    blue = _srgb_channel_to_linear(color.blue)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _srgb_channel_to_linear(channel: float) -> float:
    """Undo sRGB encoding for one channel in 0..1.

    @tags: #type/helper #action/render
    """
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def install_theme() -> None:
    """Apply :func:`build_css` to the default screen.

    Does nothing when GTK has no default screen, so a headless import can
    call this without failing. A later call with a screen still installs
    once; the provider is remembered and not added again.

    @tags: #scope/widget #action/render #type/helper
    """
    global _installed_provider
    if _installed_provider is not None:
        return

    screen = Gdk.Screen.get_default()
    if screen is None:
        return

    provider = Gtk.CssProvider()
    provider.load_from_data(build_css().encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        screen,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _installed_provider = provider
