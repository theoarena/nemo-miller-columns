"""File preview column for the Miller board.

Sits at the trailing edge of the column strip, outside the directory chain.
The pane stretches to the leftover width; the summary itself stays in a
fixed-width box so the title and metadata start at the same x no matter
how wide the window is. ``update_file`` rebuilds that box from
``get_file_metadata``.

GTK 3 only. Theme colors come from ``miller.theme``. A full-color icon's
tile tint is computed per icon, so it is not a stylesheet token.
"""

from __future__ import annotations

import math
from typing import Optional

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from miller.data import format_size, get_file_metadata
from miller.i18n import _, ngettext

# The summary does not grow with the pane. 360 is wide enough for a 320px
# image and a caption column without the path pushing the columns aside.
# @tags: #type/constant #scope/widget
_PREVIEW_CONTENT_WIDTH = 360
_PREVIEW_CONTENT_MARGIN = 24
_IMAGE_FIT_WIDTH = 320
_IMAGE_FIT_HEIGHT = 240
# Same curve as `.mc-preview-image` border-radius. GTK does not mask the
# image child to that radius, so the pixbuf is clipped to match the shadow.
# @tags: #type/constant #scope/widget
_IMAGE_CORNER_RADIUS = 12
_TILE_SIZE = 160
_LARGE_ICON = 96
# Below this alpha-weighted saturation the icon is a grey glyph. A tint
# sampled from it would be mud, so the tile uses the theme accent instead.
# @tags: #type/constant #scope/widget
_NEAR_GREY_SATURATION = 0.12

# Label, then metadata key. Empty values are skipped.
# Full caption list; preview omits Type and Path: the heading already
# shows the type, and the path is available elsewhere.
# @tags: #type/constant #scope/file-ops #scope/widget
_METADATA_FIELDS = (
    ("Type", "mime_description"),
    ("Size", "size_human"),
    ("Created", "ctime_human"),
    ("Modified", "mtime_human"),
    ("Last access", "atime_human"),
    ("Permissions", "permissions"),
    ("Path", "path"),
)
# @tags: #type/constant #scope/file-ops #scope/widget #scope/view-preference
_PREVIEW_METADATA_FIELDS = tuple(
    (caption, key)
    for caption, key in _METADATA_FIELDS
    if key not in ("mime_description", "path")
)
_CAPTION_WIDTH = 128


