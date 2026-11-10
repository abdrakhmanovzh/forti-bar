"""Read DMS's public theme outputs, without changing shell configuration."""

import json
import os
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Property, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

DEFAULTS = {
    "surface": "#141218", "surface_container": "#1d1b20",
    "surface_container_high": "#211f24", "surface_container_highest": "#2b292f",
    "on_surface": "#e6e0e9", "on_surface_variant": "#cac4cf",
    "primary": "#d0bcff", "on_primary": "#381e72", "primary_container": "#4f378b",
    "on_primary_container": "#eaddff", "outline": "#948f99", "outline_variant": "#49454e",
    "error": "#f2b8b5", "error_container": "#8c1d18", "on_error_container": "#f9dedc",
}


class Appearance(QObject):
    changed = Signal()

    def __init__(self, app, *, home: Path | None = None):
        super().__init__()
        self.app = app
        home = home or Path.home()
        config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        cache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
        state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
        self.paths = [cache / "DankMaterialShell/dms-colors.json",
                      config / "DankMaterialShell/settings.json",
                      state / "DankMaterialShell/session.json"]
        self._colors = dict(DEFAULTS)
        self._font = "Inter Variable"
        self._radius = 12
        self._scale = 1.0
        self._synced = False
        self._light = False
        self.watcher = QFileSystemWatcher(self)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(120)
        self.debounce.timeout.connect(self.reload)
        self.watcher.fileChanged.connect(lambda _p: self.debounce.start())
        self.watcher.directoryChanged.connect(lambda _p: self.debounce.start())
        font_path = Path("/usr/share/quickshell/dms/assets/fonts/inter/InterVariable.ttf")
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
        self.reload()

    @staticmethod
    def read(path):
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                return {}
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def reload(self):
        settings, session = self.read(self.paths[1]), self.read(self.paths[2])
        self._light = bool(session.get("isLightMode", False))
        data = self.read(self.paths[0])
        colors = data.get("colors", {}).get("light" if self._light else "dark", {})
        if colors:
            self._colors = {k: v for k, v in DEFAULTS.items()}
            self._colors.update({k: v for k, v in colors.items()
                                 if isinstance(v, str) and QColor(v).isValid()})
            self._synced = True
        self._font = str(settings.get("fontFamily", "Inter Variable"))[:120]
        try:
            self._radius = max(0, min(32, int(settings.get("cornerRadius", 12))))
            self._scale = max(0.8, min(1.5, float(settings.get("fontScale", 1))))
        except (ValueError, TypeError):
            pass
        for path in self.paths:
            for candidate in (path, path.parent):
                name = str(candidate)
                if candidate.exists() and name not in self.watcher.files() + self.watcher.directories():
                    self.watcher.addPath(name)
        font = QFont(self._font)
        font.setPointSizeF(10 * self._scale)
        self.app.setFont(font)
        palette = QPalette()
        roles = {QPalette.Window: "surface", QPalette.WindowText: "on_surface",
                 QPalette.Base: "surface_container_high", QPalette.AlternateBase: "surface_container_highest",
                 QPalette.Text: "on_surface", QPalette.Button: "surface_container_high",
                 QPalette.ButtonText: "on_surface", QPalette.Highlight: "primary",
                 QPalette.HighlightedText: "on_primary", QPalette.ToolTipBase: "surface_container_highest",
                 QPalette.ToolTipText: "on_surface", QPalette.PlaceholderText: "on_surface_variant"}
        for role, name in roles.items():
            palette.setColor(role, QColor(self._colors[name]))
        self.app.setPalette(palette)
        self.changed.emit()

    colors = Property("QVariantMap", lambda self: self._colors, notify=changed)
    fontFamily = Property(str, lambda self: self._font, notify=changed)
    radius = Property(int, lambda self: self._radius, notify=changed)
    scale = Property(float, lambda self: self._scale, notify=changed)
    synced = Property(bool, lambda self: self._synced, notify=changed)
    light = Property(bool, lambda self: self._light, notify=changed)
