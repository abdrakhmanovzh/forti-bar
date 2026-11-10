"""libsecret work stays off the GUI thread; no plaintext fallback exists."""

import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class SecretWorker(QRunnable):
    def __init__(self, owner, token, action, profile_id, value):
        super().__init__()
        self.owner, self.token, self.action = owner, token, action
        self.profile_id, self.value = profile_id, value

    @Slot()
    def run(self):
        result, error = "", ""
        try:
            from .native_keyring import NativeKeyring
            keyring = NativeKeyring()
            # A finite timeout also bounds requests when the keyring is locked
            # and the desktop's unlock prompt cannot be displayed.
            with self.owner.lock:
                self.owner.cancellables[self.token] = keyring
                if self.owner.closing:
                    keyring.cancel()
            timer = threading.Timer(45, keyring.cancel)
            timer.daemon = True
            timer.start()
            try:
                result = keyring.execute(self.action, self.profile_id, self.value)
            finally:
                timer.cancel()
                timer.join()
                with self.owner.lock:
                    self.owner.cancellables.pop(self.token, None)
                    keyring.close()
        except Exception:
            # Backend exception messages can contain implementation data. The
            # UI needs only this actionable, secret-free explanation.
            error = "The desktop keyring is unavailable or locked. Your password can be used for this session."
        finally:
            self.value = ""
            with self.owner.lock:
                self.owner.cancellables.pop(self.token, None)
        self.owner.workerCompleted.emit(self.token, self.action, self.profile_id, result, error)


class SecretStore(QObject):
    completed = Signal(str, str, str, str)
    workerCompleted = Signal(str, str, str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queues = {}
        self.active = set()
        self.lock = threading.Lock()
        self.cancellables = {}
        self.closing = False
        self.workerCompleted.connect(self.worker_finished)

    def request(self, token, action, profile_id, value=""):
        if self.closing:
            return
        self.queues.setdefault(profile_id, []).append((token, action, value))
        self.start_next(profile_id)

    def start_next(self, profile_id):
        if self.closing or profile_id in self.active or not self.queues.get(profile_id):
            return
        token, action, value = self.queues[profile_id].pop(0)
        self.active.add(profile_id)
        QThreadPool.globalInstance().start(SecretWorker(self, token, action, profile_id, value))

    @Slot(str, str, str, str, str)
    def worker_finished(self, token, action, profile_id, value, error):
        self.active.discard(profile_id)
        self.completed.emit(token, action, value, error)
        self.start_next(profile_id)

    def cancel_all(self):
        with self.lock:
            self.closing = True
            for cancel in self.cancellables.values():
                cancel.cancel()
        self.queues.clear()
