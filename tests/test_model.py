import json
import os
from pathlib import Path
import tempfile
import unittest

from fortibar.model import Profile, ProfileStore, backend_config, import_config, normalize_fingerprints, normalize_host, validate_password


def profile(**overrides):
    raw = {"name": "Work", "host": "vpn.example.com", "port": 443, "username": "jane"}
    raw.update(overrides)
    return Profile.parse(raw)


class ValidationTests(unittest.TestCase):
    def test_normalizes_dns_idna_and_ip(self):
        self.assertEqual(normalize_host(" VPN.Example.Com. "), "vpn.example.com")
        self.assertEqual(normalize_host("münchen.example"), "xn--mnchen-3ya.example")
        self.assertEqual(normalize_host("[2001:db8::1]"), "2001:db8::1")

    def test_rejects_urls_ports_and_command_injection(self):
        for value in ("https://vpn.example.com", "vpn.example.com:443", "vpn/x", "-vpn", "a..b", "foo\npppd-plugin=/tmp/x", "$(id)", "foo bar"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_host(value)

    def test_validates_port_types_and_range(self):
        for value in (0, 65536, -1, True, 3.5, "abc", "443\nfoo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                profile(port=value)
        self.assertEqual(profile(port="8443").port, 8443)

    def test_rejects_arbitrary_backend_fields(self):
        for key in ("pinentry", "pppd-plugin", "ca-file", "password", "pppd-log", "persistent"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                profile(**{key: "/tmp/evil"})

    def test_rejects_embedded_config_lines(self):
        for key in ("name", "username", "realm"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                profile(**{key: "safe\npppd-call=evil"})

    def test_credentials_can_contain_spaces_hashes_and_equals(self):
        p = profile(username="a user#=x", realm="a realm#=x")
        config = backend_config(p).decode()
        self.assertNotIn(p.username, config)
        self.assertNotIn(p.realm, config)
        self.assertEqual(validate_password(" # $(id)='secret' "), " # $(id)='secret' ")

    def test_fingerprints_normalize_and_deduplicate(self):
        digest = "AB" * 32
        self.assertEqual(normalize_fingerprints([":".join(["AB"] * 32), digest, ""]), [digest.lower()])
        with self.assertRaises(ValueError):
            normalize_fingerprints(["ab" * 31])

    def test_password_line_injection_is_rejected(self):
        for value in ("", "secret\nnext", "secret\r", "a\0b", "x" * 4097):
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                validate_password(value)

    def test_ca_content_must_be_certificate_data(self):
        for value in ("/etc/shadow", "not a certificate", "x" * (256 * 1024 + 1)):
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                profile(ca_pem=value)

    def test_backend_config_has_no_executable_or_secret_values(self):
        p = profile(trusted_certs=["ab" * 32])
        config = backend_config(p, "/proc/self/fd/4").decode()
        self.assertIn("trusted-cert = " + "ab" * 32, config)
        self.assertIn("pppd-use-peerdns = 0", config)
        self.assertIn("use-resolvconf = 1", config)
        self.assertNotIn("password", config)
        self.assertNotIn("username", config)
        self.assertLessEqual(len(p.interface), 15)

    def test_import_never_preserves_executable_options_or_password(self):
        result = import_config("host = vpn.example.com\nport = 8443\nusername = jane\npassword = secret\npppd-plugin = /tmp/evil\npinentry = /tmp/evil\ntrusted-cert = " + "ab" * 32)
        self.assertEqual(result["profile"]["port"], 8443)
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["ignored"], ["pinentry", "pppd-plugin"])
        self.assertEqual(result["profile"]["trusted_certs"], ["ab" * 32])

    def test_import_cannot_smuggle_an_invalid_host(self):
        with self.assertRaises(ValueError):
            import_config("host = --pppd-plugin=/tmp/evil\nusername = jane")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProfileStore(Path(self.temp.name) / "config")

    def test_round_trip_and_permissions(self):
        p = profile(remember_password=True)
        self.store.profiles = [p]
        self.store.selected = p.id
        self.store.save()
        other = ProfileStore(self.store.directory)
        other.load()
        self.assertEqual(other.profiles, [p])
        self.assertEqual(other.selected, p.id)
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.store.directory).st_mode & 0o777, 0o700)
        self.assertNotIn('"password"', self.store.path.read_text())

    def test_invalid_file_remains_untouched(self):
        self.store.directory.mkdir()
        self.store.path.write_text("broken JSON")
        with self.assertRaises(ValueError):
            self.store.load()
        self.assertEqual(self.store.path.read_text(), "broken JSON")

    def test_duplicate_profile_ids_are_rejected(self):
        p = profile().to_dict()
        self.store.directory.mkdir()
        self.store.path.write_text(json.dumps({"version": 1, "selected": p["id"], "profiles": [p, p]}))
        with self.assertRaises(ValueError):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
