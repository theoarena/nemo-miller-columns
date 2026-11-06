"""User-installed Nemo actions and scripts, without widgets.

Reads ``*.nemo_action`` files and ``~/.local/share/nemo/scripts`` the way
Nemo 6.0.2 does. Launch stays on ``GLib.spawn_*`` and ``Gio.AppInfo`` so
this module can live next to ``transfer.py`` instead of under ``miller.ui``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


ACTION_GROUP = "Nemo Action"
MAX_MENU_LEVELS = 5
_QUOTE_MARK = {"single": "'", "double": '"', "backtick": "`"}
_KNOWN_SELECTION = {"s", "m", "any", "none", "notnone"}
_TOKENS = {
    "%F": "paths",
    "%U": "uris",
    "%P": "parent_path",
    "%R": "parent_uri",
    "%f": "file_name",
    "%N": "file_name",
    "%p": "parent_name",
    "%D": "device",
    "%e": "file_stem",
    "%%": "percent",
    "%X": "xid",
}


@dataclass
class MenuTarget:
    """Selected local paths and the column directory they sit in.

    ``paths`` may be empty: that is a background click.

    @tags: #type/data-model #scope/file-ops
    """

    paths: list[str]
    parent_dir: str


@dataclass
class NemoAction:
    """One parsed ``.nemo_action`` file.

    ``selection`` is ``s``, ``m``, ``any``, ``notnone``, ``none``, or a
    positive count. Unknown Selection values become ``s``.

    @tags: #type/data-model #scope/file-ops
    """

    path: str
    file_name: str
    parent_dir: str
    label: str
    tooltip: str
    icon_name: str
    exec_line: str
    use_parent_dir: bool
    selection: str | int
    extensions: list[str]
    mimetypes: list[str]
    conditions: list[str]
    separator: str | None
    quote: str
    terminal: bool
    uri_scheme: str | None


@dataclass
class ScriptNode:
    """One scripts-menu entry. Non-empty ``children`` is a submenu.

    @tags: #type/data-model #scope/file-ops
    """

    label: str
    path: str
    children: list[ScriptNode] = field(default_factory=list)


def plugin_list(key: str) -> list[str]:
    """Return ``org.nemo.plugins`` ``key``, or ``[]`` when the schema is missing.

    Args:
        key: ``disabled-actions``, ``disabled-scripts``, or ``disabled-extensions``.

    Returns:
        Basename list from GSettings.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/gsettings
    """
    try:
        settings = Gio.Settings.new("org.nemo.plugins")
        return list(settings.get_strv(key))
    except Exception as error:
        print(f"[miller] {error}")
        return []


def load_actions(
    data_dirs: list[str] | None = None,
    user_dir: str | None = None,
    disabled: list[str] | None = None,
) -> list[NemoAction]:
    """Load top-level ``*.nemo_action`` files in Nemo's directory order.

    System data dirs come first, then the user actions directory. Files in
    each directory are sorted by name. A file whose basename is disabled,
    that has ``Active=false``, that fails ``Dependencies``, or that is
    missing Name, Exec, Selection, or both Extensions and Mimetypes is
    skipped.

    Args:
        data_dirs: Prefixes equivalent to ``GLib.get_system_data_dirs()``.
        user_dir: Full path of the user actions directory.
        disabled: Basenames to skip. ``None`` reads ``disabled-actions``.

    Returns:
        Parsed actions, in menu order.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem #side-effect/gsettings
    """
    if disabled is None:
        disabled = plugin_list("disabled-actions")
    skipped = set(disabled)
    if data_dirs is None:
        data_dirs = list(GLib.get_system_data_dirs())
    directories = [os.path.join(prefix, "nemo", "actions") for prefix in data_dirs]
    if user_dir is None:
        user_dir = os.path.join(GLib.get_user_data_dir(), "nemo", "actions")
    directories.append(user_dir)
    actions: list[NemoAction] = []
    seen_dirs: set[str] = set()
    for directory in directories:
        real = os.path.abspath(directory)
        if real in seen_dirs:
            continue
        seen_dirs.add(real)
        actions.extend(_load_action_dir(real, skipped))
    return actions


def action_visible(
    action: NemoAction,
    target: MenuTarget,
    has_owner=None,
    settings_value=None,
    run_sync=None,
) -> bool:
    """Return whether ``action`` should appear for ``target``.

    Ports ``nemo_action_get_visibility``. ``desktop`` is always false
    because a Miller column is never the desktop.

    Args:
        action: Parsed action.
        target: Current selection and parent directory.
        has_owner: ``callable(name) -> bool``. Defaults to the bus helper.
        settings_value: ``callable(schema, key)`` returning a GVariant.
        run_sync: ``callable(cmd) -> int`` exit status for ``exec``.

    Returns:
        True when the item belongs on the menu.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/gsettings
    """
    if has_owner is None:
        from miller.transfer import _name_has_owner as has_owner
    if settings_value is None:
        settings_value = _settings_value
    if run_sync is None:
        run_sync = _run_sync
    for condition in action.conditions:
        if condition.startswith("dbus"):
            parts = condition.split(" ", 1)
            if len(parts) != 2:
                continue
            if not has_owner(parts[1]):
                return False
        elif condition.startswith("gsettings"):
            if not _gsettings_ok(condition, settings_value):
                return False
    if action.uri_scheme:
        parent = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(target.parent_dir)))
        if parent.get_uri_scheme() != action.uri_scheme:
            return False
    count = len(target.paths)
    selection = action.selection
    if selection == "s":
        ok = count == 1
    elif selection == "m":
        ok = count > 1
    elif selection == "notnone":
        ok = count > 0
    elif selection == "none":
        ok = count == 0
    elif selection == "any":
        ok = True
    else:
        ok = count == selection
    if not ok:
        return False
    if not _extension_match(action, target):
        return False
    for condition in action.conditions:
        if condition == "desktop":
            return False
        if condition == "removable":
            if not _is_removable(target):
                return False
        elif condition.startswith("exec"):
            if not _exec_ok(action, condition, target, run_sync):
                return False
    return True


def expand(
    text: str,
    action: NemoAction,
    target: MenuTarget,
    xid: int = 0,
    for_label: bool = False,
) -> str:
    """Expand Nemo Exec/Name/Comment tokens in ``text``.

    Scans for the first ``%``. An unknown token stops the scan, so a later
    ``%F`` after ``%Z`` is left alone. After a known token the scan
    continues just past the inserted text.

    Args:
        text: Template with ``%F`` / ``%U`` / … tokens.
        action: Quote type and separator come from here.
        target: Paths and parent directory used as insertions.
        xid: Window XID for ``%X``.
        for_label: Double underscores in inserted text (mnemonic labels).

    Returns:
        Expanded string.

    @tags: #scope/file-ops #action/read #type/helper
    """
    result = text
    pos = 0
    while True:
        rel = result[pos:].find("%")
        if rel < 0:
            break
        idx = pos + rel
        kind = _token_kind(result, idx)
        if kind is None:
            break
        insertion = _insertion(kind, action, target, xid, for_label)
        result = result[:idx] + insertion + result[idx + 2 :]
        pos = idx + len(insertion)
    return result


def run_action(action: NemoAction, target: MenuTarget, xid: int) -> bool:
    """Expand ``action`` and spawn it.

    ``Terminal=true`` goes through ``Gio.AppInfo`` with ``NEEDS_TERMINAL``.
    Otherwise the command is ``GLib.spawn_command_line_async``.

    Args:
        action: Parsed action.
        target: Selection used to expand Exec.
        xid: Window XID inserted as ``%X``.

    Returns:
        True when the launch was accepted.

    @tags: #scope/file-ops #action/write #side-effect/filesystem
    """
    try:
        command = expand(action.exec_line, action, target, xid)
        if action.use_parent_dir:
            command = action.parent_dir.rstrip(os.sep) + os.sep + command
        if action.terminal:
            app = Gio.AppInfo.create_from_commandline(
                command,
                None,
                Gio.AppInfoCreateFlags.NEEDS_TERMINAL,
            )
            return bool(app.launch([], None))
        return bool(GLib.spawn_command_line_async(command))
    except Exception as error:
        print(f"[miller] {error}")
        return False


def load_scripts(
    root: str | None = None,
    disabled: list[str] | None = None,
) -> list[ScriptNode]:
    """Load the scripts tree under ``root``.

    Hidden names are skipped. Regular files need ``X_OK`` and must not be
    in ``disabled-scripts``. Directories nest up to ``MAX_MENU_LEVELS``;
    empty directories are dropped.

    Args:
        root: Scripts directory. Default ``~/.local/share/nemo/scripts``.
        disabled: Basenames to skip. ``None`` reads ``disabled-scripts``.

    Returns:
        Menu nodes, sorted by display name.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem #side-effect/gsettings
    """
    if disabled is None:
        disabled = plugin_list("disabled-scripts")
    if root is None:
        root = os.path.join(GLib.get_user_data_dir(), "nemo", "scripts")
    if not os.path.isdir(root):
        return []
    return _load_script_dir(root, set(disabled), depth=0)


def run_script(path: str, target: MenuTarget, geometry: str = "") -> bool:
    """Spawn ``path`` with Nemo's script argv, cwd, and environment.

    Names are relative to ``parent_dir`` when possible, otherwise absolute.
    A path without a local name is dropped. ``NEMO_SCRIPT_SELECTED_FILE_PATHS``
    is empty when the parent URI is not ``file:``.

    Args:
        path: Executable script.
        target: Selection and parent directory.
        geometry: ``WIDTHxHEIGHT+X+Y`` window string.

    Returns:
        True when ``GLib.spawn_async`` accepted the launch.

    @tags: #scope/file-ops #action/write #side-effect/filesystem
    """
    try:
        names = _script_names(target)
        env = _script_env(target, geometry)
        envp = [f"{key}={value}" for key, value in env.items()]
        GLib.spawn_async(
            [path, *names],
            envp=envp,
            working_directory=os.path.abspath(os.path.expanduser(target.parent_dir)),
            flags=GLib.SpawnFlags.SEARCH_PATH,
        )
    except Exception as error:
        print(f"[miller] {error}")
        return False
    return True


def _load_action_dir(directory: str, skipped: set[str]) -> list[NemoAction]:
    """Load ``*.nemo_action`` files from one directory.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    if not os.path.isdir(directory):
        return []
    names = sorted(
        name for name in os.listdir(directory) if name.endswith(".nemo_action")
    )
    loaded: list[NemoAction] = []
    for name in names:
        if name in skipped:
            continue
        parsed = _parse_action(os.path.join(directory, name))
        if parsed is not None:
            loaded.append(parsed)
    return loaded


