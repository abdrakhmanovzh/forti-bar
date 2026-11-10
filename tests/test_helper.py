import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

from fortibar.helper import Supervisor, certificate_info, classify_failure, private_memfd, read_request, redact
from tests.test_model import profile

FAKE_ENGINE = '''#!/usr/bin/python3
import json, pathlib, signal, sys, time
base = pathlib.Path(__file__).parent
mode = (base / "mode").read_text()
password = sys.stdin.readline().rstrip("\\n")
config_path = sys.argv[sys.argv.index("-c") + 1]
(base / "audit.json").write_text(json.dumps({"argv":sys.argv, "password":password, "config":pathlib.Path(config_path).read_text()}))
if mode == "auth":
    print("ERROR: Could not authenticate to gateway.", flush=True)
    sys.exit(1)
if mode == "cert":
    print("ERROR: Server certificate validation failed", flush=True)
    print("ERROR: --trusted-cert=" + "ab"*32, flush=True)
    print("ERROR: subject: CN=work.example.com", flush=True)
    sys.exit(1)
print("INFO: Authenticated.", flush=True)
print("INFO: Tunnel is up and running.", flush=True)
if mode == "wait":
    try:
        time.sleep(30)
    except KeyboardInterrupt:
        print("INFO: Tunnel is down.", flush=True)
else:
    time.sleep(.2)
'''


class HelperUnitTests(unittest.TestCase):
    def test_secret_redaction(self):
        line = "password=abc SVPNCOOKIE=foo; token=bar actual secret"
        safe = redact(line, "actual secret")
        for secret in ("abc", "foo", "bar", "actual secret"):
            self.assertNotIn(secret, safe)
        self.assertNotIn("\x1b", redact("\x1b[31mred\x1b[0m"))

    def test_successful_auth_does_not_mask_a_later_network_error(self):
        self.assertEqual(classify_failure(["Authenticated.", "Could not connect to gateway"]), "network")
        self.assertEqual(classify_failure(["Could not authenticate"]), "authentication")

    def test_certificate_extraction(self):
        info = certificate_info(["--trusted-cert=" + "ab" * 32, "subject: CN=vpn", "issuer: CN=CA"])
        self.assertEqual(info, {"fingerprint": "ab" * 32, "subject": "CN=vpn", "issuer": "CN=CA"})

    def test_memory_config_is_sealed(self):
        fd = private_memfd("forti-bar-test", b"public config\n")
        try:
            self.assertEqual(os.read(fd, 1024), b"public config\n")
            with self.assertRaises(OSError):
                os.write(fd, b"changed")
            self.assertTrue(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE)
        finally:
            os.close(fd)

    def test_request_schema_rejects_user_command(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'{"profile":{},"password":"x","command":"id"}\n')
            with self.assertRaises(ValueError):
                read_request(read_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.backend = self.directory / "fake-vpn"
        self.backend.write_text(FAKE_ENGINE)
        self.backend.chmod(0o755)

    def run_engine(self, mode, *, address="10.23.0.4", pending=b""):
        (self.directory / "mode").write_text(mode)
        events = []
        p = profile(username="a user #=x", realm="work #=realm")
        supervisor = Supervisor(p, " secret #=$() ", backend=str(self.backend), emit=events.append,
                                address_reader=lambda _name: address)
        read_fd, write_fd = os.pipe()
        try:
            result = supervisor.run(control_fd=read_fd, pending=pending)
        finally:
            os.close(read_fd)
            os.close(write_fd)
        return p, result, events

    def test_password_is_only_on_stdin_and_credentials_are_single_arguments(self):
        p, _result, events = self.run_engine("ready")
        audit = json.loads((self.directory / "audit.json").read_text())
        self.assertEqual(audit["password"], " secret #=$() ")
        self.assertNotIn(" secret #=$() ", " ".join(audit["argv"]))
        self.assertNotIn("password", audit["config"])
        self.assertIn("--username=" + p.username, audit["argv"])
        self.assertIn("--realm=" + p.realm, audit["argv"])
        self.assertTrue(any(e.get("state") == "connected" for e in events))

    def test_log_alone_cannot_report_connected(self):
        _p, _result, events = self.run_engine("ready", address="")
        self.assertFalse(any(e.get("state") == "connected" for e in events))

    def test_authentication_failure_is_structured(self):
        _p, result, events = self.run_engine("auth")
        self.assertNotEqual(result, 0)
        self.assertIn({"type": "error", "category": "authentication", "certificate": {}}, events)

    def test_certificate_failure_can_be_reviewed(self):
        _p, _result, events = self.run_engine("cert")
        error = next(e for e in events if e.get("type") == "error")
        self.assertEqual(error["category"], "certificate")
        self.assertEqual(error["certificate"]["fingerprint"], "ab" * 32)

    def test_disconnect_reaps_backend(self):
        (self.directory / "mode").write_text("wait")
        p = profile()
        events = []
        s = Supervisor(p, "secret", backend=str(self.backend), emit=events.append,
                       address_reader=lambda _name: "10.23.0.4")
        read_fd, write_fd = os.pipe()
        # A controller subprocess sends a disconnect after the child is ready.
        sender = subprocess.Popen([sys.executable, "-c", "import os,time; time.sleep(.2); os.write(int(__import__('sys').argv[1]), b'{\"action\":\"disconnect\"}\\n')", str(write_fd)], pass_fds=(write_fd,))
        try:
            self.assertEqual(s.run(control_fd=read_fd), 0)
            self.assertIsNotNone(s.process.returncode)
            self.assertEqual(events[-1], {"type": "state", "state": "disconnected"})
        finally:
            os.close(read_fd)
            os.close(write_fd)
            sender.wait(timeout=2)

    def test_control_pipe_loss_stops_tunnel(self):
        (self.directory / "mode").write_text("wait")
        s = Supervisor(profile(), "secret", backend=str(self.backend), emit=lambda _e: None)
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            self.assertEqual(s.run(control_fd=read_fd), 0)
            self.assertIsNotNone(s.process.returncode)
        finally:
            os.close(read_fd)


if __name__ == "__main__":
    unittest.main()
