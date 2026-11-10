"""Headless controller owned by DMS. Its standard streams never create a window."""

import json
import os
from pathlib import Path
import signal
import sys

from PySide6.QtCore import QCoreApplication, QObject, QSocketNotifier, Signal

from .controller import Controller
from .model import MAX_CA_BYTES

PROPERTIES = ("profiles", "selected", "status", "error", "notice", "localIp", "interfaceName",
              "certificate", "busy", "hasProfiles", "logs", "retryEnabled", "elapsed", "setupReady", "setupCommand")
METHODS = {"saveProfile", "deleteProfile", "selectProfile", "newProfile", "editProfile", "toggleConnection",
           "providePassword", "setRetryEnabled", "trustCertificate", "clearError", "setVisible"}


class DmsBridge(QObject):
    def __init__(self, app, controller, *, writer=None):
        super().__init__()
        self.app, self.controller = app, controller
        self.writer = writer or self.write
        self.buffer = bytearray()
        self.notifier = None
        self.closed = False
        controller.changed.connect(self.snapshot)
        controller.logsChanged.connect(self.snapshot)
        controller.elapsedChanged.connect(self.snapshot)
        controller.editRequested.connect(lambda profile: self.writer({"event": "edit", "data": profile}))
        controller.passwordRequested.connect(lambda name: self.writer({"event": "password", "data": name}))
        controller.certificateRequested.connect(lambda: self.writer({"event": "certificate"}))
        controller.notification.connect(lambda title, body: self.writer({"event": "notification", "data": [title, body]}))
        controller.quitReady.connect(app.quit)

    def write(self, message):
        if self.closed:
            return
        try:
            sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            self.closed = True
            self.controller.requestQuit()

    def snapshot(self):
        self.writer({"snapshot": {name: getattr(self.controller, name) for name in PROPERTIES}})

    def dispatch(self, request):
        request_id = request.get("id") if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict) or set(request) - {"id", "method", "args"}:
                raise ValueError("Invalid DMS request.")
            method, args = request.get("method"), request.get("args", [])
            if not isinstance(args, list) or len(args) > 3:
                raise ValueError("Invalid DMS arguments.")
            if method == "readCa" and len(args) == 1 and isinstance(args[0], str):
                path = Path(args[0]).expanduser()
                if path.stat().st_size > MAX_CA_BYTES:
                    raise ValueError("The CA bundle must be smaller than 256 KiB.")
                pem = path.read_text()
                import ssl
                ssl.create_default_context().load_verify_locations(cadata=pem)
                result = {"pem": pem, "name": path.name}
            elif method in METHODS:
                result = getattr(self.controller, method)(*args)
            else:
                raise ValueError("Unsupported DMS action.")
            self.writer({"reply": request_id, "result": result})
            self.snapshot()
        except (ValueError, TypeError, OSError) as exc:
            self.controller._error = str(exc)
            self.writer({"reply": request_id, "result": False})
            self.snapshot()

    def attach(self, fd=0):
        os.set_blocking(fd, False)
        self.notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
        self.notifier.activated.connect(lambda *_args: self.read(fd))

    def read(self, fd):
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            return
        if not chunk:
            self.notifier.setEnabled(False)
            self.controller.requestQuit()
            return
        self.buffer.extend(chunk)
        if len(self.buffer) > 2 * 1024 * 1024:
            self.notifier.setEnabled(False)
            self.controller.requestQuit()
            return
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                self.dispatch(json.loads(line))
            except ValueError:
                self.writer({"reply": None, "result": False})
            except ConnectionError:
                self.controller.requestQuit()


def main(argv=None, *, demo=False):
    app = QCoreApplication(argv or ["forti-bar"])
    app.setApplicationName("forti-bar")
    app.setOrganizationName("FortiBar")
    controller = Controller(demo=demo)
    bridge = DmsBridge(app, controller)
    bridge.attach()
    read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    previous_wakeup = signal.set_wakeup_fd(write_fd)
    wakeup = QSocketNotifier(read_fd, QSocketNotifier.Read, app)
    wakeup.activated.connect(lambda *_args: os.read(read_fd, 4096))
    handlers = {sig: signal.signal(sig, lambda *_args: controller.requestQuit())
                for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        bridge.snapshot()
        return app.exec()
    finally:
        signal.set_wakeup_fd(previous_wakeup)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        os.close(read_fd)
        os.close(write_fd)