def _parse_action(path: str) -> NemoAction | None:
    """Parse one ``.nemo_action`` file into ``NemoAction``.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    key_file = GLib.KeyFile()
    try:
        key_file.load_from_file(path, GLib.KeyFileFlags.NONE)
    except Exception as error:
        print(f"[miller] {error}")
        return None
    if not key_file.has_group(ACTION_GROUP):
        return None
    if not _key_bool(key_file, "Active", True):
        return None
    label = _key_locale(key_file, "Name")
    exec_raw = _key_string(key_file, "Exec")
    selection_raw = _key_string(key_file, "Selection")
    extensions = _key_list(key_file, "Extensions")
    mimetypes = _key_list(key_file, "Mimetypes")
    if not label or not exec_raw or not selection_raw or (not extensions and not mimetypes):
        return None
    if not _dependencies_ok(_key_list(key_file, "Dependencies")):
        return None
    use_parent_dir, exec_line = _strip_custom_modifier(exec_raw)
    quote = _key_string(key_file, "Quote", "none")
    if quote not in ("single", "double", "backtick"):
        quote = "none"
    separator = _key_string(key_file, "Separator", None)
    uri_scheme = _key_string(key_file, "UriScheme", None) or None
    parent_dir = os.path.dirname(os.path.abspath(path))
    return NemoAction(
        path=os.path.abspath(path),
        file_name=os.path.basename(path),
        parent_dir=parent_dir,
        label=label,
        tooltip=_key_locale(key_file, "Comment"),
        icon_name=_key_string(key_file, "Icon-Name"),
        exec_line=exec_line,
        use_parent_dir=use_parent_dir,
        selection=_parse_selection(selection_raw),
        extensions=extensions,
        mimetypes=mimetypes,
        conditions=_key_list(key_file, "Conditions"),
        separator=separator,
        quote=quote,
        terminal=_key_bool(key_file, "Terminal", False),
        uri_scheme=uri_scheme,
    )


def _parse_selection(raw: str) -> str | int:
    """Normalize Selection key to a known token or positive count.

    @tags: #scope/file-ops #action/read #type/helper
    """
    lowered = raw.strip().lower()
    if lowered in _KNOWN_SELECTION:
        return lowered
    digits = []
    for char in lowered:
        if char.isdigit():
            digits.append(char)
        else:
            break
    value = int("".join(digits)) if digits else 0
    return value if value > 0 else "s"


def _strip_custom_modifier(raw: str) -> tuple[bool, str]:
    """Detect ``<exec>`` parent-dir modifier and strip angle brackets.

    @tags: #scope/file-ops #action/read #type/helper
    """
    if raw.startswith("<") and raw.endswith(">"):
        return True, raw[1:-1]
    return False, raw


def _dependencies_ok(deps: list[str]) -> bool:
    """Return whether every Dependencies entry resolves on PATH or disk.

    @tags: #scope/file-ops #action/read #type/helper
    """
    for dep in deps:
        reverse = dep.startswith("!")
        name = dep[1:] if reverse else dep
        found = os.path.exists(name) if os.path.isabs(name) else GLib.find_program_in_path(name) is not None
        if reverse and found:
            return False
        if not reverse and not found:
            return False
    return True


def _extension_match(action: NemoAction, target: MenuTarget) -> bool:
    """Match action Extensions/Mimetypes against every selected path.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    extensions = action.extensions
    mimetypes = action.mimetypes
    if len(extensions) == 1 and extensions[0] == "any":
        return True
    found_match = True
    for path in target.paths:
        found_match = False
        filename = os.path.basename(path).lower()
        is_dir = os.path.isdir(path)
        for ext in extensions:
            if ext == "dir":
                if is_dir:
                    found_match = True
                    break
            elif ext == "none":
                if "." not in filename:
                    found_match = True
                    break
            elif ext == "nodirs":
                if not is_dir:
                    found_match = True
                    break
            elif filename.endswith(ext.lower()):
                found_match = True
                break
        content_type = _content_type(path)
        if mimetypes:
            for mime in mimetypes:
                if content_type and (
                    content_type == mime or Gio.content_type_is_a(content_type, mime)
                ):
                    found_match = True
                    break
        if content_type and (
            content_type == "application/x-nemo-link"
            or Gio.content_type_is_a(content_type, "application/x-nemo-link")
        ):
            found_match = False
        if not found_match:
            return False
    return found_match


