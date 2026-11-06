"""Domain tests for Nemo actions and scripts."""

import os
import stat
import sys
import tempfile
import unittest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gi.repository import Gio, GLib

from miller.nemo_actions import (
    MenuTarget,
    NemoAction,
    ScriptNode,
    action_visible,
    expand,
    load_actions,
    load_scripts,
    run_action,
    run_script,
)


def _write_action(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def _minimal(**fields):
    values = {
        "Name": "Sample",
        "Exec": "true %F",
        "Selection": "s",
        "Extensions": "any;",
    }
    values.update(fields)
    lines = ["[Nemo Action]"] + [f"{key}={value}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def _action(**kwargs):
    values = dict(
        path="/tmp/x.nemo_action",
        file_name="x.nemo_action",
        parent_dir="/tmp",
        label="Label",
        tooltip="",
        icon_name="",
        exec_line="true %F",
        use_parent_dir=False,
        selection="s",
        extensions=["any"],
        mimetypes=[],
        conditions=[],
        separator=None,
        quote="none",
        terminal=False,
        uri_scheme=None,
    )
    values.update(kwargs)
    return NemoAction(**values)


class NemoActionTests(unittest.TestCase):
    def test_required_fields_and_active_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_action(tmp, "ok.nemo_action", _minimal())
            _write_action(
                tmp,
                "inactive.nemo_action",
                _minimal(Active="false"),
            )
            _write_action(
                tmp,
                "noexec.nemo_action",
                "[Nemo Action]\nName=X\nSelection=s\nExtensions=any;\n",
            )
            loaded = load_actions(data_dirs=[], user_dir=tmp, disabled=[])
            self.assertEqual([item.file_name for item in loaded], ["ok.nemo_action"])

    def test_dependencies_bare_bang_and_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "no-such-bin")
            present = os.path.join(tmp, "present.bin")
            with open(present, "w", encoding="utf-8") as handle:
                handle.write("x")
            _write_action(tmp, "need-bash.nemo_action", _minimal(Dependencies="bash;"))
            _write_action(tmp, "need-missing.nemo_action", _minimal(Dependencies="definitely-not-a-nemo-bin;"))
            _write_action(tmp, "bang-bash.nemo_action", _minimal(Dependencies="!bash;"))
            _write_action(tmp, "bang-missing.nemo_action", _minimal(Dependencies="!definitely-not-a-nemo-bin;"))
            _write_action(tmp, "abs-ok.nemo_action", _minimal(Dependencies=present + ";"))
            _write_action(tmp, "abs-missing.nemo_action", _minimal(Dependencies=missing + ";"))
            loaded = {item.file_name for item in load_actions(data_dirs=[], user_dir=tmp, disabled=[])}
            self.assertEqual(
                loaded,
                {"need-bash.nemo_action", "bang-missing.nemo_action", "abs-ok.nemo_action"},
            )

    def test_disabled_list_and_directory_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys_a = os.path.join(tmp, "sys-a")
            sys_b = os.path.join(tmp, "sys-b")
            user = os.path.join(tmp, "user")
            for prefix, name in ((sys_a, "b.nemo_action"), (sys_a, "a.nemo_action"), (sys_b, "c.nemo_action")):
                os.makedirs(os.path.join(prefix, "nemo", "actions"), exist_ok=True)
                _write_action(os.path.join(prefix, "nemo", "actions"), name, _minimal())
            os.makedirs(user)
            _write_action(user, "z.nemo_action", _minimal())
            _write_action(user, "skip.nemo_action", _minimal())
            loaded = load_actions(
                data_dirs=[sys_a, sys_b],
                user_dir=user,
                disabled=["skip.nemo_action"],
            )
            self.assertEqual(
                [item.file_name for item in loaded],
                ["a.nemo_action", "b.nemo_action", "c.nemo_action", "z.nemo_action"],
            )

    def test_parent_dir_exec_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_action(
                tmp,
                "wrap.nemo_action",
                _minimal(Exec="<myaction.py %F>"),
            )
            loaded = load_actions(data_dirs=[], user_dir=tmp, disabled=[])
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0].use_parent_dir)
            self.assertEqual(loaded[0].exec_line, "myaction.py %F")
            self.assertEqual(loaded[0].parent_dir, tmp)

    def test_selection_matrix(self):
        target0 = MenuTarget(paths=[], parent_dir="/tmp")
        target1 = MenuTarget(paths=["/tmp/a"], parent_dir="/tmp")
        target2 = MenuTarget(paths=["/tmp/a", "/tmp/b"], parent_dir="/tmp")
        cases = [
            ("s", target0, False),
            ("s", target1, True),
            ("s", target2, False),
            ("m", target0, False),
            ("m", target1, False),
            ("m", target2, True),
            ("any", target0, True),
            ("any", target2, True),
            ("notnone", target0, False),
            ("notnone", target1, True),
            ("none", target0, True),
            ("none", target1, False),
            (2, target2, True),
            (2, target1, False),
        ]
        for selection, target, expected in cases:
            action = _action(selection=selection)
            self.assertEqual(
                action_visible(action, target, has_owner=lambda _n: False),
                expected,
                (selection, target.paths, expected),
            )

    def test_extensions_and_mimetypes(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = os.path.join(tmp, "note.txt")
            archive = os.path.join(tmp, "pack.tar")
            folder = os.path.join(tmp, "Docs")
            none = os.path.join(tmp, "README")
            mixed = os.path.join(tmp, "Note.TXT")
            with open(note, "w", encoding="utf-8") as handle:
                handle.write("x")
            with open(archive, "w", encoding="utf-8") as handle:
                handle.write("x")
            with open(none, "w", encoding="utf-8") as handle:
                handle.write("x")
            with open(mixed, "w", encoding="utf-8") as handle:
                handle.write("x")
            os.makedirs(folder)
            parent = tmp
            vis = lambda action, paths: action_visible(
                action, MenuTarget(paths=paths, parent_dir=parent), has_owner=lambda _n: False
            )
            self.assertTrue(vis(_action(extensions=["any"], selection="any"), [note, folder]))
            self.assertTrue(vis(_action(extensions=["dir"], selection="s"), [folder]))
            self.assertFalse(vis(_action(extensions=["dir"]), [note]))
            self.assertTrue(vis(_action(extensions=["none"]), [none]))
            self.assertFalse(vis(_action(extensions=["none"]), [note]))
            self.assertTrue(vis(_action(extensions=["nodirs"]), [note]))
            self.assertFalse(vis(_action(extensions=["nodirs"]), [folder]))
            self.assertTrue(vis(_action(extensions=["txt"]), [note]))
            self.assertTrue(vis(_action(extensions=["txt"]), [mixed]))
            self.assertFalse(vis(_action(extensions=["txt"]), [archive]))
            self.assertTrue(
                vis(_action(extensions=[], mimetypes=["text/plain"]), [note])
            )
            self.assertFalse(
                vis(_action(extensions=[], mimetypes=["image/png"]), [note])
            )

    def test_conditions_use_injected_callables(self):
        target = MenuTarget(paths=["/tmp/a.txt"], parent_dir="/tmp")
        dbus_action = _action(conditions=["dbus org.Foo"], extensions=["any"], selection="s")
        self.assertTrue(action_visible(dbus_action, target, has_owner=lambda name: name == "org.Foo"))
        self.assertFalse(action_visible(dbus_action, target, has_owner=lambda _n: False))

        bool_action = _action(conditions=["gsettings org.x flag"], selection="any")
        self.assertTrue(
            action_visible(
                bool_action,
                target,
                has_owner=lambda _n: False,
                settings_value=lambda _s, _k: GLib.Variant("b", True),
            )
        )
        self.assertFalse(
            action_visible(
                bool_action,
                target,
                has_owner=lambda _n: False,
                settings_value=lambda _s, _k: GLib.Variant("b", False),
            )
        )
        cmp_action = _action(conditions=["gsettings org.x n i gt 3"], selection="any")
        self.assertTrue(
            action_visible(
                cmp_action,
                target,
                has_owner=lambda _n: False,
                settings_value=lambda _s, _k: GLib.Variant("i", 5),
            )
        )
        self.assertFalse(
            action_visible(
                cmp_action,
                target,
                has_owner=lambda _n: False,
                settings_value=lambda _s, _k: GLib.Variant("i", 1),
            )
        )
        exec_action = _action(conditions=["exec true %f"], selection="any")
        ran = []
        self.assertTrue(
            action_visible(
                exec_action,
                target,
                has_owner=lambda _n: False,
                run_sync=lambda cmd: ran.append(cmd) or 0,
            )
        )
        self.assertTrue(ran[0].startswith("true "))
        self.assertFalse(
            action_visible(
                exec_action,
                target,
                has_owner=lambda _n: False,
                run_sync=lambda _cmd: 1,
            )
        )
        desktop = _action(conditions=["desktop"], selection="any")
        self.assertFalse(action_visible(desktop, target, has_owner=lambda _n: False))
        removable = _action(conditions=["removable"], selection="any")
        self.assertFalse(action_visible(removable, MenuTarget(paths=[], parent_dir="/tmp"), has_owner=lambda _n: False))

    def test_uri_scheme(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = os.path.join(tmp, "note.txt")
            with open(note, "w", encoding="utf-8") as handle:
                handle.write("x")
            target = MenuTarget(paths=[note], parent_dir=tmp)
            file_action = _action(uri_scheme="file", selection="s")
            sftp_action = _action(uri_scheme="sftp", selection="s")
            self.assertTrue(action_visible(file_action, target, has_owner=lambda _n: False))
            self.assertFalse(action_visible(sftp_action, target, has_owner=lambda _n: False))

    def test_expand_tokens_quotes_separator_and_first_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = os.path.join(tmp, "a file.txt")
            two = os.path.join(tmp, "b.txt")
            with open(one, "w", encoding="utf-8") as handle:
                handle.write("x")
            with open(two, "w", encoding="utf-8") as handle:
                handle.write("x")
            target = MenuTarget(paths=[one, two], parent_dir=tmp)
            parent_uri = Gio.File.new_for_path(tmp).get_uri()
            one_uri = Gio.File.new_for_path(one).get_uri()
            two_uri = Gio.File.new_for_path(two).get_uri()
            quoted = _action(quote="double", separator=",")
            self.assertEqual(
                expand("%F", quoted, target),
                f'"{one}","{two}"',
            )
            self.assertEqual(expand("%U", quoted, target), f'"{one_uri}","{two_uri}"')
            self.assertEqual(expand("%P", quoted, target), f'"{tmp}"')
            self.assertEqual(expand("%R", quoted, target), f'"{parent_uri}"')
            self.assertEqual(expand("%f", quoted, target), "a file.txt")
            self.assertEqual(expand("%N", quoted, target), "a file.txt")
            self.assertEqual(expand("%p", quoted, target), f'"{os.path.basename(tmp)}"')
            self.assertEqual(expand("%e", quoted, target), "a file")
            self.assertEqual(expand("%%", quoted, target), "%")
            self.assertEqual(expand("%X", quoted, target, xid=42), "42")
            single = _action(quote="single")
            self.assertEqual(expand("%F", single, MenuTarget(paths=[one], parent_dir=tmp)), f"'{one}'")
            none = _action(quote="none")
            self.assertEqual(expand("%F", none, MenuTarget(paths=[one], parent_dir=tmp)), one.replace(" ", "\\ "))
            tick = _action(quote="backtick")
            self.assertEqual(
                expand("%F", tick, MenuTarget(paths=[one], parent_dir=tmp)),
                f"`{one.replace(' ', '\\ ')}`",
            )
            label_action = _action()
            self.assertEqual(
                expand("Open %f", label_action, MenuTarget(paths=[os.path.join(tmp, "a_b.txt")], parent_dir=tmp), for_label=True),
                "Open a__b.txt",
            )
            with open(os.path.join(tmp, "a_b.txt"), "w", encoding="utf-8") as handle:
                handle.write("x")
            self.assertEqual(
                expand("Open %f", label_action, MenuTarget(paths=[os.path.join(tmp, "a_b.txt")], parent_dir=tmp), for_label=True),
                "Open a__b.txt",
            )
            self.assertEqual(expand("100% done %F", none, target), "100% done %F")
            self.assertIn(tmp, expand("%F then %P", none, target))

    def test_run_action_plain_and_terminal(self):
        action = _action(exec_line="echo %f", selection="s")
        target = MenuTarget(paths=["/tmp/note.txt"], parent_dir="/tmp")
        spawned = []
        original = GLib.spawn_command_line_async
        GLib.spawn_command_line_async = lambda cmd: spawned.append(cmd) or True
        try:
            self.assertTrue(run_action(action, target, 0))
            self.assertEqual(spawned, ["echo note.txt"])
        finally:
            GLib.spawn_command_line_async = original

        class _App:
            def __init__(self):
                self.launched = False

            def launch(self, files, context):
                self.launched = True
                return True

        created = []
        app = _App()
        original_create = Gio.AppInfo.create_from_commandline
        Gio.AppInfo.create_from_commandline = lambda cmd, name, flags: created.append((cmd, flags)) or app
        try:
            terminal = _action(exec_line="top", terminal=True, use_parent_dir=True, parent_dir="/opt/actions")
            self.assertTrue(run_action(terminal, target, 0))
            self.assertEqual(created[0][0], "/opt/actions/top")
            self.assertEqual(int(created[0][1]), int(Gio.AppInfoCreateFlags.NEEDS_TERMINAL))
            self.assertTrue(app.launched)
        finally:
            Gio.AppInfo.create_from_commandline = original_create


class NemoScriptTests(unittest.TestCase):
    def test_load_scripts_filters_sorts_and_nests(self):
        with tempfile.TemporaryDirectory() as tmp:
            def make_exec(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("#!/bin/sh\n")
                os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

            make_exec(os.path.join(tmp, "b.sh"))
            make_exec(os.path.join(tmp, "a.sh"))
            make_exec(os.path.join(tmp, ".hidden.sh"))
            make_exec(os.path.join(tmp, "skip.sh"))
            with open(os.path.join(tmp, "nope.txt"), "w", encoding="utf-8") as handle:
                handle.write("no")
            make_exec(os.path.join(tmp, "nested", "inner.sh"))
            os.makedirs(os.path.join(tmp, "empty"))
            make_exec(os.path.join(tmp, "l1", "keep.sh"))
            deep = tmp
            for name in ("l1", "l2", "l3", "l4", "l5", "l6"):
                deep = os.path.join(deep, name)
            make_exec(os.path.join(deep, "too-deep.sh"))
            nodes = load_scripts(root=tmp, disabled=["skip.sh"])
            labels = [node.label for node in nodes]
            self.assertEqual(labels, ["a.sh", "b.sh", "l1", "nested"])
            nested = next(node for node in nodes if node.label == "nested")
            self.assertEqual([child.label for child in nested.children], ["inner.sh"])
            self.assertFalse(any(node.label == "empty" for node in nodes))
            self.assertFalse(any(node.label == ".hidden.sh" for node in nodes))
            l1 = next(node for node in nodes if node.label == "l1")

            def deepest(node, depth=0):
                if not node.children:
                    return depth, node.label
                return max(deepest(child, depth + 1) for child in node.children)

            depth, label = deepest(l1)
            self.assertLessEqual(depth, 5)
            self.assertNotEqual(label, "too-deep.sh")

    def test_run_script_argv_cwd_env_and_absolute_fallback(self):
        captured = {}

        def fake_spawn(argv, envp=None, working_directory=None, flags=0, child_setup=None):
            captured["argv"] = list(argv)
            captured["envp"] = list(envp)
            captured["cwd"] = working_directory
            return (1, None, None, None)

        original = GLib.spawn_async
        GLib.spawn_async = fake_spawn
        try:
            with tempfile.TemporaryDirectory() as tmp:
                nested = os.path.join(tmp, "sub", "echo.sh")
                os.makedirs(os.path.dirname(nested))
                note = os.path.join(tmp, "note.txt")
                with open(note, "w", encoding="utf-8") as handle:
                    handle.write("x")
                target = MenuTarget(paths=[note], parent_dir=tmp)
                self.assertTrue(run_script(nested, target, geometry="10x20+1+2"))
                self.assertEqual(captured["argv"], [nested, "note.txt"])
                self.assertEqual(captured["cwd"], tmp)
                env = dict(item.split("=", 1) for item in captured["envp"] if item.startswith("NEMO_SCRIPT_"))
                self.assertEqual(env["NEMO_SCRIPT_SELECTED_FILE_PATHS"], note + "\n")
                self.assertTrue(env["NEMO_SCRIPT_SELECTED_URIS"].endswith("\n"))
                self.assertEqual(env["NEMO_SCRIPT_CURRENT_URI"], Gio.File.new_for_path(tmp).get_uri())
                self.assertEqual(env["NEMO_SCRIPT_WINDOW_GEOMETRY"], "10x20+1+2")
                self.assertEqual(env["NEMO_SCRIPT_NEXT_PANE_SELECTED_FILE_PATHS"], "")
                outside = os.path.join(tempfile.gettempdir(), "outside-nemo-script.txt")
                other = MenuTarget(paths=[outside], parent_dir=tmp)
                self.assertTrue(run_script(nested, other, geometry=""))
                self.assertEqual(captured["argv"][1], os.path.abspath(outside))
        finally:
            GLib.spawn_async = original


if __name__ == "__main__":
    unittest.main()
