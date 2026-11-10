"""One event-driven source of connection state for both the window and tray."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from PySide6.QtCore import QObject, Property, QProcess, QSaveFile, QSettings, QStandardPaths, Qt, QTimer, QUrl, Signal, Slot, QIODevice
from PySide6.QtDBus import QDBusConnection

from .helper import redact, root_owned_executable
from .model import MAX_CA_BYTES, Profile, ProfileStore, import_config, validate_password
from .secrets import SecretStore

HELPER_PATH = "/usr/libexec/forti-bar-helper"
ERRORS = {
    "authentication": "The gateway rejected your login. Check your username and password.",
    "certificate": "The gateway certificate could not be verified. Review it before connecting.",
    "network": "The VPN gateway could not be reached. Check your network and server address.",
    "timeout": "The connection took too long. Check the gateway and try again.",
    "tunnel": "The VPN tunnel stopped unexpectedly. Connection details are in Activity.",
    "unsupported-auth": "This gateway requires another authentication method. This version supports username and password.",
    "busy": "Another Forti Bar connection is already running.",
}


def quoted_exec(value: str) -> str:
    if any(c in value for c in "\n\r\0"):
        raise ValueError("Invalid executable path.")
    value = value.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


class Controller(QObject):
    changed = Signal()
    logsChanged = Signal()
    elapsedChanged = Signal()
    showRequested = Signal()
    editRequested = Signal("QVariantMap")
    passwordRequested = Signal(str)
    certificateRequested = Signal()
    notification = Signal(str, str)
    quitReady = Signal()

    def __init__(self, *, demo=False, config_dir=None):
        super().__init__()
        self.demo = demo
        directory = config_dir or Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
        self.store = ProfileStore(Path(directory))
        self._status = "disconnected"
        self._error = ""
        self._notice = ""
        self._logs: list[str] = []
        self._certificate: dict = {}
        self._local_ip = ""
        self._interface = ""
        self._start_time = 0.0
        self._visible = False
        self._pending_quit = False
        self._cancelled = False
        self._suspended = False
        self._retry_count = 0
        self._retry_enabled = False
        self.preferences = None if demo else QSettings(str(Path(directory) / "preferences.ini"), QSettings.IniFormat)
        if self.preferences:
            self._retry_enabled = self.preferences.value("reconnect", False, type=bool)
        self._failure = ""
        self._load_failed = False
        self._session_passwords: dict[str, str] = {}
        self._secret_requests: dict[str, tuple[str, str]] = {}
        self._connecting_id = ""
        self._helper_seen = False
        self._certificate_profile = ""
        self._output = bytearray()
        self._stderr = bytearray()
        self.secrets = SecretStore(self)
        self.secrets.completed.connect(self.secret_completed)
        if demo:
            p = Profile.parse({"id": str(uuid.uuid4()), "name": "Work network", "host": "vpn.example.com",
                               "port": 443, "username": "jesse", "remember_password": False})
            self.store.profiles = [p]
            self.store.selected = p.id
        else:
            try:
                self.store.load()
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self._error = f"Could not read saved profiles: {exc}"
                self._load_failed = True
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.started.connect(self.process_started)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.elapsedChanged)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.retry_connection)
        self.demo_timer = QTimer(self)
        self.demo_timer.setSingleShot(True)
        self.demo_timer.timeout.connect(self.demo_connected)
        bus = QDBusConnection.systemBus()
        if bus.isConnected():
            bus.connect("org.freedesktop.login1", "/org/freedesktop/login1",
                        "org.freedesktop.login1.Manager", "PrepareForSleep", self, SLOT_SLEEP)

    def selected_profile(self) -> Profile | None:
        return next((p for p in self.store.profiles if p.id == self.store.selected), None)

    @Property("QVariantList", notify=changed)
    def profiles(self):
        return [{"id": p.id, "name": p.name, "endpoint": p.endpoint} for p in self.store.profiles]

    @Property("QVariantMap", notify=changed)
    def selected(self):
        profile = self.selected_profile()
        return {**profile.to_dict(), "endpoint": profile.endpoint} if profile else {}

    status = Property(str, lambda self: self._status, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    notice = Property(str, lambda self: self._notice, notify=changed)
    localIp = Property(str, lambda self: self._local_ip, notify=changed)
    interfaceName = Property(str, lambda self: self._interface, notify=changed)
    certificate = Property("QVariantMap", lambda self: self._certificate, notify=changed)
    busy = Property(bool, lambda self: self._status in ("connecting", "connected", "disconnecting", "reconnecting", "unlocking"), notify=changed)
    isDemo = Property(bool, lambda self: self.demo, constant=True)
    hasProfiles = Property(bool, lambda self: bool(self.store.profiles), notify=changed)
    logs = Property(str, lambda self: "\n".join(self._logs), notify=logsChanged)
    retryEnabled = Property(bool, lambda self: self._retry_enabled, notify=changed)

    @Property(str, notify=elapsedChanged)
    def elapsed(self):
        seconds = int(time.monotonic() - self._start_time) if self._start_time else 0
        return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"

    @Property(bool, notify=changed)
    def setupReady(self):
        return self.demo or (root_owned_executable(HELPER_PATH) and bool(shutil.which("openfortivpn")))

    @Property(str, constant=True)
    def setupCommand(self):
        source = Path(__file__).resolve().parent.parent / "scripts/install.sh"
        if shutil.which("rpm-ostree"):
            return "sudo rpm-ostree install openfortivpn\n# Install the Forti Bar RPM, then reboot."
        return "sudo " + str(source) if source.exists() else "Install forti-bar and openfortivpn on the host."

    @Property(bool, notify=changed)
    def autostart(self):
        return self.autostart_path().exists()

    @staticmethod
    def autostart_path():
        return Path(QStandardPaths.writableLocation(QStandardPaths.ConfigLocation)) / "autostart/io.github.fortibar.desktop"

    def state(self, status):
        self._status = status
        if status != "connected":
            self.clock.stop()
        elif self._visible:
            self.clock.start()
        self.changed.emit()

    def log(self, text):
        text = redact(text)
        self._logs = (self._logs + [datetime.now().strftime("%H:%M:%S") + "  " + text])[-250:]
        while len("\n".join(self._logs).encode()) > 65536:
            self._logs.pop(0)
        self.logsChanged.emit()

    def persist(self):
        if self.demo:
            return True
        try:
            if self._load_failed:
                raise ValueError("The existing profiles file could not be read. Move it aside before saving new profiles.")
            self.store.save()
            return True
        except (ValueError, OSError) as exc:
            self._error = "Could not save profiles: " + str(exc)
            self.changed.emit()
            return False

    @Slot(str)
    def selectProfile(self, profile_id):
        if self.busy or profile_id not in {p.id for p in self.store.profiles}:
            return
        self.store.selected = profile_id
        self._error = ""
        self._certificate = {}
        self._certificate_profile = ""
        self.persist()
        self.changed.emit()

    @Slot()
    def newProfile(self):
        if not self.busy:
            self.editRequested.emit({"id": str(uuid.uuid4()), "name": "", "host": "", "port": 443,
                                     "username": "", "realm": "", "trusted_certs": [], "ca_pem": "",
                                     "ca_name": "", "remember_password": False})

    @Slot()
    def editProfile(self):
        profile = self.selected_profile()
        if profile and not self.busy:
            self.editRequested.emit(profile.to_dict())

    @Slot("QVariantMap", str, bool, result=bool)
    def saveProfile(self, raw, password, connect_after):
        if self.busy:
            return False
        try:
            profile = Profile.parse(raw)
            if password:
                validate_password(password)
        except (ValueError, TypeError) as exc:
            self._error = str(exc)
            self.changed.emit()
            return False
        old = next((p for p in self.store.profiles if p.id == profile.id), None)
        identity_changed = old and (old.host, old.port, old.username, old.realm) != (profile.host, profile.port, profile.username, profile.realm)
        original, previous_selected = list(self.store.profiles), self.store.selected
        self.store.profiles = [profile if p.id == profile.id else p for p in self.store.profiles]
        if not old:
            self.store.profiles.append(profile)
        self.store.selected = profile.id
        if not self.persist():
            self.store.profiles, self.store.selected = original, previous_selected
            return False
        self._error = ""
        self._notice = ""
        if identity_changed:
            self._session_passwords.pop(profile.id, None)
        if password:
            self._session_passwords[profile.id] = password
        if not self.demo:
            if profile.remember_password and password:
                self.request_secret("set", profile.id, password)
            elif (old and old.remember_password and not profile.remember_password) or identity_changed:
                # Avoid racing a delete with a replacement for the same identity.
                self.request_secret("delete", profile.id)
        self.log(f"Saved profile {profile.name}.")
        self.changed.emit()
        if connect_after:
            QTimer.singleShot(0, self.connectVpn)
        return True

    @Slot(result=bool)
    def deleteProfile(self):
        profile = self.selected_profile()
        if self.busy or not profile:
            return False
        original, previous_selected = list(self.store.profiles), self.store.selected
        self.store.profiles = [p for p in self.store.profiles if p.id != profile.id]
        self.store.selected = self.store.profiles[0].id if self.store.profiles else ""
        if not self.persist():
            self.store.profiles, self.store.selected = original, previous_selected
            return False
        self._session_passwords.pop(profile.id, None)
        if not self.demo:
            self.request_secret("delete", profile.id)
        self.changed.emit()
        return True

    def request_secret(self, action, profile_id, password=""):
        token = str(uuid.uuid4())
        self._secret_requests[token] = (action, profile_id)
        self.secrets.request(token, action, profile_id, password)

    @Slot(str, str, str, str)
    def secret_completed(self, token, action, value, error):
        request = self._secret_requests.pop(token, None)
        if not request:
            return
        _action, profile_id = request
        if action == "get":
            if self._connecting_id != profile_id or self._cancelled or self._status != "unlocking":
                return
            if value:
                self._session_passwords[profile_id] = value
                self.start_connection(value)
            else:
                self.state("disconnected")
                self._notice = error
                self.passwordRequested.emit(self.selected_profile().name)
        elif error:
            self._notice = error
            if action == "set":
                for profile in self.store.profiles:
                    if profile.id == profile_id:
                        profile.remember_password = False
                self.persist()
        self.changed.emit()

    @Slot()
    def connectVpn(self):
        if self.busy:
            return
        profile = self.selected_profile()
        if not profile:
            self.newProfile()
            return
        if not self.setupReady:
            self._error = "Install the host helper and openfortivpn to connect. Open Settings for the setup command."
            self.changed.emit()
            return
        self._cancelled = False
        self._error = ""
        self._failure = ""
        self._certificate = {}
        self._connecting_id = profile.id
        if self.demo:
            self.state("connecting")
            self.demo_timer.start(900)
            return
        password = self._session_passwords.get(profile.id, "")
        if password:
            self.start_connection(password)
        elif profile.remember_password:
            self.state("unlocking")
            self.request_secret("get", profile.id)
        else:
            self.passwordRequested.emit(profile.name)

    @Slot(str, bool)
    def providePassword(self, password, remember):
        profile = self.selected_profile()
        if not profile or self.busy:
            return
        try:
            validate_password(password)
        except ValueError as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._session_passwords[profile.id] = password
        old_remember = profile.remember_password
        profile.remember_password = remember
        self.persist()
        if not self.demo and remember:
            self.request_secret("set", profile.id, password)
        elif not self.demo and old_remember:
            self.request_secret("delete", profile.id)
        self.connectVpn()

    def start_connection(self, password):
        profile = self.selected_profile()
        if not profile or self._cancelled:
            return
        self._request = {"profile": profile.to_dict(), "password": password}
        self._output.clear()
        self._stderr.clear()
        self._helper_seen = False
        self.log(f"Connecting to {profile.endpoint}.")
        self.state("connecting")
        self.process.start("/usr/bin/pkexec", [HELPER_PATH])

    @Slot()
    def process_started(self):
        if getattr(self, "_request", None):
            payload = json.dumps(self._request, ensure_ascii=True).encode() + b"\n"
            self.process.write(payload)
            self._request = None
        if self._cancelled:
            self.process.write(b'{"action":"disconnect"}\n')

    @Slot()
    def read_output(self):
        self._output.extend(bytes(self.process.readAllStandardOutput()))
        if len(self._output) > 128 * 1024:
            self._output.clear()
            self._failure = "protocol"
            self._error = "The VPN helper returned an invalid response."
            self.disconnectVpn()
            return
        while b"\n" in self._output:
            line, _, tail = self._output.partition(b"\n")
            self._output = bytearray(tail)
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError()
                self.backend_event(event)
            except (ValueError, KeyError, TypeError):
                self.log("Ignored an invalid helper response.")

    def backend_event(self, event):
        self._helper_seen = True
        if event.get("type") == "log":
            message = str(event.get("message", ""))[:4096]
            for password in self._session_passwords.values():
                message = redact(message, password)
            self.log(message)
        elif event.get("type") == "state" and event.get("state") == "connected" and not self._cancelled:
            self._local_ip = str(event.get("local_ip", ""))
            self._interface = str(event.get("interface", ""))
            self._start_time = time.monotonic()
            self._retry_count = 0
            self.state("connected")
            self.elapsedChanged.emit()
            self.notification.emit("VPN connected", self.selected_profile().name)
        elif event.get("type") == "error":
            category = str(event.get("category", "tunnel"))
            self._failure = category
            self._error = ERRORS.get(category, str(event.get("message", "The VPN could not start.")))
            if category == "certificate":
                candidate = event.get("certificate", {})
                if isinstance(candidate, dict):
                    self._certificate = {k: str(candidate.get(k, "")) for k in ("fingerprint", "subject", "issuer")}
                    self._certificate_profile = self._connecting_id
            self.log(self._error)
            self.changed.emit()

    @Slot()
    def read_stderr(self):
        self._stderr.extend(bytes(self.process.readAllStandardError()))
        self._stderr = self._stderr[-8192:]

    @Slot(int, QProcess.ExitStatus)
    def process_finished(self, code, _exit_status):
        self.read_output()
        was_connected = bool(self._start_time)
        self._start_time = 0
        self._local_ip = ""
        self._interface = ""
        self.state("disconnected")
        self.elapsedChanged.emit()
        if not self._cancelled and not self._failure:
            self._failure = "authorization" if code in (126, 127) else "tunnel"
            self._error = ("Authorization was cancelled or unavailable. Check that your desktop's polkit agent is running."
                           if self._failure == "authorization" else ERRORS["tunnel"])
            self.log(self._error)
        elif self._cancelled:
            self.log("Disconnected. Network settings released.")
        if self._pending_quit:
            self.quitReady.emit()
            return
        if self._certificate.get("fingerprint"):
            self.showRequested.emit()
            self.certificateRequested.emit()
        elif (self._retry_enabled and not self._cancelled and not self._suspended
              and self._failure in ("network", "timeout", "tunnel") and self._retry_count < 3):
            self._retry_count += 1
            self.state("reconnecting")
            self.retry.start((2, 5, 15)[self._retry_count - 1] * 1000)
        elif self._error and not self._cancelled:
            self.notification.emit("VPN connection failed", self._error)
        elif was_connected:
            self.notification.emit("VPN disconnected", "Your VPN connection has ended.")
        self.changed.emit()

    @Slot(QProcess.ProcessError)
    def process_error(self, error):
        if error != QProcess.FailedToStart:
            return
        self._request = None
        self._error = "Could not start pkexec. Install polkit and the Forti Bar host helper."
        self.state("disconnected")
        if self._pending_quit:
            self.quitReady.emit()

    @Slot()
    def disconnectVpn(self):
        self._cancelled = True
        self.retry.stop()
        self.demo_timer.stop()
        if self.demo or self.process.state() == QProcess.NotRunning:
            self._start_time = 0
            self._local_ip = ""
            self._interface = ""
            self.state("disconnected")
            self.elapsedChanged.emit()
            if self._pending_quit:
                self.quitReady.emit()
            return
        self.state("disconnecting")
        self.process.write(b'{"action":"disconnect"}\n')
        self.process.closeWriteChannel()
        # Before pkexec starts the helper it may still be waiting for a polkit
        # dialog. It belongs to our user then and can be cancelled directly.
        if not self._helper_seen:
            self.process.terminate()

    @Slot()
    def toggleConnection(self):
        if self.busy:
            self.disconnectVpn()
        else:
            self._retry_count = 0
            self.connectVpn()

    @Slot()
    def retry_connection(self):
        self.state("disconnected")
        self.connectVpn()

    @Slot()
    def demo_connected(self):
        self._local_ip = "10.42.0.8"
        self._interface = "fb-preview"
        self._start_time = time.monotonic()
        self.log("Preview connected. No VPN or network changes were made.")
        self.state("connected")

    @Slot(bool)
    def prepareForSleep(self, sleeping):
        self._suspended = sleeping
        if sleeping and self.busy:
            resume = self._retry_enabled
            self.disconnectVpn()
            self._resume_after_sleep = resume
        elif not sleeping and getattr(self, "_resume_after_sleep", False):
            self._resume_after_sleep = False
            self.retry.start(2500)

    @Slot(bool)
    def setRetryEnabled(self, enabled):
        self._retry_enabled = enabled
        if self.preferences:
            self.preferences.setValue("reconnect", enabled)
            self.preferences.sync()
            if self.preferences.status() != QSettings.NoError:
                self._error = "Could not save the reconnection preference."
        if not enabled and self._status == "reconnecting":
            self.disconnectVpn()
        self.changed.emit()

    @Slot(bool)
    def setVisible(self, visible):
        self._visible = visible
        if visible and self._status == "connected":
            self.clock.start()
        else:
            self.clock.stop()

    @Slot()
    def trustCertificate(self):
        profile = self.selected_profile()
        fingerprint = self._certificate.get("fingerprint", "")
        if not profile or self.busy or not fingerprint or profile.id != self._certificate_profile:
            return
        try:
            raw = profile.to_dict()
            raw["trusted_certs"] = list(profile.trusted_certs) + [fingerprint]
            validated = Profile.parse(raw)
        except ValueError:
            return
        original = self.store.profiles
        self.store.profiles = [validated if p.id == profile.id else p for p in original]
        if not self.persist():
            self.store.profiles = original
            return
        self._certificate = {}
        self._error = ""
        self.log("Added the explicitly approved certificate fingerprint.")
        self.changed.emit()
        self.connectVpn()

    @Slot()
    def clearError(self):
        self._error = ""
        self._notice = ""
        self.changed.emit()

    @Slot(result="QVariantMap")
    def chooseCa(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(None, "Choose a CA certificate", "", "PEM certificates (*.pem *.crt *.cer);;All files (*)")
        if not path:
            return {}
        try:
            if Path(path).stat().st_size > MAX_CA_BYTES:
                raise ValueError("The CA bundle must be smaller than 256 KiB.")
            pem = Path(path).read_text()
            import ssl
            ssl.create_default_context().load_verify_locations(cadata=pem)
            return {"pem": pem, "name": Path(path).name}
        except (OSError, ValueError) as exc:
            self._error = "Could not read the certificate: " + str(exc)
            self.changed.emit()
            return {}

    @Slot()
    def importProfile(self):
        from PySide6.QtWidgets import QFileDialog
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(None, "Import an openfortivpn config", "", "VPN configs (*.conf *.config);;All files (*)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 1024 * 1024:
                raise ValueError("The configuration file is too large.")
            result = import_config(Path(path).read_text())
            profile = result["profile"]
            profile["name"] = Path(path).stem.replace("-", " ").title()[:80] or "Imported VPN"
            self._notice = ("Imported connection fields. Enter your password below."
                            + (" Other backend options were skipped: " + ", ".join(result["ignored"]) if result["ignored"] else ""))
            self.editRequested.emit(profile)
            self.changed.emit()
        except (OSError, ValueError, UnicodeError) as exc:
            self._error = "Could not import the config: " + str(exc)
            self.changed.emit()

    @Slot()
    def exportLogs(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(None, "Save redacted activity", "forti-bar-activity.txt", "Text files (*.txt)")
        if path:
            try:
                Path(path).write_text("\n".join(self._logs) + "\n")
                os.chmod(path, 0o600)
            except OSError as exc:
                self._error = "Could not save activity: " + str(exc)
                self.changed.emit()

    @Slot()
    def copyLogs(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText("\n".join(self._logs))

    @Slot(bool)
    def setAutostart(self, enabled):
        if self.demo:
            return
        try:
            path = self.autostart_path()
            if enabled:
                launcher = Path("/usr/bin/forti-bar")
                if not launcher.exists():
                    launcher = Path(__file__).resolve().parent.parent / "forti-bar"
                path.parent.mkdir(parents=True, exist_ok=True)
                text = ("[Desktop Entry]\nType=Application\nName=Forti Bar\n"
                        f"Exec={quoted_exec(str(launcher))} --background\n"
                        "Icon=io.github.fortibar\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
                file = QSaveFile(str(path))
                if not file.open(QIODevice.WriteOnly) or file.write(text.encode()) < 0 or not file.commit():
                    raise OSError("Could not write the autostart entry.")
            else:
                path.unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            self._error = "Could not update login startup: " + str(exc)
        self.changed.emit()

    @Slot()
    def requestQuit(self):
        self._pending_quit = True
        self._session_passwords.clear()
        self.secrets.cancel_all()
        if self.busy:
            self.disconnectVpn()
        else:
            self.quitReady.emit()


# QtDBus's connect overload expects a C++ slot signature, not a bound Python
# callable. Keep this declaration here so it is tested with the installed Qt.
from PySide6.QtCore import SLOT
SLOT_SLEEP = SLOT("prepareForSleep(bool)")