def _content_type(path: str) -> str:
    """Resolve Gio content type for a local path.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    try:
        info = Gio.File.new_for_path(path).query_info(
            "standard::content-type",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
        found = info.get_content_type()
        if found:
            return found
    except Exception:
        pass
    guessed, _uncertain = Gio.content_type_guess(os.path.basename(path), None)
    return guessed or ""


def _is_removable(target: MenuTarget) -> bool:
    """Return whether the first selected path sits on a removable drive.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    if not target.paths:
        return False
    try:
        gfile = Gio.File.new_for_path(target.paths[0])
        mount = gfile.find_enclosing_mount(None)
        if mount is None:
            return False
        drive = mount.get_drive()
        return bool(drive is not None and drive.is_removable())
    except Exception as error:
        print(f"[miller] {error}")
        return False


def _exec_ok(action: NemoAction, condition: str, target: MenuTarget, run_sync) -> bool:
    """Run an exec condition synchronously and require exit status zero.

    @tags: #scope/file-ops #action/read #type/helper
    """
    parts = condition.split(" ", 1)
    if len(parts) != 2 or parts[0] != "exec":
        return False
    use_parent_dir, command = _strip_custom_modifier(parts[1])
    command = expand(command, action, target)
    if use_parent_dir:
        command = action.parent_dir.rstrip(os.sep) + os.sep + command
    return run_sync(command) == 0


