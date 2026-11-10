"""Exercise installed DMS discovery in the real offscreen Quickshell engine."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


DMS = Path("/usr/share/quickshell/dms")


@unittest.skipUnless(shutil.which("quickshell") and DMS.exists(), "DMS and Quickshell are required")
class RealDiscoveryTests(unittest.TestCase):
    def test_native_quickshell_bridge_starts_and_handles_menu_requests(self):
        with tempfile.TemporaryDirectory(prefix="forti-native-bridge-") as temporary:
            root = Path(temporary)
            for name in ("Services", "Native", "config", "runtime"):
                (root / name).mkdir(mode=0o700)
            (root / "Services/ToastService.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { function showInfo(title,body) {} }\n')
            source = Path(__file__).resolve().parent.parent
            for filename in ("Backend.qml", "qmldir"):
                shutil.copy2(source / "dms/FortiBar" / filename, root / "Native" / filename)
            (root / "Native/backend.json").write_text(json.dumps([sys.executable, str(source / "forti-bar"), "--dms-bridge", "--demo"]))
            (root / "shell.qml").write_text('''import QtQuick
import Quickshell
import "Native" as Forti
ShellRoot {
    Component.onCompleted: Forti.Backend.attachWidget()
    Timer { interval: 100; running: true; repeat: true; onTriggered: {
        if (Forti.Backend.available) {
            stop()
            console.warn("BRIDGE_READY", Forti.Backend.state.status)
            Forti.Backend.request("newProfile", [])
        }
    }}
    Connections { target: Forti.Backend; function onEditRequested(profile) {
        console.warn("BRIDGE_MENU_EDIT", profile.id)
        Forti.Backend.detachWidget()
        finish.start()
    }}
    Timer { id: finish; interval: 500; onTriggered: {
        console.warn("BRIDGE_STOPPED", !Forti.Backend.available)
        Qt.quit()
    }}
    Timer { interval: 4000; running: true; onTriggered: Qt.quit() }
}
''')
            environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
                           "XDG_CONFIG_HOME": str(root / "config"), "XDG_RUNTIME_DIR": str(root / "runtime")}
            result = subprocess.run(["quickshell", "--no-color", "-p", str(root)], env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=6)
            self.assertEqual(result.returncode, 0, result.stdout)
            for marker in ("BRIDGE_READY disconnected", "BRIDGE_MENU_EDIT", "BRIDGE_STOPPED true"):
                self.assertIn(marker, result.stdout, result.stdout)

    def test_directory_created_after_startup_needs_rescan(self):
        with tempfile.TemporaryDirectory(prefix="forti-discovery-test-") as temporary:
            root = Path(temporary)
            for name in ("Common", "Services", "config", "runtime"):
                (root / name).mkdir(mode=0o700)
            # Use the installed discovery implementation and native FolderListModel
            # and FileView. Only unrelated settings and path helpers are fixtures.
            shutil.copy2(DMS / "Services/PluginService.qml", root / "Services/PluginService.qml")
            (root / "Common/Paths.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { function toFileUrl(path) {return "file://"+path} }\n')
            (root / "Common/SettingsData.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { function getPluginSetting(id,key,fallback) {return fallback} }\n')
            manifest = Path(__file__).resolve().parent.parent / "dms/FortiBar/plugin.json"
            destination = root / "config/DankMaterialShell/plugins/FortiBar"
            create = f"from pathlib import Path; import shutil; p=Path({str(destination)!r}); p.mkdir(parents=True); shutil.copy2({str(manifest)!r}, p/'plugin.json')"
            qml = '''import QtQuick
import Quickshell
import Quickshell.Io
import qs.Services
ShellRoot {
    Component.onCompleted: console.warn("DISCOVERY_DIR", PluginService.pluginDirectory)
    Timer { interval: 150; running: true; onTriggered: {
        console.warn("DISCOVERY_BEFORE", PluginService.getAvailablePlugins().length)
        creator.running = true
    }}
    Process {
        id: creator
        command: CREATE_COMMAND
        onExited: { console.warn("DISCOVERY_CREATED", exitCode); afterCreate.start() }
    }
    Timer { id: afterCreate; interval: 300; onTriggered: {
        console.warn("DISCOVERY_AFTER_CREATE", PluginService.getAvailablePlugins().length)
        PluginService.scanPlugins()
        afterScan.start()
    }}
    Timer { id: afterScan; interval: 400; onTriggered: {
        console.warn("DISCOVERY_AFTER_SCAN", JSON.stringify(PluginService.getAvailablePlugins().map(p => p.id)))
        Qt.quit()
    }}
    Timer { interval: 4000; running: true; onTriggered: Qt.quit() }
}
'''.replace("CREATE_COMMAND", json.dumps([sys.executable, "-c", create]))
            (root / "shell.qml").write_text(qml)
            environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
                           "XDG_CONFIG_HOME": str(root / "config"), "XDG_RUNTIME_DIR": str(root / "runtime")}
            result = subprocess.run(["quickshell", "--no-color", "-p", str(root)], env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=6)
            self.assertEqual(result.returncode, 0, result.stdout)
            for marker in ("DISCOVERY_BEFORE 0", "DISCOVERY_CREATED 0", "DISCOVERY_AFTER_CREATE 0", 'DISCOVERY_AFTER_SCAN ["fortiBar"]'):
                self.assertIn(marker, result.stdout, result.stdout)
