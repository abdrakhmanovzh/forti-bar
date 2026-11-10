"""Enable the native widget beside DMS's system tray, preserving existing settings."""

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import subprocess
import time
import re


class ActivationError(RuntimeError):
    """The files exist, but the running desktop did not activate the widget."""


def _run_dms(arguments):
    try:
        result = subprocess.run(["dms", *arguments], capture_output=True, text=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActivationError(f"Could not reach DMS: {exc}") from exc
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", (result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode, output


def activate_widget(*, restart_dms=False):
    """Check the installed CLI syntax and verify discovery, loading and display."""
    if not shutil.which("dms"):
        raise ActivationError("DMS is not installed or its command is not on PATH.")
    _code, help_text = _run_dms(["ipc", "--help"])
    if "dms ipc <target> <function>" in help_text:
        prefix = ["ipc"]
    elif "dms ipc call" in help_text or re.search(r"(?m)^\s+call\s+", help_text):
        prefix = ["ipc", "call"]
    else:
        raise ActivationError("Could not determine this DMS version's IPC syntax. " + help_text[-1200:])

    if restart_dms:
        print("Restarting the DMS desktop service to refresh plugin discovery...", flush=True)
        try:
            restarted = subprocess.run(["systemctl", "--user", "restart", "dms.service"],
                                       capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ActivationError(f"Could not restart DMS: {exc}") from exc
        if restarted.returncode:
            raise ActivationError("Could not restart DMS: " + (restarted.stderr or restarted.stdout).strip())
        deadline = time.monotonic() + 12
        while True:
            code, output = _run_dms([*prefix, "plugins", "list"])
            if code == 0:
                break
            if time.monotonic() >= deadline:
                raise ActivationError("DMS did not become ready after restarting: " + output)
            time.sleep(0.2)

    def ipc(target, function, *args):
        code, output = _run_dms([*prefix, target, function, *args])
        if code != 0:
            raise ActivationError(output or f"DMS {target} {function} failed.")
        return output

    # Referencing PluginService initializes discovery in DMS versions that load
    # the service lazily. Allow its directory watcher to finish before reload.
    ipc("plugins", "list")
    for attempt in range(8):
        result = ipc("plugins", "reload", "fortiBar")
        if "PLUGIN_RELOAD_SUCCESS: fortiBar" in result:
            break
        if "PLUGIN_NOT_FOUND" in result and attempt == 7:
            raise ActivationError(
                "DMS has not discovered the installed widget. Its folder watcher can miss a plugins "
                "directory created after startup. Run this launcher with --restart-dms to restart "
                "the DMS desktop service and refresh discovery."
            )
        if "PLUGIN_NOT_FOUND" not in result:
            raise ActivationError("DMS did not load Forti Bar: " + result)
        time.sleep(0.2)

    status = ipc("plugins", "status", "fortiBar")
    if status.strip() != "loaded":
        raise ActivationError("DMS did not confirm that Forti Bar is loaded: " + status)
    for attempt in range(8):
        visibility = ipc("widget", "visibility", "fortiBar")
        if visibility.strip() in ("visible", "hidden"):
            break
        if "WIDGET_NOT_FOUND" not in visibility or attempt == 7:
            raise ActivationError("The plugin loaded, but DMS did not register its top-bar icon: " + visibility)
        time.sleep(0.2)
    if visibility.strip() == "hidden":
        revealed = ipc("widget", "reveal", "fortiBar")
        if "WIDGET_REVEAL_SUCCESS" not in revealed:
            raise ActivationError("DMS could not show the VPN icon: " + revealed)
    opened = ipc("widget", "openWith", "fortiBar", "all")
    if "WIDGET_OPEN_WITH_SUCCESS: fortiBar" not in opened:
        raise ActivationError("DMS could not open the VPN menu: " + opened)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".forti-bar-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Invalid DMS configuration: {path}")
    return data


SYSTEM_PLUGIN = Path("/etc/xdg/quickshell/dms-plugins/FortiBar")
REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_our_plugin(directory):
    try:
        return json.loads((directory / "plugin.json").read_text()).get("id") == "fortiBar"
    except (OSError, ValueError, AttributeError):
        return False


def install(*, config_dir=None, source=None, command=None, activate=None, restart_dms=False, system_plugin=None):
    """Enable the widget for this user.

    Packaged installs ship the widget in DMS's system plugin directory, so only
    this user's DMS settings change. Running from a source checkout copies the
    widget into the user's plugin directory, which DMS prefers over the system one.
    """
    config = Path(config_dir or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "DankMaterialShell")
    system_plugin = Path(system_plugin or SYSTEM_PLUGIN)
    development = source is not None or (REPO_ROOT / "forti-bar").exists()
    if development:
        source = Path(source or REPO_ROOT / "dms/FortiBar")
        if not (source / "plugin.json").is_file():
            raise ValueError("The Forti Bar DMS widget files are missing.")
    elif not (system_plugin / "plugin.json").is_file():
        raise ValueError(f"The Forti Bar DMS widget is missing from {system_plugin}. Reinstall the forti-bar package.")
    settings_path, plugins_path = config / "settings.json", config / "plugin_settings.json"
    # Parse both before any changes. A malformed file is never replaced.
    settings, plugins = load_json(settings_path), load_json(plugins_path)
    bars = settings.get("barConfigs", [])
    if not isinstance(bars, list) or not bars or not all(isinstance(bar, dict) for bar in bars):
        raise ValueError("No DMS bar configuration was found. Start DMS first.")
    if "fortiBar" in plugins and not isinstance(plugins["fortiBar"], dict):
        raise ValueError("Invalid existing Forti Bar plugin settings.")
    for bar in bars:
        for section in ("leftWidgets", "centerWidgets", "rightWidgets"):
            if section in bar and not isinstance(bar[section], list):
                raise ValueError("Invalid DMS widget list. Existing settings were left unchanged.")
    user_plugin = config / "plugins/FortiBar"
    if development:
        if command is None:
            command = [sys.executable, str(REPO_ROOT / "forti-bar"), "--dms-bridge"]
        user_plugin.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            if item.is_file():
                shutil.copy2(item, user_plugin / item.name)
        atomic_json(user_plugin / "backend.json", command)
        destination = user_plugin
    else:
        # A copy left by an earlier per-user install would shadow the packaged widget.
        if user_plugin.is_dir() and _is_our_plugin(user_plugin):
            shutil.rmtree(user_plugin)
        destination = system_plugin
    plugins.setdefault("fortiBar", {})["enabled"] = True
    for bar in bars:
        if not bar.get("enabled", True):
            continue
        if any("fortiBar" in bar.get(section, []) for section in ("leftWidgets", "centerWidgets", "rightWidgets")):
            continue
        widgets = bar.setdefault("rightWidgets", [])
        index = widgets.index("systemTray") + 1 if "systemTray" in widgets else 0
        widgets.insert(index, "fortiBar")
    for path in (settings_path, plugins_path):
        if path.exists():
            backup = path.with_suffix(path.suffix + ".before-forti-bar")
            if not backup.exists():
                shutil.copy2(path, backup)
    atomic_json(plugins_path, plugins)
    atomic_json(settings_path, settings)
    if activate is None:
        activate = config_dir is None
    if activate:
        activate_widget(restart_dms=restart_dms)
        print("Forti Bar menu opened beside the DMS tray.")
    else:
        print(f"Forti Bar enabled from {destination}.")
    return destination