def _gsettings_ok(condition: str, settings_value) -> bool:
    """Evaluate a gsettings Conditions line against a live value.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/gsettings
    """
    parts = condition.split(" ", 5)
    if len(parts) not in (3, 6) or parts[0] != "gsettings":
        return False
    try:
        raw = settings_value(parts[1], parts[2])
    except Exception as error:
        print(f"[miller] {error}")
        return False
    variant = _as_variant(raw)
    if variant is None:
        return False
    if len(parts) == 3:
        return variant.is_of_type(GLib.VariantType.new("b")) and bool(variant.get_boolean())
    type_str, op, value_str = parts[3], parts[4], parts[5]
    if op not in ("eq", "ne", "lt", "gt"):
        return False
    try:
        target_type = GLib.VariantType.new(type_str)
        if not variant.is_of_type(target_type):
            return False
        target = GLib.Variant.parse(target_type, value_str, None, None)
        vector = variant.compare(target)
    except Exception as error:
        print(f"[miller] {error}")
        return False
    if op == "eq":
        return vector == 0
    if op == "ne":
        return vector != 0
    if op == "lt":
        return vector < 0
    return vector > 0


def _as_variant(value):
    """Coerce a Python value into ``GLib.Variant`` when possible.

    @tags: #type/helper #side-effect/gsettings
    """
    if isinstance(value, GLib.Variant):
        return value
    if isinstance(value, bool):
        return GLib.Variant.new_boolean(value)
    if isinstance(value, int):
        return GLib.Variant.new_int32(value)
    if isinstance(value, str):
        return GLib.Variant.new_string(value)
    return None


