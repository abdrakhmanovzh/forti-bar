"""Privileged process supervisor. No Qt imports and no user-controlled commands."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import time

from .model import MAX_REQUEST_BYTES, Profile, backend_config, validate_password

BACKEND_PATHS = ("/usr/bin/openfortivpn", "/usr/sbin/openfortivpn")
RESOLVCONF_PATHS = ("/usr/bin/resolvconf", "/usr/sbin/resolvconf", "/sbin/resolvconf")
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}


def root_owned_executable(path: str) -> bool:
    try:
        p = Path(path).resolve(strict=True)
        for parent in (p, *p.parents):
            info = parent.stat()
            if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return False
        return p.is_file() and os.access(p, os.X_OK)
    except OSError:
        return False


def redact(line: str, password: str = "") -> str:
    if password:
        line = line.replace(password, "[redacted]")
    line = re.sub(r"(?i)(SVPNCOOKIE\s*[=:]\s*)[^\s;]+", r"\1[redacted]", line)
    line = re.sub(r"(?i)((?:password|passwd|cookie|token|authorization)\s*[=:]\s*)\S+", r"\1[redacted]", line)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)[:4096]


def classify_failure(lines: list[str]) -> str:
    text = "\n".join(lines).lower()
    if "trusted-cert" in text or "certificate validation failed" in text:
        return "certificate"
    if any(x in text for x in ("could not authenticate", "authentication failed", "permission denied", "invalid password", "login failed")):
        return "authentication"
    if any(x in text for x in ("resolve", "connect to gateway", "connection refused", "timed out", "network is unreachable", "could not connect")):
        return "network"
    if any(x in text for x in ("otp", "two-factor", "two factor", "saml")):
        return "unsupported-auth"
    return "tunnel"


def certificate_info(lines: list[str]) -> dict:
    text = "\n".join(lines)
    match = re.search(r"(?i)trusted-cert\s*=\s*([0-9a-f]{64})", text)
    result = {"fingerprint": match.group(1).lower() if match else ""}
    for key in ("subject", "issuer"):
        match = re.search(rf"(?im){key}\s*:\s*(.+)", text)
        result[key] = match.group(1).strip() if match else ""
    return result


def private_memfd(name: str, content: bytes) -> int:
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.write(fd, content)
        os.lseek(fd, 0, os.SEEK_SET)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        return fd
    except BaseException:
        os.close(fd)
        raise


def interface_address(name: str) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            result = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", name.encode()))
            return socket.inet_ntoa(result[20:24])
    except OSError:
        return ""


def read_request(fd: int = 0) -> tuple[dict, bytes]:
    data = bytearray()
    while b"\n" not in data:
        chunk = os.read(fd, 16384)
        if not chunk:
            raise ValueError("No connection request received.")
        data.extend(chunk)
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError("Connection request is too large.")
    line, rest = bytes(data).split(b"\n", 1)
    request = json.loads(line)
    if not isinstance(request, dict) or set(request) != {"profile", "password"}:
        raise ValueError("Unsupported connection request.")
    return request, rest


class Supervisor:
    def __init__(self, profile: Profile, password: str, *, backend: str, emit=None,
                 address_reader=interface_address):
        self.profile = profile
        self.password = password
        self.backend = backend
        self.emit = emit or self.write_event
        self.address_reader = address_reader
        self.process: subprocess.Popen | None = None
        self.stopping = False
        self.stop_deadline = 0.0
        self.stop_stage = 0
        self.history: list[str] = []
        self.ready_pending = False
        self.connected = False
        self.previous_handlers: dict = {}
        self.wakeup_fds = ()
        self.previous_wakeup = -1

    @staticmethod
    def write_event(event: dict) -> None:
        try:
            sys.stdout.write(json.dumps(event, ensure_ascii=True) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            raise ConnectionError("The controlling application closed.") from None

    def stop(self, *_args) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.stop_deadline = time.monotonic() + 8
        self.stop_stage = 0
        self.signal_child(signal.SIGINT)

    def signal_child(self, sig: int) -> None:
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                pass

    def line(self, raw: bytes) -> None:
        line = redact(raw.decode("utf-8", "replace").strip(), self.password)
        if not line:
            return
        self.history = (self.history + [line])[-80:]
        self.emit({"type": "log", "message": line})
        if "tunnel is up and running" in line.lower():
            self.ready_pending = True

    def check_ready(self) -> None:
        if not self.ready_pending or self.connected or self.stopping:
            return
        address = self.address_reader(self.profile.interface)
        if address and address != "0.0.0.0":
            self.connected = True
            self.ready_pending = False
            self.emit({"type": "state", "state": "connected",
                       "interface": self.profile.interface, "local_ip": address})

    def run(self, control_fd: int = 0, pending: bytes = b"") -> int:
        fds = []
        selector = selectors.DefaultSelector()
        output = bytearray()
        controls = bytearray(pending)
        try:
            ca_path = ""
            if self.profile.ca_pem:
                ca_fd = private_memfd("forti-bar-ca", self.profile.ca_pem.encode())
                fds.append(ca_fd)
                ca_path = f"/proc/self/fd/{ca_fd}"
            config_fd = private_memfd("forti-bar-config", backend_config(self.profile, ca_path))
            fds.append(config_fd)
            args = [self.backend, "-c", f"/proc/self/fd/{config_fd}",
                    "--username=" + self.profile.username,
                    "--pppd-ifname=" + self.profile.interface]
            if self.profile.realm:
                args.append("--realm=" + self.profile.realm)
            args.append(self.profile.endpoint)
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, pass_fds=fds,
                                            env=SAFE_ENV, start_new_session=True)
            assert self.process.stdin and self.process.stdout
            self.process.stdin.write((self.password + "\n").encode())
            self.process.stdin.close()
            selector.register(self.process.stdout, selectors.EVENT_READ, "output")
            selector.register(control_fd, selectors.EVENT_READ, "control")
            self.wakeup_fds = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            selector.register(self.wakeup_fds[0], selectors.EVENT_READ, "signal")
            self.previous_wakeup = signal.set_wakeup_fd(self.wakeup_fds[1])
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                self.previous_handlers[sig] = signal.signal(sig, self.stop)
            self.previous_handlers[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, lambda *_args: None)
            self.emit({"type": "state", "state": "connecting"})
            deadline = time.monotonic() + 60
            timed_out = False
            while self.process.poll() is None or selector.get_map().get(self.process.stdout.fileno()):
                if b"\n" in controls:
                    command, _, tail = controls.partition(b"\n")
                    controls = bytearray(tail)
                    try:
                        if json.loads(command) == {"action": "disconnect"}:
                            self.stop()
                    except (ValueError, UnicodeError):
                        self.stop()
                now = time.monotonic()
                if not self.connected and not self.stopping and now > deadline:
                    timed_out = True
                    self.stop()
                if self.stopping and now >= self.stop_deadline:
                    self.stop_stage += 1
                    self.signal_child(signal.SIGTERM if self.stop_stage == 1 else signal.SIGKILL)
                    self.stop_deadline = now + 2
                self.check_ready()
                timeout = 0.15 if self.ready_pending or self.stopping else (None if self.connected else 1.0)
                for key, _mask in selector.select(timeout):
                    if key.data == "signal":
                        try:
                            os.read(key.fd, 4096)
                        except BlockingIOError:
                            pass
                    elif key.data == "control":
                        chunk = os.read(control_fd, 16384)
                        if not chunk:
                            selector.unregister(control_fd)
                            self.stop()
                        else:
                            controls.extend(chunk)
                            if len(controls) > 4096:
                                self.stop()
                    else:
                        chunk = os.read(key.fd, 16384)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            while b"\n" in output:
                                line, _, tail = output.partition(b"\n")
                                output = bytearray(tail)
                                self.line(line)
                            if len(output) > 32768:
                                self.line(bytes(output[:4096]))
                                output.clear()
            if output:
                self.line(bytes(output))
            code = self.process.wait()
            if not self.stopping:
                category = classify_failure(self.history)
                self.emit({"type": "error", "category": category,
                           "certificate": certificate_info(self.history) if category == "certificate" else {}})
            elif timed_out:
                self.emit({"type": "error", "category": "timeout"})
            self.emit({"type": "state", "state": "disconnected"})
            return 0 if self.stopping and not timed_out else (code or 1)
        finally:
            self.signal_child(signal.SIGTERM)
            if self.process:
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.signal_child(signal.SIGKILL)
                    self.process.wait()
                if self.process.stdout:
                    self.process.stdout.close()
            for sig, handler in self.previous_handlers.items():
                signal.signal(sig, handler)
            if self.wakeup_fds:
                signal.set_wakeup_fd(self.previous_wakeup)
                for fd in self.wakeup_fds:
                    os.close(fd)
            selector.close()
            for fd in fds:
                os.close(fd)
            self.password = ""


def acquire_lock() -> int:
    path = Path("/run/forti-bar")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Unsafe VPN runtime directory.")
    fd = os.open(path / "connection.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Unsafe VPN connection lock.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


def main() -> int:
    lock = None
    try:
        if os.geteuid() != 0:
            raise ValueError("The VPN helper must be started through polkit.")
        backend = next((p for p in BACKEND_PATHS if root_owned_executable(p)), "")
        if not backend:
            raise ValueError("Install the openfortivpn package on the host first.")
        if not any(root_owned_executable(p) for p in RESOLVCONF_PATHS):
            raise ValueError("Install resolvconf or systemd-resolved's resolvconf compatibility tool first.")
        request, pending = read_request()
        profile = Profile.parse(request["profile"])
        password = validate_password(request["password"])
        lock = acquire_lock()
        return Supervisor(profile, password, backend=backend).run(pending=pending)
    except BlockingIOError:
        Supervisor.write_event({"type": "error", "category": "busy", "message": "Another Forti Bar connection is already running."})
    except (ValueError, OSError) as exc:
        Supervisor.write_event({"type": "error", "category": "setup", "message": str(exc)})
    except (BrokenPipeError, ConnectionError):
        pass
    finally:
        if lock is not None:
            os.close(lock)
    return 1
