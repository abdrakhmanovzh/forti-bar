"""Validated, non-executable connection settings, shared with the system helper."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

MAX_CA_BYTES = 256 * 1024
MAX_PASSWORD_BYTES = 4096
MAX_REQUEST_BYTES = 320 * 1024
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def clean_text(value: object, label: str, limit: int = 255, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if (not empty and not value) or len(value.encode()) > limit:
        raise ValueError(f"Enter a valid {label.lower()} (up to {limit} bytes).")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label} cannot contain control characters.")
    return value


def normalize_host(value: object) -> str:
    host = clean_text(value, "Host", 253)
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError("Enter a hostname or IP address, without a URL or port.") from exc
    if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")):
        raise ValueError("Enter a hostname or IP address, without https://, a path, or a port.")
    return host


def normalize_fingerprints(value: object) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;\n]+", value)
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("Use up to 16 SHA-256 certificate fingerprints.")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Certificate fingerprints must be text.")
        item = item.strip().lower().replace(":", "").replace(" ", "")
        if not item:
            continue
        if not FINGERPRINT.fullmatch(item):
            raise ValueError("A SHA-256 fingerprint must contain exactly 64 hexadecimal characters.")
        if item not in result:
            result.append(item)
    return result


def validate_password(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError("Enter a password (up to 4096 bytes).")
    if any(c in value for c in ("\r", "\n", "\0")):
        raise ValueError("The VPN password cannot contain a line break or NUL.")
    return value


@dataclass
class Profile:
    id: str
    name: str
    host: str
    port: int
    username: str
    realm: str = ""
    trusted_certs: list[str] = field(default_factory=list)
    ca_pem: str = ""
    ca_name: str = ""
    remember_password: bool = False

    @classmethod
    def parse(cls, raw: dict) -> "Profile":
        if not isinstance(raw, dict):
            raise ValueError("Invalid connection profile.")
        allowed = set(cls.__dataclass_fields__)
        if set(raw) - allowed:
            raise ValueError("This profile contains unsupported options.")
        try:
            profile_id = str(uuid.UUID(str(raw.get("id") or uuid.uuid4())))
        except ValueError as exc:
            raise ValueError("Invalid profile ID.") from exc
        port = raw.get("port", 443)
        if isinstance(port, bool) or not isinstance(port, (str, int)):
            raise ValueError("Port must be a number from 1 to 65535.")
        try:
            port = int(port)
        except (ValueError, TypeError) as exc:
            raise ValueError("Port must be a number from 1 to 65535.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Port must be a number from 1 to 65535.")
        ca = raw.get("ca_pem", "")
        if not isinstance(ca, str) or len(ca.encode()) > MAX_CA_BYTES or "\0" in ca:
            raise ValueError("The CA certificate is too large or invalid.")
        if ca:
            import ssl
            try:
                ssl.create_default_context().load_verify_locations(cadata=ca)
            except (ssl.SSLError, ValueError) as exc:
                raise ValueError("Choose a valid PEM CA certificate or bundle.") from exc
        remember = raw.get("remember_password", False)
        if not isinstance(remember, bool):
            raise ValueError("Invalid password storage option.")
        return cls(
            id=profile_id,
            name=clean_text(raw.get("name", ""), "Profile name", 80),
            host=normalize_host(raw.get("host", "")),
            port=port,
            username=clean_text(raw.get("username", ""), "Username", 255),
            realm=clean_text(raw.get("realm", ""), "Realm", 255, empty=True),
            trusted_certs=normalize_fingerprints(raw.get("trusted_certs", [])),
            ca_pem=ca,
            ca_name=clean_text(raw.get("ca_name", ""), "Certificate filename", 255, empty=True),
            remember_password=remember,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def endpoint(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    @property
    def interface(self) -> str:
        return "fb" + self.id.replace("-", "")[:10]


def backend_config(profile: Profile, ca_path: str = "") -> bytes:
    """Only fixed, validated keys ever reach the privileged executable."""
    # Fields with whitespace, '#' or '=' are valid credentials/realms. They are
    # passed as individual exec arguments, never interpolated into this file.
    lines = ["set-routes = 1", "half-internet-routes = 1", "set-dns = 1",
             "use-resolvconf = 1", "pppd-use-peerdns = 0", "persistent = 0"]
    lines.extend("trusted-cert = " + digest for digest in profile.trusted_certs)
    if ca_path:
        lines.append("ca-file = " + ca_path)
    return ("\n".join(lines) + "\n").encode()


def import_config(text: str) -> dict:
    """Import useful settings; executable paths and pppd overrides are rejected."""
    mapping = {"host": "host", "port": "port", "username": "username", "realm": "realm"}
    raw: dict = {"name": "Imported VPN", "trusted_certs": []}
    ignored = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"Invalid config on line {number}.")
        key, value = key.strip(), value.strip()
        if key in mapping:
            raw[mapping[key]] = value
        elif key == "trusted-cert":
            raw["trusted_certs"].append(value)
        elif key == "password":
            # Import is deliberately non-secret. The user enters it in the form.
            continue
        else:
            ignored.append(key)
    profile = Profile.parse(raw)
    return {"profile": profile.to_dict(), "ignored": sorted(set(ignored))}


class ProfileStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "profiles.json"
        self.profiles: list[Profile] = []
        self.selected = ""

    def load(self) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("The profiles file is too large.")
        data = json.loads(self.path.read_text())
        if data.get("version") != 1:
            raise ValueError("Unsupported profiles format.")
        profiles = [Profile.parse(x) for x in data.get("profiles", [])]
        if len(profiles) > 100 or len({p.id for p in profiles}) != len(profiles):
            raise ValueError("Invalid or duplicate profiles.")
        self.profiles = profiles
        self.selected = data.get("selected", "")
        if self.selected not in {p.id for p in profiles}:
            self.selected = profiles[0].id if profiles else ""

    def save(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = {"version": 1, "selected": self.selected,
                "profiles": [p.to_dict() for p in self.profiles]}
        if len(json.dumps(data).encode()) > 4 * 1024 * 1024:
            raise ValueError("Saved profiles must fit within 4 MiB. Remove unused CA bundles or profiles.")
        fd, path = tempfile.mkstemp(prefix=".profiles-", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.path)
        finally:
            if os.path.exists(path):
                os.unlink(path)