def _settings_value(schema: str, key: str):
    """Read one GSettings key as a ``GLib.Variant``.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/gsettings
    """
    settings = Gio.Settings.new(schema)
    return settings.get_value(key)


def _run_sync(command: str) -> int:
    """Run a shell command synchronously and return its exit status.

    @tags: #scope/file-ops #action/read #type/helper
    """
    try:
        _ok, _out, _err, status = GLib.spawn_command_line_sync(command)
        return int(status)
    except Exception as error:
        print(f"[miller] {error}")
        return 1


def _token_kind(text: str, idx: int) -> str | None:
    """Map a two-character ``%`` token at ``idx`` to an insertion kind.

    @tags: #scope/file-ops #action/read #type/helper
    """
    token = text[idx : idx + 2]
    return _TOKENS.get(token)


def _insertion(kind: str, action: NemoAction, target: MenuTarget, xid: int, for_label: bool) -> str:
    """Build expanded text for one Nemo Exec/Name token kind.

    @tags: #scope/file-ops #action/read #type/helper
    """
    quote = action.quote
    if kind == "percent":
        return _score("%", for_label)
    if kind == "xid":
        return _score(str(int(xid)), for_label)
    parent = os.path.abspath(os.path.expanduser(target.parent_dir))
    parent_name = os.path.basename(parent.rstrip(os.sep)) or parent
    if kind == "parent_path":
        return _quoted(_escape_path(parent, quote), quote, for_label)
    if kind == "parent_uri":
        return _quoted(Gio.File.new_for_path(parent).get_uri(), quote, for_label)
    if kind == "parent_name":
        return _quoted(parent_name, quote, for_label)
    if kind == "file_name":
        if target.paths:
            return _score(os.path.basename(target.paths[0]), for_label)
        return _quoted(parent_name, quote, for_label)
    if kind == "file_stem":
        if target.paths:
            return _score(_strip_ext(os.path.basename(target.paths[0])), for_label)
        return _quoted(parent, quote, for_label)
    if kind == "paths":
        if not target.paths:
            return _quoted(_escape_path(parent, quote), quote, for_label)
        return _join_quoted(
            [_escape_path(os.path.abspath(path), quote) for path in target.paths],
            action,
            for_label,
        )
    if kind == "uris":
        if not target.paths:
            return _quoted(Gio.File.new_for_path(parent).get_uri(), quote, for_label)
        return _join_quoted(
            [Gio.File.new_for_path(os.path.abspath(path)).get_uri() for path in target.paths],
            action,
            for_label,
        )
    if kind == "device":
        if not target.paths:
            return _quoted(_escape_path(parent, quote), quote, for_label)
        return _join_quoted(
            [_escape_path(_device_path(path), quote) for path in target.paths],
            action,
            for_label,
        )
    return ""


def _join_quoted(values: list[str], action: NemoAction, for_label: bool) -> str:
    """Join quoted path or URI fragments with the action separator.

    @tags: #scope/file-ops #action/read #type/helper
    """
    pieces = []
    separator = " " if action.separator is None else _score(action.separator, for_label)
    for index, value in enumerate(values):
        if index:
            pieces.append(separator)
        pieces.append(_quoted(value, action.quote, for_label))
    return "".join(pieces)


def _quoted(value: str, quote: str, for_label: bool) -> str:
    """Wrap a value in the action's quote style, optionally for labels.

    @tags: #scope/file-ops #action/read #type/helper
    """
    mark = _QUOTE_MARK.get(quote, "")
    return mark + _score(value, for_label) + mark


def _score(text: str, for_label: bool) -> str:
    """Double underscores in inserted text when building mnemonic labels.

    @tags: #scope/file-ops #action/read #type/helper
    """
    return text.replace("_", "__") if for_label else text


def _escape_path(text: str, quote: str) -> str:
    """Escape a path for the action's quoting rules.

    @tags: #scope/file-ops #action/read #type/helper #security/path-validation
    """
    if quote == "double":
        return text.replace("\\", "\\\\").replace('"', '\\"')
    if quote == "single":
        return text.replace("'", "'\\''")
    out = []
    for char in text:
        if char == "\n":
            out.append("'\n'")
        elif char in " \t'\"\\#":
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