def _round_pixbuf(pixbuf: GdkPixbuf.Pixbuf, radius: int) -> GdkPixbuf.Pixbuf:
    """Clip pixbuf corners so they follow the preview drop shadow.

    GTK paints the image on top of the frame and does not clip it to the
    frame's border-radius, so a square pixbuf would cover the shadow curve.

    @tags: #scope/widget #action/render #type/helper
    """
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    radius = max(0, min(int(radius), width // 2, height // 2))
    if radius == 0:
        return pixbuf
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    context.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
    context.arc(width - radius, radius, radius, 3 * math.pi / 2, 0)
    context.arc(width - radius, height - radius, radius, 0, math.pi / 2)
    context.arc(radius, height - radius, radius, math.pi / 2, math.pi)
    context.close_path()
    context.clip()
    Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
    context.paint()
    rounded = Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
    return rounded if rounded is not None else pixbuf


def icon_tint(pixbuf: GdkPixbuf.Pixbuf) -> Optional[tuple[int, int, int]]:
    """Return a tint sampled from the icon, or None when it is near grey.

    Each pixel is weighted by opacity and saturation, so transparent
    padding and grey outlines do not pull the color toward white. A
    symbolic or flat-grey icon has nothing to sample; the caller paints
    the theme accent instead of inventing a hue.

    Args:
        pixbuf: Icon pixels. 8-bit RGB, with or without alpha.

    Returns:
        ``(red, green, blue)`` in 0–255, or None.

    @tags: #scope/widget #scope/file-ops #action/read #action/render #type/helper
    """
    if pixbuf.get_bits_per_sample() != 8 or pixbuf.get_colorspace() != GdkPixbuf.Colorspace.RGB:
        return None
    channels = pixbuf.get_n_channels()
    if channels < 3:
        return None
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    rowstride = pixbuf.get_rowstride()
    raw = pixbuf.get_pixels()
    has_alpha = bool(pixbuf.get_has_alpha()) and channels >= 4
    red = green = blue = 0.0
    weight = 0.0
    alpha_sum = 0.0
    saturation_sum = 0.0
    for y in range(height):
        row = y * rowstride
        for x in range(width):
            offset = row + x * channels
            red_byte = raw[offset]
            green_byte = raw[offset + 1]
            blue_byte = raw[offset + 2]
            alpha = raw[offset + 3] / 255.0 if has_alpha else 1.0
            if alpha <= 0.0:
                continue
            alpha_sum += alpha
            peak = red_byte if red_byte > green_byte else green_byte
            if blue_byte > peak:
                peak = blue_byte
            floor = red_byte if red_byte < green_byte else green_byte
            if blue_byte < floor:
                floor = blue_byte
            saturation = 0.0 if peak == 0 else (peak - floor) / float(peak)
            saturation_sum += saturation * alpha
            sample = alpha * saturation
            if sample <= 0.0:
                continue
            red += red_byte * sample
            green += green_byte * sample
            blue += blue_byte * sample
            weight += sample
    if alpha_sum <= 0.0 or weight <= 0.0:
        return None
    if saturation_sum / alpha_sum < _NEAR_GREY_SATURATION:
        return None
    scale = 1.0 / weight
    return (
        max(0, min(255, int(round(red * scale)))),
        max(0, min(255, int(round(green * scale)))),
        max(0, min(255, int(round(blue * scale)))),
    )


def pack_metadata_rows(
    box: Gtk.Box,
    meta: dict,
    heading: str = "INFORMATION",
    fields=_METADATA_FIELDS,
) -> None:
    """Pack a metadata block into ``box``.

    Rows with an empty value are omitted so a missing birth time does not
    render a blank line. The path row is a single ellipsized line because
    it is the one people copy; the others wrap but stay labels.

    Args:
        box: Vertical container that receives the heading and rows.
        meta: Dict from ``get_file_metadata``.
        heading: Section title. Pass an empty string to skip it.
        fields: Caption/key pairs to render. Preview passes
            ``_PREVIEW_METADATA_FIELDS`` for the preview pane.

    @tags: #scope/file-ops #scope/widget #scope/view-preference #action/render
    """
    if heading:
        title = Gtk.Label(label=_(heading))
        title.get_style_context().add_class("mc-preview-section")
        title.set_halign(Gtk.Align.START)
        title.set_xalign(0)
        title.set_margin_top(6)
        box.pack_start(title, False, False, 0)

    for caption, key in fields:
        value = meta.get(key) or ""
        if not value:
            continue
        box.pack_start(
            _metadata_row(_(caption), value, path=(key == "path")),
            False,
            False,
            0,
        )


def _metadata_row(caption: str, value: str, path: bool) -> Gtk.Box:
    """Build one caption/value row for preview metadata.

    @tags: #scope/widget #scope/file-ops #action/render
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.get_style_context().add_class("mc-meta")

    label = Gtk.Label(label=caption)
    label.get_style_context().add_class("mc-meta-label")
    label.set_halign(Gtk.Align.START)
    label.set_valign(Gtk.Align.START)
    label.set_xalign(0)
    label.set_size_request(_CAPTION_WIDTH, -1)

    value_label = Gtk.Label(label=value)
    value_label.get_style_context().add_class("mc-meta-value")
    value_label.set_halign(Gtk.Align.START)
    value_label.set_hexpand(True)
    value_label.set_xalign(0)
    if path:
        # One line, middle ellipsis: the filename stays visible and the
        # tooltip still carries the full path for copying.
        value_label.set_line_wrap(False)
        value_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        value_label.set_max_width_chars(24)
        value_label.set_selectable(True)
        value_label.set_tooltip_text(value)
    else:
        value_label.set_line_wrap(True)
        value_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        value_label.set_max_width_chars(28)
        value_label.set_selectable(False)

    row.pack_start(label, False, False, 0)
    row.pack_start(value_label, True, True, 0)
    return row


class PreviewPane(Gtk.Box):
    """Summary of the file selected in the focused column.

    ``preview_kind`` mirrors the metadata kind (``image``, ``text``,
    ``icon``) and is an empty string when nothing is selected. The kind
    stays on the metadata value even if the pixbuf loader fails and we
    fall back to an icon, so the caller can tell a broken image from a
    generic file.

    Attributes:
        preview_kind: ``image``, ``text``, ``icon``, or ``""``.

    @tags: #scope/widget #type/widget #scope/file-ops
    """

    def __init__(self) -> None:
        """Create the preview content box and show the empty state.

        @tags: #lifecycle/init #scope/widget #scope/file-ops #action/render
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("mc-preview")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.preview_kind = ""
        self._title = ""
        self._path: Optional[str] = None

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._content.get_style_context().add_class("mc-preview-content")
        self._content.set_size_request(_PREVIEW_CONTENT_WIDTH, -1)
        self._content.set_halign(Gtk.Align.START)
        self._content.set_valign(Gtk.Align.START)
        self._content.set_margin_start(_PREVIEW_CONTENT_MARGIN)
        self._content.set_margin_end(_PREVIEW_CONTENT_MARGIN)
        self._content.set_margin_top(_PREVIEW_CONTENT_MARGIN)
        self._content.set_margin_bottom(_PREVIEW_CONTENT_MARGIN)
        self.pack_start(self._content, True, True, 0)
        self.update_file(None)

    def get_title(self) -> str:
        """Return the selected filename, or ``""`` when the pane is empty."""
        return self._title

    def update_file(self, path: Optional[str]) -> None:
        """Show ``path``, or the empty state when ``path`` is None.

        Images are scaled to fit ``_IMAGE_FIT_WIDTH`` by
        ``_IMAGE_FIT_HEIGHT``. A loader failure still leaves
        ``preview_kind`` as ``image`` and draws the themed icon instead,
        so a corrupt photo is not presented as a generic file.

        Args:
            path: Local file path, or None to clear the pane.

        @tags: #scope/file-ops #scope/widget #scope/view-preference #action/render
        """
        self._clear()
        if not path:
            self._path = None
            self._title = ""
            self.preview_kind = ""
            self._pack_empty_state()
            self.show_all()
            return

        meta = get_file_metadata(path)
        self._path = meta["path"]
        self._title = meta["name"] or ""
        self.preview_kind = meta["preview_kind"] or ""
        self._pack_kind(meta)
        self._pack_heading(meta)
        pack_metadata_rows(self._content, meta, fields=_PREVIEW_METADATA_FIELDS)
        self.show_all()

    def update_selection(self, entries) -> None:
        """Summarize several selected rows.

        The heading is the selected-count msgid. The subtitle counts folders
        and files and omits a count of zero. Size is the sum of the
        file sizes. Directories are not walked; a folder's own size is not
        the size of what is inside it.

        Args:
            entries: Selected entries, folders and files.

        @tags: #scope/file-ops #scope/widget #action/render
        """
        self._clear()
        self._path = None
        self.preview_kind = ""
        count = len(entries)
        self._title = ngettext("%d item selected", "%d items selected", count) % count
        folders = sum(1 for entry in entries if entry.is_dir)
        files = count - folders
        parts = []
        if folders:
            parts.append(ngettext("%d folder", "%d folders", folders) % folders)
        if files:
            parts.append(ngettext("%d file", "%d files", files) % files)
        self._pack_heading_text(self._title, ", ".join(parts))
        total = sum(int(getattr(entry, "size", 0) or 0) for entry in entries if not entry.is_dir)
        pack_metadata_rows(self._content, {"size_human": format_size(total)}, heading="")
        self.show_all()

    def _clear(self) -> None:
        """_clear.

        @tags: #scope/widget #action/clear
        """
        for child in list(self._content.get_children()):
            self._content.remove(child)
            child.destroy()

    def _pack_empty_state(self) -> None:
        """_pack_empty_state.

        @tags: #scope/widget #action/render
        """
        empty = Gtk.Label(label=_("No file selected"))
        empty.get_style_context().add_class("mc-empty")
        empty.set_halign(Gtk.Align.START)
        empty.set_xalign(0)
        empty.set_line_wrap(True)
        empty.set_max_width_chars(28)
        self._content.pack_start(empty, False, False, 0)

    def _pack_kind(self, meta: dict) -> None:
        """_pack_kind.

        @tags: #scope/widget #action/render #scope/file-ops
        """
        kind = self.preview_kind
        if kind == "image":
            self._content.pack_start(self._image_widget(meta), False, False, 0)
            return
        if kind == "text":
            self._content.pack_start(self._text_widget(meta.get("preview_text") or ""), False, False, 0)
            return
        self._content.pack_start(self._icon_widget(meta), False, False, 0)

    def _image_widget(self, meta: dict) -> Gtk.Widget:
        """_image_widget.

        @tags: #scope/file-ops #scope/widget #action/render
        """
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.get_style_context().add_class("mc-preview-image")
        frame.set_halign(Gtk.Align.CENTER)
        image = Gtk.Image()
        image.set_halign(Gtk.Align.CENTER)
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                self._path,
                _IMAGE_FIT_WIDTH,
                _IMAGE_FIT_HEIGHT,
                True,
            )
            image.set_from_pixbuf(_round_pixbuf(pixbuf, _IMAGE_CORNER_RADIUS))
        except (GLib.Error, OSError, Exception):
            # Kind stays "image". The icon is only a stand-in for the pixels.
            image.set_from_icon_name(
                meta.get("icon_name") or "image-x-generic-symbolic",
                Gtk.IconSize.DIALOG,
            )
            image.set_pixel_size(_LARGE_ICON)
        frame.pack_start(image, False, False, 0)
        return frame

    def _text_widget(self, text: str) -> Gtk.Widget:
        """_text_widget.

        @tags: #scope/file-ops #scope/widget #action/render
        """
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.get_style_context().add_class("mc-preview-text")
        frame.set_halign(Gtk.Align.FILL)
        label = Gtk.Label(label=text)
        label.set_selectable(True)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_halign(Gtk.Align.START)
        label.set_xalign(0)
        label.set_yalign(0)
        label.set_max_width_chars(40)
        frame.pack_start(label, False, False, 0)
        return frame

    def _icon_widget(self, meta: dict) -> Gtk.Widget:
        """_icon_widget.

        @tags: #scope/file-ops #scope/widget #action/render
        """
        content_type = meta.get("content_type") or ""
        theme = Gtk.IconTheme.get_default()
        gicon = Gio.content_type_get_icon(content_type) if content_type else None
        info = None
        if gicon is not None and theme is not None:
            info = theme.lookup_by_gicon(gicon, _LARGE_ICON, Gtk.IconLookupFlags.FORCE_SIZE)

        pixbuf = None
        symbolic = info is None or info.is_symbolic()
        if info is not None and not symbolic:
            try:
                pixbuf = info.load_icon()
            except (GLib.Error, OSError):
                pixbuf = None
        tint = icon_tint(pixbuf) if pixbuf is not None else None
        # A symbolic glyph and a grey full-color icon both have no hue.
        use_accent = symbolic or tint is None

        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        tile.get_style_context().add_class("mc-preview-tile")
        tile.set_size_request(_TILE_SIZE, _TILE_SIZE)
        tile.set_halign(Gtk.Align.CENTER)
        image = Gtk.Image()
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        tile.pack_start(image, True, True, 0)

        if use_accent:
            tile.get_style_context().add_class("mc-preview-tile-accent")
            self._set_accent_icon(image, tile, info, gicon, theme, meta)
        else:
            self._paint_tile_tint(tile, tint)
            image.set_from_pixbuf(pixbuf)
        return tile

    def _set_accent_icon(self, image, tile, info, gicon, theme, meta: dict) -> None:
        """Recolor a symbolic icon with the theme's selected foreground.

        @tags: #scope/widget #scope/file-ops #action/render
        """
        symbolic_info = info
        if gicon is not None and theme is not None and (info is None or not info.is_symbolic()):
            forced = theme.lookup_by_gicon(
                gicon,
                _LARGE_ICON,
                Gtk.IconLookupFlags.FORCE_SIZE | Gtk.IconLookupFlags.FORCE_SYMBOLIC,
            )
            if forced is not None:
                symbolic_info = forced
        if symbolic_info is not None:
            try:
                colored, _was_symbolic = symbolic_info.load_symbolic_for_context(tile.get_style_context())
                image.set_from_pixbuf(colored)
                return
            except (GLib.Error, TypeError, ValueError):
                pass
        image.set_from_icon_name(
            meta.get("icon_name") or "text-x-generic-symbolic",
            Gtk.IconSize.DIALOG,
        )
        image.set_pixel_size(_LARGE_ICON)

    def _paint_tile_tint(self, tile: Gtk.Widget, tint: tuple[int, int, int]) -> None:
        """Fill the tile with the icon's own color at low opacity.

        The stylesheet cannot name this color: it is sampled from the file's
        icon. The provider stays on the widget so the color survives a
        style refresh.

        @tags: #scope/widget #scope/file-ops #action/render
        """
        red, green, blue = tint
        provider = Gtk.CssProvider()
        css = f".mc-preview-tile {{ background-color: rgba({red}, {green}, {blue}, 0.28); }}"
        provider.load_from_data(css.encode("utf-8"))
        tile.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        tile._tint_provider = provider

    def _pack_heading(self, meta: dict) -> None:
        """Pack the file name and its type description.

        @tags: #scope/widget #action/render
        """
        self._pack_heading_text(self._title, meta.get("mime_description") or "")

    def _pack_heading_text(self, title: str, subtitle: str) -> None:
        """Pack a centered title and an optional subtitle.

        Args:
            title: Heading text.
            subtitle: Second line. Empty skips the label.

        @tags: #scope/widget #action/render
        """
        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class("mc-preview-title")
        title_label.set_line_wrap(True)
        title_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title_label.set_justify(Gtk.Justification.CENTER)
        title_label.set_halign(Gtk.Align.CENTER)
        title_label.set_xalign(0.5)
        title_label.set_max_width_chars(24)
        self._content.pack_start(title_label, False, False, 0)
        if not subtitle:
            return
        sub = Gtk.Label(label=subtitle)
        sub.get_style_context().add_class("mc-preview-subtitle")
        sub.set_line_wrap(True)
        sub.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        sub.set_justify(Gtk.Justification.CENTER)
        sub.set_halign(Gtk.Align.CENTER)
        sub.set_xalign(0.5)
        sub.set_max_width_chars(28)
        self._content.pack_start(sub, False, False, 0)
