import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QEvent, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from fortibar.controller import Controller
from fortibar.dms_bridge import DmsBridge
from fortibar.dms_install import install, activate_widget, ActivationError

APP = QApplication.instance() or QApplication([])


class DmsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(demo=True)
        self.packets = []
        self.bridge = DmsBridge(APP, self.controller, writer=self.packets.append)

    def tearDown(self):
        self.controller.secrets.cancel_all()
        self.bridge.deleteLater()
        self.controller.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_no_gui_event_loop_is_required_for_controller(self):
        process = subprocess.Popen([sys.executable, "-m", "fortibar", "--dms-bridge", "--demo"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(json.loads(process.stdout.readline())["snapshot"]["status"], "disconnected")
            process.stdin.write(b'{"id":1,"method":"newProfile","args":[]}\n')
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["event"], "edit")
        finally:
            process.stdin.close()
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.stderr.read(), b"")
            process.stdout.close()
            process.stderr.close()

    def test_password_is_not_reflected_to_dms(self):
        raw = self.controller.selected_profile().to_dict()
        self.bridge.dispatch({"id": 5, "method": "saveProfile", "args": [raw, "very-private", False]})
        self.assertNotIn("very-private", json.dumps(self.packets))
        self.assertIn({"reply": 5, "result": True}, self.packets)

    def test_arbitrary_method_is_rejected(self):
        self.bridge.dispatch({"id": 7, "method": "__getattribute__", "args": ["__class__"]})
        self.assertIn({"reply": 7, "result": False}, self.packets)

    def test_normal_invocation_activates_the_menu_without_an_app_window(self):
        from fortibar.app import main
        windows = list(APP.topLevelWindows())
        with patch("fortibar.dms_install.install") as setup:
            self.assertEqual(main([]), 0)
        setup.assert_called_once_with()
        self.assertEqual(list(APP.topLevelWindows()), windows)

    def test_activation_failure_is_reported_and_returns_failure(self):
        from fortibar.app import main
        error = io.StringIO()
        with patch("fortibar.dms_install.install", side_effect=ActivationError("DMS rejected the plugin")), contextlib.redirect_stderr(error):
            self.assertEqual(main([]), 1)
        self.assertIn("DMS rejected the plugin", error.getvalue())

    def test_restart_is_explicitly_requested_by_launcher_flag(self):
        from fortibar.app import main
        with patch("fortibar.dms_install.install") as setup:
            self.assertEqual(main(["--restart-dms"]), 0)
        setup.assert_called_once_with(restart_dms=True)


class DmsInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.settings = {"fontFamily": "My Font", "barConfigs": [
            {"id": "top", "enabled": True, "rightWidgets": ["systemTray", "battery"]},
            {"id": "off", "enabled": False, "rightWidgets": ["clock"]}]}
        (self.directory / "settings.json").write_text(json.dumps(self.settings))
        (self.directory / "plugin_settings.json").write_text('{"anotherPlugin":{"enabled":true}}')

    def enable(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return install(config_dir=self.directory, command=["forti-bar", "--dms-bridge"])

    def test_inserts_beside_tray_preserving_other_settings_and_backups(self):
        destination = self.enable()
        settings = json.loads((self.directory / "settings.json").read_text())
        self.assertEqual(settings["barConfigs"][0]["rightWidgets"], ["systemTray", "fortiBar", "battery"])
        self.assertEqual(settings["barConfigs"][1], self.settings["barConfigs"][1])
        self.assertEqual(settings["fontFamily"], "My Font")
        self.assertEqual(json.loads((self.directory / "settings.json.before-forti-bar").read_text()), self.settings)
        plugins = json.loads((self.directory / "plugin_settings.json").read_text())
        self.assertTrue(plugins["anotherPlugin"]["enabled"])
        self.assertTrue(plugins["fortiBar"]["enabled"])
        self.assertTrue((destination / "FortiPanel.qml").exists())

    def test_repeated_install_does_not_duplicate_widget(self):
        self.enable()
        self.enable()
        settings = json.loads((self.directory / "settings.json").read_text())
        self.assertEqual(settings["barConfigs"][0]["rightWidgets"].count("fortiBar"), 1)

    def test_invalid_configuration_is_not_overwritten(self):
        path = self.directory / "plugin_settings.json"
        path.write_text("broken")
        with self.assertRaises(ValueError):
            self.enable()
        self.assertEqual(path.read_text(), "broken")
        self.assertEqual(json.loads((self.directory / "settings.json").read_text()), self.settings)

    def packaged(self):
        system = self.directory / "system/FortiBar"
        system.mkdir(parents=True)
        (system / "plugin.json").write_text('{"id": "fortiBar"}')
        patcher = patch("fortibar.dms_install.REPO_ROOT", self.directory / "not-a-checkout")
        patcher.start()
        self.addCleanup(patcher.stop)
        return system

    def test_packaged_install_uses_system_plugin_without_copying(self):
        system = self.packaged()
        with contextlib.redirect_stdout(io.StringIO()):
            destination = install(config_dir=self.directory, system_plugin=system)
        self.assertEqual(destination, system)
        self.assertFalse((self.directory / "plugins/FortiBar").exists())
        self.assertTrue(json.loads((self.directory / "plugin_settings.json").read_text())["fortiBar"]["enabled"])
        settings = json.loads((self.directory / "settings.json").read_text())
        self.assertEqual(settings["barConfigs"][0]["rightWidgets"], ["systemTray", "fortiBar", "battery"])

    def test_packaged_install_removes_shadowing_user_copy_only_if_ours(self):
        system = self.packaged()
        stale = self.directory / "plugins/FortiBar"
        stale.mkdir(parents=True)
        (stale / "plugin.json").write_text('{"id": "fortiBar"}')
        with contextlib.redirect_stdout(io.StringIO()):
            install(config_dir=self.directory, system_plugin=system)
        self.assertFalse(stale.exists())
        stale.mkdir(parents=True)
        (stale / "plugin.json").write_text('{"id": "somethingElse"}')
        with contextlib.redirect_stdout(io.StringIO()):
            install(config_dir=self.directory, system_plugin=system)
        self.assertTrue(stale.exists())

    def test_packaged_install_without_system_plugin_fails_before_changes(self):
        self.packaged()
        with self.assertRaisesRegex(ValueError, "Reinstall"):
            install(config_dir=self.directory, system_plugin=self.directory / "missing")
        self.assertEqual(json.loads((self.directory / "settings.json").read_text()), self.settings)

    def test_real_launcher_copies_files_activates_and_opens_menu(self):
        config = self.directory / "config/DankMaterialShell"
        config.mkdir(parents=True)
        (config / "settings.json").write_text(json.dumps(self.settings))
        binaries = self.directory / "bin"
        binaries.mkdir()
        dms = binaries / "dms"
        dms.write_text('''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
root=Path(os.environ["XDG_CONFIG_HOME"])/"DankMaterialShell"
if args==["ipc","--help"]:
 print("Usage: dms ipc <target> <function> [args...]")
elif args==["ipc","plugins","list"]:
 print("fortiBar [disabled]")
elif args==["ipc","plugins","reload","fortiBar"]:
 assert (root/"plugins/FortiBar/FortiBar.qml").exists()
 assert json.loads((root/"plugin_settings.json").read_text())["fortiBar"]["enabled"]
 print("PLUGIN_RELOAD_SUCCESS: fortiBar")
elif args==["ipc","plugins","status","fortiBar"]:
 print("loaded")
elif args==["ipc","widget","visibility","fortiBar"]:
 assert "fortiBar" in json.loads((root/"settings.json").read_text())["barConfigs"][0]["rightWidgets"]
 print("visible")
elif args==["ipc","widget","openWith","fortiBar","all"]:
 (root/"menu-opened").touch()
 print("WIDGET_OPEN_WITH_SUCCESS: fortiBar all")
else:
 print("Unsupported DMS command: "+repr(args),file=sys.stderr)
 sys.exit(1)
''')
        dms.chmod(0o755)
        environment = {**os.environ, "XDG_CONFIG_HOME": str(config.parent), "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", "")}
        result = subprocess.run([sys.executable, "-m", "fortibar"], env=environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((config / "menu-opened").exists())
        self.assertIn("menu opened", result.stdout)


class DmsActivationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.new_cli = False
        self.reload_reply = "PLUGIN_RELOAD_SUCCESS: fortiBar"
        self.widget_reply = "visible"

    def run_dms(self, args):
        self.calls.append(args)
        if args == ["ipc", "--help"]:
            return 0, ("Available Commands:\n  call  Call an IPC function\n" if self.new_cli else "Usage: dms ipc <target> <function> [args...]")
        command = args[2:] if self.new_cli else args[1:]
        self.assertEqual(args[:2] if self.new_cli else args[:1], ["ipc", "call"] if self.new_cli else ["ipc"])
        target, function = command[:2]
        if target == "plugins" and function == "list": return 0, "fortiBar [disabled]"
        if target == "plugins" and function == "reload": return 0, self.reload_reply
        if target == "plugins" and function == "status": return 0, "loaded"
        if target == "widget" and function == "visibility": return 0, self.widget_reply
        if target == "widget" and function == "openWith": return 0, "WIDGET_OPEN_WITH_SUCCESS: fortiBar all"
        self.fail("Unexpected DMS command: " + repr(args))

    def activate(self):
        with patch("fortibar.dms_install.shutil.which", return_value="/usr/bin/dms"), patch("fortibar.dms_install._run_dms", side_effect=self.run_dms), patch("fortibar.dms_install.time.sleep"):
            activate_widget()

    def test_installed_dms_uses_direct_ipc_syntax_and_opens_menu(self):
        self.activate()
        self.assertIn(["ipc", "plugins", "reload", "fortiBar"], self.calls)
        self.assertIn(["ipc", "widget", "openWith", "fortiBar", "all"], self.calls)
        self.assertNotIn(["ipc", "call", "plugins", "reload", "fortiBar"], self.calls)

    def test_newer_call_subcommand_is_detected_from_help(self):
        self.new_cli = True
        self.activate()
        self.assertIn(["ipc", "call", "plugins", "reload", "fortiBar"], self.calls)

    def test_plugin_load_failure_cannot_be_reported_as_success(self):
        self.reload_reply = "PLUGIN_RELOAD_FAILED: fortiBar"
        with self.assertRaisesRegex(ActivationError, "did not load"):
            self.activate()
        self.assertFalse(any("openWith" in args for args in self.calls))

    def test_missing_plugin_explains_discovery_recovery(self):
        self.reload_reply = "PLUGIN_NOT_FOUND: fortiBar"
        with self.assertRaisesRegex(ActivationError, "--restart-dms"):
            self.activate()
        self.assertFalse(any("openWith" in args for args in self.calls))

    def test_explicit_restart_waits_for_new_shell_before_opening_menu(self):
        from subprocess import CompletedProcess
        original = self.run_dms
        unavailable = [True]
        def starting(args):
            if args == ["ipc", "plugins", "list"] and unavailable[0]:
                unavailable[0] = False
                return 255, "No running DMS instance"
            return original(args)
        with patch("fortibar.dms_install.shutil.which", return_value="/usr/bin/dms"), \
             patch("fortibar.dms_install._run_dms", side_effect=starting), \
             patch("fortibar.dms_install.time.sleep"), \
             patch("fortibar.dms_install.subprocess.run", return_value=CompletedProcess([], 0, "", "")) as restart, \
             contextlib.redirect_stdout(io.StringIO()):
            activate_widget(restart_dms=True)
        self.assertEqual(restart.call_args.args[0], ["systemctl", "--user", "restart", "dms.service"])
        self.assertIn(["ipc", "widget", "openWith", "fortiBar", "all"], self.calls)

    def test_failed_restart_is_reported_before_attempting_activation(self):
        from subprocess import CompletedProcess
        with patch("fortibar.dms_install.shutil.which", return_value="/usr/bin/dms"), \
             patch("fortibar.dms_install._run_dms", side_effect=self.run_dms), \
             patch("fortibar.dms_install.subprocess.run", return_value=CompletedProcess([], 1, "", "Service failed")), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ActivationError, "Service failed"):
                activate_widget(restart_dms=True)
        self.assertFalse(any("openWith" in args for args in self.calls))

    def test_loaded_plugin_without_a_registered_icon_is_failure(self):
        self.widget_reply = "WIDGET_NOT_FOUND: fortiBar"
        with self.assertRaisesRegex(ActivationError, "did not register"):
            self.activate()
        self.assertFalse(any("openWith" in args for args in self.calls))

    def test_socket_access_errors_are_not_silently_ignored(self):
        original = self.run_dms
        def denied(args):
            return original(args) if args == ["ipc", "--help"] else (255, "SocketAccessError")
        self.run_dms = denied
        with self.assertRaisesRegex(ActivationError, "SocketAccessError"):
            self.activate()


