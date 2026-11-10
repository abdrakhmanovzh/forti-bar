# Forti Bar

Fortinet SSL VPN controls inside a **native DMS top-bar popout**. Click the VPN
key icon beside the system tray. Profiles, connect/disconnect, credentials,
certificates and activity stay inside that menu. No standalone application window.

The widget uses DMS's own PluginComponent, PluginPopout, buttons, fields, toggles,
icons and theme. DMS handles its position, outside-click dismissal, Escape,
monitor placement, light/dark mode, fonts and animations.

The headless controller measured about 25 MiB
PSS and no measurable CPU use over the idle sample, excluding the VPN engine.

## Enable on your desktop

Development uses uv:

```sh
uv sync --locked
uv run --locked python -m fortibar --install-dms
```

This copies the widget into `~/.config/DankMaterialShell/plugins/FortiBar`, enables
it, and adds `fortiBar` immediately beside `systemTray` on your enabled bars. It
preserves the other widgets and settings, and creates `.before-forti-bar` backups
of the two JSON settings files it updates. The launcher detects your DMS CLI
syntax, reloads the widget and opens its menu. Click **+** to configure your VPN.

If DMS was started before its plugins directory existed, its folder watcher can
miss the new directory and return `PLUGIN_NOT_FOUND`. Refresh discovery once:

```sh
./forti-bar --restart-dms
```

This explicitly restarts the `dms.service` user service, waits for the new shell,
then verifies the plugin and opens its menu. Alternatively, click **Scan** in
DMS Settings → Plugins, then run the launcher again.

On a host with distro PySide6 already installed, offline development is possible:

```sh
uv venv --system-site-packages --python /usr/bin/python3
uv run --frozen --no-sync python -m fortibar --install-dms
```

Running `forti-bar` installs/enables the widget, verifies that DMS loaded it and
registered its icon, and opens its native menu. Activation errors are reported
with a nonzero exit status. It never opens an application window.
DMS starts and owns the headless controller through private pipes. One QML
singleton shares that controller across all bars and monitors. Disabling the widget
or stopping DMS shuts down the VPN cleanly.

## Install host networking support on Bazzite

The privileged helper and openfortivpn must run on the host:

```sh
python3 scripts/build-rpm.py
sudo rpm-ostree install ./dist/noarch/forti-bar-0.2.2-1.fc45.noarch.rpm
# Reboot into the new deployment, then, as your desktop user:
forti-bar
```

The RPM includes the controller, isolated helper, polkit policy and the DMS widget,
which it installs system-wide in `/etc/xdg/quickshell/dms-plugins/FortiBar`.
Running `forti-bar` as a user only enables that plugin and places its icon beside
the tray in that user's DMS settings; no files are copied into the home directory.
It also removes a per-user copy left by an earlier development install, since DMS
prefers user plugins over system ones. If DMS then reports `PLUGIN_NOT_FOUND`,
run `forti-bar --restart-dms` once.

Running from a source checkout (`./forti-bar` or `uv run python -m fortibar`)
instead copies the widget into `~/.config/DankMaterialShell/plugins/FortiBar`,
pointed at the checkout, which overrides the packaged widget for development. It uses shared distro Qt packages; uv manages development only.
The build script uses rpmbuild or Bazzite's installed RPM 6 build library and
keeps all build files inside this repository. On conventional distributions,
install openfortivpn, ppp, PySide6, libsecret, polkit and resolvconf, then run
`sudo ./scripts/install.sh` followed by `forti-bar` as your user.

## Connection settings

- Several saved profiles; one active VPN connection.
- Host, port (443 by default), username, password and optional realm.
- Optional desktop keyring storage via libsecret. No plaintext fallback.
- Normal system TLS verification, custom PEM CA bundles, and explicit SHA-256
  trusted certificate exceptions. Fingerprints match openfortivpn's exception
  semantics; they are not exclusive certificate pinning.
- Certificate review and password prompts remain inside the DMS popout.
- Optional bounded reconnection after network loss or resume. Authentication and
  certificate failures require attention; manual disconnection never reconnects.
- Up to 250 redacted activity events, bounded to 64 KiB, kept in memory.

Credentials never enter DMS plugin settings or command arguments. DMS exchanges
bounded JSON messages with the user controller through private parent/child pipes.
Only the root-owned helper is elevated through polkit. It validates a fixed
configuration schema and invokes an absolute root-owned openfortivpn executable
with no shell. Config/CA data use sealed anonymous memory files and the password
is delivered on stdin. User-supplied privileged scripts/plugins/paths are rejected.

openfortivpn owns server-supplied routes and uses resolvconf for scoped DNS with
`pppd-use-peerdns=0`. Connected requires both the engine's ready message and an
IPv4 address on the expected PPP interface. A closed control pipe disconnects,
with bounded signal escalation and child reaping.

Username/password SSL VPN is the current scope. OTP, SAML, IPsec and firewall kill
switches are not implemented. FortiOS 7.6.3 removed SSL VPN tunnel mode; migrated
IPsec gateways need another backend.

## Checks and preview

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/benchmark.py
uv run --locked python -m fortibar --demo --screenshot=/tmp/forti-bar-menu.png
uv run --locked python -m fortibar --demo --page=edit --screenshot=/tmp/forti-bar-editor.png
```

Preview captures render the actual installed DMS controls using fixture shell
services. They do not start another desktop shell or create an application window
on your desktop. Actual placement and dismissal are provided by DMS's PluginPopout.

This sandbox cannot access the real DMS session or install host packages. Live
panel placement, keyring storage and real gateway routing/DNS need a desktop run.
The lockfile's Qt wheels were bootstrapped from PyPI metadata and pass
`uv lock --check --offline`; dependency downloads were unavailable here, so local
checks use the distribution's Qt runtime through uv's explicit offline mode.
