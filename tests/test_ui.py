import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QEvent, QObject, QTimer, Signal, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fortibar.controller import Controller, quoted_exec
from fortibar.theme import Appearance, DEFAULTS
from tests.test_model import profile

APP = QApplication.instance() or QApplication([])
APP.setApplicationName("forti-bar-tests")


class FakeSecrets(QObject):
    completed = Signal(str, str, str, str)

    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, token, action, profile_id, value=""):
        self.calls.append((token, action, profile_id, value))

    def cancel_all(self):
        pass


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = Controller(config_dir=Path(self.temp.name))
        self.controller.secrets = FakeSecrets()
        self.controller.secrets.completed.connect(self.controller.secret_completed)

    def tearDown(self):
        self.controller.secrets.cancel_all()
        self.controller.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_save_keeps_password_only_in_session(self):
        c = self.controller
        self.assertTrue(c.saveProfile(profile().to_dict(), "secret123", False))
        self.assertNotIn("secret123", c.store.path.read_text())
        self.assertNotIn("secret123", json.dumps(c.profiles))
        self.assertEqual(c._session_passwords[c.store.selected], "secret123")

    def test_changed_identity_forgets_previous_password(self):
        c = self.controller
        p = profile(remember_password=True)
        c.saveProfile(p.to_dict(), "old-secret", False)
        raw = p.to_dict()
        raw["host"] = "different.example.com"
        c.saveProfile(raw, "", False)
        self.assertNotIn(p.id, c._session_passwords)
        self.assertEqual(c.secrets.calls[-1][1], "delete")

    def test_keyring_completion_after_cancel_never_connects(self):
        c = self.controller
        p = profile(remember_password=True)
        c.saveProfile(p.to_dict(), "", False)
        starts = []
        c.start_connection = starts.append
        c._connecting_id = p.id
        c.state("unlocking")
        c.request_secret("get", p.id)
        token = c.secrets.calls[-1][0]
        c.disconnectVpn()
        c.secrets.completed.emit(token, "get", "secret", "")
        self.assertEqual(starts, [])
        self.assertEqual(c.status, "disconnected")

    def test_certificate_cannot_be_trusted_for_another_profile(self):
        c = self.controller
        one, two = profile(name="One"), profile(name="Two", host="other.example.com")
        c.saveProfile(one.to_dict(), "", False)
        c.saveProfile(two.to_dict(), "", False)
        c._certificate = {"fingerprint": "ab" * 32}
        c._certificate_profile = one.id
        c.trustCertificate()
        self.assertEqual(c.selected_profile().trusted_certs, [])

    def test_disabled_keyring_remember_deletes_saved_secret(self):
        c = self.controller
        p = profile(remember_password=True)
        c.saveProfile(p.to_dict(), "secret", False)
        raw = p.to_dict()
        raw["remember_password"] = False
        c.saveProfile(raw, "", False)
        self.assertEqual(c.secrets.calls[-1][1], "delete")

    def test_reconnect_preference_survives_restart(self):
        c = self.controller
        c.setRetryEnabled(True)
        other = Controller(config_dir=Path(self.temp.name))
        self.assertTrue(other.retryEnabled)
        other.secrets.cancel_all()
        other.deleteLater()

    def test_profile_save_failure_does_not_change_selection(self):
        c = self.controller
        c.saveProfile(profile().to_dict(), "", False)
        previous = c.store.selected
        with patch.object(c.store, "save", side_effect=OSError("read only")):
            self.assertFalse(c.saveProfile(profile(name="Other").to_dict(), "", False))
        self.assertEqual(c.store.selected, previous)
        self.assertEqual(len(c.store.profiles), 1)

    def test_desktop_exec_escapes_paths(self):
        self.assertEqual(quoted_exec('/a path/50%"x'), '"/a path/50%%\\"x"')
        with self.assertRaises(ValueError):
            quoted_exec("bad\npath")

    def test_sleep_subscription_accepts_the_qt_slot_signature(self):
        from PySide6.QtDBus import QDBusConnection
        from fortibar.controller import SLOT_SLEEP
        bus = QDBusConnection("forti-bar-unconnected-test")
        self.assertFalse(bus.connect("org.freedesktop.login1", "/org/freedesktop/login1",
                                    "org.freedesktop.login1.Manager", "PrepareForSleep",
                                    self.controller, SLOT_SLEEP))


if __name__ == "__main__":
    unittest.main()