def _strip_ext(name: str) -> str:
    """Return basename without the last extension segment.

    @tags: #scope/file-ops #action/read #type/helper
    """
    index = name.rfind(".")
    if index > 0:
        return name[:index]
    return name


def _device_path(path: str) -> str:
    """Resolve unix-device identifier for a path's enclosing volume.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    try:
        gfile = Gio.File.new_for_path(path)
        mount = gfile.find_enclosing_mount(None)
        if mount is None:
            return ""
        volume = mount.get_volume()
        if volume is None:
            return ""
        ident = volume.get_identifier("unix-device")
        return ident or ""
    except Exception as error:
        print(f"[miller] {error}")
        return ""


def _load_script_dir(directory: str, disabled: set[str], depth: int) -> list[ScriptNode]:
    """Recursively load executable scripts under one directory.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    try:
        names = sorted(os.listdir(directory))
    except Exception as error:
        print(f"[miller] {error}")
        return []
    nodes: list[ScriptNode] = []
    for name in names:
        if name.startswith("."):
            continue
        full = os.path.join(directory, name)
        if os.path.isdir(full):
            if depth + 1 > MAX_MENU_LEVELS:
                continue
            children = _load_script_dir(full, disabled, depth + 1)
            if children:
                nodes.append(ScriptNode(label=name, path=full, children=children))
            continue
        if not os.path.isfile(full) or not os.access(full, os.X_OK):
            continue
        if name in disabled:
            continue
        nodes.append(ScriptNode(label=name, path=full, children=[]))
    return nodes


def _script_names(target: MenuTarget) -> list[str]:
    """Build argv basenames relative to the parent directory when possible.

    @tags: #scope/file-ops #action/read #type/helper
    """
    parent = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(target.parent_dir)))
    names: list[str] = []
    for path in target.paths:
        if not path:
            continue
        location = Gio.File.new_for_path(path)
        relative = parent.get_relative_path(location)
        if relative:
            names.append(relative)
            continue
        absolute = location.get_path()
        if absolute:
            names.append(absolute)
    return names


def _script_env(target: MenuTarget, geometry: str) -> dict[str, str]:
    """Populate Nemo script environment variables for the current target.

    @tags: #scope/file-ops #action/read #type/helper
    """
    env = os.environ.copy()
    parent = Gio.File.new_for_path(os.path.abspath(os.path.expanduser(target.parent_dir)))
    parent_uri = parent.get_uri() or ""
    if parent_uri.startswith("file:"):
        paths = "".join(f"{os.path.abspath(path)}\n" for path in target.paths if path)
    else:
        paths = ""
    uris = "".join(
        f"{Gio.File.new_for_path(path).get_uri()}\n" for path in target.paths if path
    )
    env["NEMO_SCRIPT_SELECTED_FILE_PATHS"] = paths
    env["NEMO_SCRIPT_SELECTED_URIS"] = uris
    env["NEMO_SCRIPT_CURRENT_URI"] = parent_uri
    env["NEMO_SCRIPT_WINDOW_GEOMETRY"] = geometry
    env["NEMO_SCRIPT_NEXT_PANE_SELECTED_FILE_PATHS"] = ""
    env["NEMO_SCRIPT_NEXT_PANE_SELECTED_URIS"] = ""
    env["NEMO_SCRIPT_NEXT_PANE_CURRENT_URI"] = ""
    return env


def _key_string(key_file: GLib.KeyFile, key: str, default: str | None = "") -> str | None:
    """Read a string key from the Nemo Action group with a default.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    try:
        value = key_file.get_string(ACTION_GROUP, key)
    except GLib.Error:
        return default
    return value if value is not None else default


def _key_locale(key_file: GLib.KeyFile, key: str, default: str = "") -> str:
    """Read a locale string key from the Nemo Action group.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    try:
        value = key_file.get_locale_string(ACTION_GROUP, key, None)
    except GLib.Error:
        return default
    return value if value is not None else default


def _key_list(key_file: GLib.KeyFile, key: str) -> list[str]:
    """Read a string list key from the Nemo Action group.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    try:
        return list(key_file.get_string_list(ACTION_GROUP, key))
    except GLib.Error:
        return []


def _key_bool(key_file: GLib.KeyFile, key: str, default: bool) -> bool:
    """Read a boolean key from the Nemo Action group with a default.

    @tags: #scope/file-ops #action/read #type/helper #side-effect/filesystem
    """
    try:
        return bool(key_file.get_boolean(ACTION_GROUP, key))
    except GLib.Error:
        return default