@unittest.skipUnless(Path("/usr/share/quickshell/dms").exists(), "Native DMS controls are not installed")
class NativePanelTests(unittest.TestCase):
    def setUp(self):
        from fortibar.dms_preview import PanelPreview
        self.messages = []
        self.handler = qInstallMessageHandler(lambda _kind, _context, message: self.messages.append(message))
        self.preview = PanelPreview(APP)
        QTest.qWait(30)

    def tearDown(self):
        self.preview.close()
        qInstallMessageHandler(self.handler)

    def assert_no_errors(self):
        self.assertEqual([message for message in self.messages if "Error:" in message or "Cannot assign" in message], [])

    def test_menu_is_compact_and_uses_native_controls(self):
        self.assertLess(self.preview.view.height(), 350)
        self.assertEqual(self.preview.panel.property("title"), "VPN")
        self.assertFalse(self.preview.view.grabWindow().isNull())
        self.assert_no_errors()

    def test_edit_activity_password_and_certificate_are_pages_in_same_panel(self):
        panel = self.preview.panel
        original = self.preview.view
        panel.edit(self.preview.backend.controller.selected_profile().to_dict())
        QTest.qWait(15)
        self.assertEqual(panel.property("page"), "edit")
        panel.setProperty("page", "activity")
        self.preview.backend.passwordRequested.emit("Work")
        self.assertEqual(panel.property("page"), "password")
        self.preview.backend.certificateRequested.emit()
        self.assertEqual(panel.property("page"), "certificate")
        self.assertIs(self.preview.view, original)
        self.assert_no_errors()

    def test_widget_and_bridge_qml_compile_against_shell_contract(self):
        import shutil
        from PySide6.QtCore import QUrl
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        root = Path(self.preview.temp.name)
        # Quickshell links these plugins into its executable; PySide cannot
        # load them. Match their installed qmltypes contract for syntax checks.
        quickshell = root / "Quickshell"
        io_module = quickshell / "Io"
        io_module.mkdir(parents=True)
        (quickshell / "qmldir").write_text("module Quickshell\nSingleton 1.0 Singleton.qml\n")
        (quickshell / "Singleton.qml").write_text("import QtQuick\nItem {}")
        (io_module / "qmldir").write_text("module Quickshell.Io\nProcess 1.0 Process.qml\nFileView 1.0 FileView.qml\nSplitParser 1.0 SplitParser.qml\n")
        (io_module / "Process.qml").write_text('import QtQuick\nQtObject { property var command; property bool running; property bool stdinEnabled; property QtObject stdout; signal exited(int code,int status); function write(data) {} function signal(value) {} }')
        (io_module / "FileView.qml").write_text('import QtQuick\nQtObject { property string path; property bool preload; property bool printErrors; signal loaded(); signal loadFailed(int error); function text() { return "[]" } }')
        (io_module / "SplitParser.qml").write_text('import QtQuick\nQtObject { signal read(string data) }')
        services = root / "qs/Services"
        services.mkdir()
        (services / "qmldir").write_text("module qs.Services\nsingleton ToastService 1.0 ToastService.qml\n")
        (services / "ToastService.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { function showInfo(title,body) {} }')
        plugins = root / "qs/Modules/Plugins"
        (plugins / "PluginComponent.qml").write_text('import QtQuick\nItem { property string layerNamespacePlugin; property int iconSize:20; property real popoutWidth; property Component horizontalBarPill; property Component verticalBarPill; property Component popoutContent }')
        with (plugins / "qmldir").open("a") as file:
            file.write("PluginComponent 1.0 PluginComponent.qml\n")
        copied = root / "NativePlugin"
        shutil.copytree(Path(__file__).resolve().parent.parent / "dms/FortiBar", copied)
        handler = qInstallMessageHandler(None)
        engine = QQmlEngine()
        engine.addImportPath(str(root))
        try:
            component = QQmlComponent(engine, QUrl.fromLocalFile(str(copied / "FortiBar.qml")))
        finally:
            qInstallMessageHandler(handler)
        self.assertEqual(component.status(), QQmlComponent.Ready, "\n".join(e.toString() for e in component.errors()))
        engine.deleteLater()
