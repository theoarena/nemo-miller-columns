"""Nemo.MenuProvider discovery and item collection.

Walks loaded GTypes for ``Nemo.MenuProvider``, instantiates each once, and
asks those instances for file or background items. Construction is cached
by type name: a second instance would re-run a Python extension's
``__init__``. The instance is Nemo's, not ours.

Used by: ``miller.ui.nemo_items``.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GObject, Gtk

from miller.nemo_actions import MenuTarget, plugin_list

_PROVIDER_CACHE: dict[str, object] = {}


def menu_providers(disabled: list[str] | None = None) -> list:
    """Return cached ``Nemo.MenuProvider`` instances.

    Types whose name is in ``disabled-extensions`` are skipped. A type
    whose constructor fails is skipped and not cached.

    Args:
        disabled: ``g_type_name`` values to skip. ``None`` reads GSettings.

    Returns:
        Provider instances, in discovery order.

    @tags: #scope/file-ops #action/read #type/helper #type/extension #lifecycle/init #side-effect/gsettings
    """
    nemo = _nemo()
    if nemo is None:
        return []
    if disabled is None:
        disabled = plugin_list("disabled-extensions")
    skipped = set(disabled)
    provider_type = nemo.MenuProvider.__gtype__
    found: list = []
    for gtype in _walk_gtypes(GObject.Object.__gtype__):
        try:
            if gtype.is_abstract() or not gtype.is_a(provider_type):
                continue
            if gtype == provider_type or gtype.name in skipped:
                continue
        except Exception as error:
            print(f"[miller] {error}")
            continue
        instance = _PROVIDER_CACHE.get(gtype.name)
        if instance is None and gtype.name not in _PROVIDER_CACHE:
            try:
                instance = GObject.new(gtype)
            except Exception as error:
                print(f"[miller] {error}")
                continue
            _PROVIDER_CACHE[gtype.name] = instance
        if instance is not None:
            found.append(instance)
    return found


def extension_items(
    window,
    target: MenuTarget,
    background: bool,
    lookup=None,
) -> list:
    """Collect ``Nemo.MenuItem`` values from every provider.

    ``window`` must be the toplevel ``Gtk.Window``. ``None`` is rejected
    because providers expect a real Nemo window. Lookup is injected
    because ``Nemo.file_info_lookup_for_uri`` only works inside Nemo.

    Args:
        window: Toplevel window passed to the provider.
        target: Selection, or empty for a background click.
        background: Call ``get_background_items`` when true.
        lookup: ``callable(uri) -> file_info``.

    Returns:
        Provider menu items, skipping a provider that raises.

    @tags: #scope/file-ops #action/read #type/helper #type/extension #scope/widget
    """
    nemo = _nemo()
    if nemo is None:
        return []
    if window is None or not isinstance(window, Gtk.Window):
        return []
    if lookup is None:
        lookup = nemo.file_info_lookup_for_uri
    items: list = []
    if background:
        folder = _lookup_one(lookup, target.parent_dir)
        if folder is None:
            return []
        for provider in menu_providers():
            try:
                got = provider.get_background_items(window, folder)
            except Exception as error:
                print(f"[miller] {error}")
                continue
            if got:
                items.extend(got)
        return items
    files = []
    for path in target.paths:
        info = _lookup_one(lookup, path)
        if info is not None:
            files.append(info)
    if not files:
        return []
    for provider in menu_providers():
        try:
            got = provider.get_file_items(window, files)
        except Exception as error:
            print(f"[miller] {error}")
            continue
        if got:
            items.extend(got)
    return items


def _lookup_one(lookup, path: str):
    """Resolve a local path to a Nemo file info via URI lookup.

    @tags: #scope/file-ops #action/read #type/helper #type/extension
    """
    try:
        uri = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(path))).get_uri()
        return lookup(uri)
    except Exception as error:
        print(f"[miller] {error}")
        return None


def _walk_gtypes(gtype):
    """Depth-first yield of concrete GObject types under ``gtype``.

    @tags: #action/read #type/helper #type/extension
    """
    try:
        children = list(gtype.children)
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk_gtypes(child)


def _nemo():
    """Import ``gi.repository.Nemo`` when the extension bindings are available.

    @tags: #action/read #type/helper #type/extension
    """
    try:
        gi.require_version("Nemo", "3.0")
        from gi.repository import Nemo

        return Nemo
    except Exception as error:
        print(f"[miller] {error}")
        return None

