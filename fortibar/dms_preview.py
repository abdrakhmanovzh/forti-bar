"""Render actual installed DMS controls with fixture shell services, offscreen only.

The product runs inside DMS's PluginPopout. This harness avoids initializing a
second desktop shell or accessing the user's display while checking its content.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QObject, Property, QEvent, QUrl, Signal, Slot, QTimer, qInstallMessageHandler
from PySide6.QtQml import QJSValue
from PySide6.QtQuick import QQuickView
from PySide6.QtWidgets import QApplication

from .controller import Controller
from .theme import Appearance


class PreviewBackend(QObject):
    changed = Signal()
    editRequested = Signal("QVariantMap")
    passwordRequested = Signal(str)
    certificateRequested = Signal()

    def __init__(self):
        super().__init__()
        self.controller = Controller(demo=True)
        self.controller.changed.connect(self.changed)
        self.controller.editRequested.connect(self.editRequested)
        self.controller.passwordRequested.connect(self.passwordRequested)
        self.controller.certificateRequested.connect(self.certificateRequested)

    @Property("QVariantMap", notify=changed)
    def state(self):
        from .dms_bridge import PROPERTIES
        return {key: getattr(self.controller, key) for key in PROPERTIES}

    available = Property(bool, lambda s: True, constant=True)
    bridgeError = Property(str, lambda s: "", constant=True)

    @Slot(str)
    @Slot(str, "QVariantList")
    @Slot(str, "QVariantList", QJSValue)
    def request(self, method, args=None, callback=None):
        result = getattr(self.controller, method)(*(args or []))
        if callback and callback.isCallable():
            callback.call([QJSValue(bool(result))])

    @Slot(str, bool)
    def setPanelVisible(self, _id, visible):
        self.controller.setVisible(visible)


def prepare_imports(directory, appearance):
    installed = Path("/usr/share/quickshell/dms")
    if not installed.exists():
        raise RuntimeError("Install DMS to render its native controls.")
    directory = Path(directory)
    common = directory / "qs/Common"
    widgets = directory / "qs/Widgets"
    plugins = directory / "qs/Modules/Plugins"
    for path in (common, widgets, plugins):
        path.mkdir(parents=True)
    for name in ("StyledRect", "StyledText", "DankIcon", "DankButton", "DankActionButton", "DankToggle", "DankTextField", "StateLayer", "DankRipple"):
        shutil.copy2(installed / f"Widgets/{name}.qml", widgets / f"{name}.qml")
    (directory / "qs/assets").symlink_to(installed / "assets", target_is_directory=True)
    (directory / "qs/Shaders").symlink_to(installed / "Shaders", target_is_directory=True)
    # Only the tooltip's shell window is substituted; it is never visible in
    # these captures. Buttons, fields, toggles, icons and layout are DMS files.
    (widgets / "DankTooltipV2.qml").write_text('import QtQuick\nQtObject { function show() {} function hide() {} }\n')
    (widgets / "qmldir").write_text("module qs.Widgets\n" + "\n".join(f"{p.stem} 1.0 {p.name}" for p in widgets.glob("*.qml")))
    shutil.copy2(installed / "Modules/Plugins/PopoutComponent.qml", plugins / "PopoutComponent.qml")
    (plugins / "qmldir").write_text("module qs.Modules.Plugins\nPopoutComponent 1.0 PopoutComponent.qml\n")
    c = appearance.colors
    aliases = {"surface": "surface", "surfaceContainer": "surface_container", "surfaceContainerHigh": "surface_container_high", "surfaceContainerHighest": "surface_container_highest", "surfaceText": "on_surface", "surfaceVariantText": "on_surface_variant", "primary": "primary", "primaryContainer": "primary_container", "outline": "outline", "outlineMedium": "outline_variant", "outlineButton": "outline", "error": "error", "buttonBg": "primary", "buttonText": "on_primary"}
    declarations = "\n".join(f'property color {name}: "{c[key]}"' for name, key in aliases.items())
    theme = '''pragma Singleton
import QtQuick
QtObject {
COLORS
property int cornerRadius: 12
property int fontWeight: 400
property int fontSizeSmall: 12
property int fontSizeMedium: 14
property int fontSizeLarge: 16
property int iconSize: 24
property int iconSizeSmall: 16
property int spacingXS: 4
property int spacingS: 8
property int spacingM: 12
property int spacingL: 16
property int shortDuration: 0
property int shorterDuration: 0
property int currentAnimationSpeed: 0
property int standardEasing: Easing.OutCubic
property bool isLightMode: false
property real popupTransparency: 1
property string fontFamily: "Inter Variable"
property string monoFontFamily: "Fira Code"
property string defaultFontFamily: "Inter Variable"
property string defaultMonoFontFamily: "Fira Code"
property var expressiveCurves: ({standard: [0.2,0,0,1,1,1], standardDecel: [0.2,0,0,1,1,1]})
property var expressiveDurations: ({normal: 0, expressiveDefaultSpatial: 0})
property color surfaceVariant: "%s"
property color background: "%s"
property color surfaceLight: withAlpha(surfaceVariant, 0.1)
property color surfaceHover: withAlpha(surfaceVariant, 0.08)
property color surfacePressed: withAlpha(surfaceVariant, 0.12)
property color surfaceVariantAlpha: withAlpha(surfaceVariant, 0.2)
property color surfaceTextHover: withAlpha(surfaceText, 0.08)
property color surfaceTextMedium: withAlpha(surfaceText, 0.7)
property color outlineLight: withAlpha(outline, 0.05)
property color outlineStrong: withAlpha(outline, 0.12)
property color primaryHoverLight: withAlpha(primary, 0.08)
property color primaryPressed: withAlpha(primary, 0.16)
property color errorHover: withAlpha(error, 0.12)
function withAlpha(color, alpha) { return Qt.rgba(color.r,color.g,color.b,alpha) }
}
'''.replace("COLORS", declarations) % (c.get("surface_variant", c["on_surface_variant"]), c.get("background", c["surface"]))
    (common / "Theme.qml").write_text(theme)
    (common / "Appearance.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { property var rounding: ({normal: 12}); property var fontSize: ({normal:14, small:12}); property var anim: ({durations:{normal:0}, curves:{standard:[0.2,0,0,1,1,1], emphasizedDecel:[0.2,0,0,1,1,1], emphasized:[0.2,0,0,1,1,1]}}) }\n')
    (common / "SettingsData.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { enum AnimationSpeed {None, Normal} property bool enableRippleEffects: false }\n')
    (common / "I18n.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { property bool isRtl: false }\n')
    for name in ("DankAnim", "DankColorAnim"):
        shutil.copy2(installed / f"Common/{name}.qml", common / f"{name}.qml")
    (common / "qmldir").write_text("module qs.Common\n" + "\n".join(("singleton " if p.stem in ("Theme", "Appearance", "SettingsData", "I18n") else "") + f"{p.stem} 1.0 {p.name}" for p in common.glob("*.qml")))
    return directory


class PanelPreview:
    def __init__(self, app, *, page="connections"):
        self.temp = tempfile.TemporaryDirectory(prefix="forti-bar-dms-preview-")
        self.appearance = Appearance(app)
        root = prepare_imports(self.temp.name, self.appearance)
        self.backend = PreviewBackend()
        source = Path(__file__).resolve().parent.parent / "dms/FortiBar"
        if not source.exists():
            source = Path("/etc/xdg/quickshell/dms-plugins/FortiBar")
        (root / "Panel").mkdir()
        for name in ("FortiPanel.qml", "FortiField.qml"):
            shutil.copy2(source / name, root / "Panel" / name)
        (root / "Panel/qmldir").write_text("module Panel\nFortiPanel 1.0 FortiPanel.qml\nFortiField 1.0 FortiField.qml\n")
        (root / "preview.qml").write_text('''import QtQuick
import qs.Common
import Panel
Rectangle {
    width: 392
    height: menu.implicitHeight + 16
    color: Theme.surfaceContainer
    radius: Theme.cornerRadius
    FortiPanel { id: menu; objectName: "menu"; x: 8; y: 8; width: parent.width - 16; backend: previewBackend }
}''')
        self.view = QQuickView()
        self.view.engine().addImportPath(str(root))
        self.view.rootContext().setContextProperty("previewBackend", self.backend)
        # QQuickView's loader thread can log while setSource holds Python's
        # GIL. Keep loading on Qt's native logger, then collect runtime errors.
        handler = qInstallMessageHandler(None)
        try:
            self.view.setSource(QUrl.fromLocalFile(str(root / "preview.qml")))
        finally:
            qInstallMessageHandler(handler)
        if self.view.status() == QQuickView.Error:
            raise RuntimeError("\n".join(e.toString() for e in self.view.errors()))
        self.view.show()
        self.panel = self.view.rootObject().findChild(QObject, "menu")
        if page == "edit":
            self.panel.edit(self.backend.controller.selected_profile().to_dict())
        elif page != "connections":
            self.panel.setProperty("page", page)

    def close(self):
        self.backend.controller.secrets.cancel_all()
        self.view.close()
        self.view.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()


def render(path, *, page="connections"):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("forti-bar-preview")
    errors = []
    previous = qInstallMessageHandler(lambda _kind, _context, message: errors.append(message))
    preview = PanelPreview(app, page=page)
    success = []
    def capture():
        success.append(preview.view.grabWindow().save(path))
        app.quit()
    QTimer.singleShot(300, capture)
    app.exec()
    preview.close()
    qInstallMessageHandler(previous)
    for error in errors:
        print(error, file=__import__("sys").stderr)
    return 0 if success and success[0] and not any("Error:" in e for e in errors) else 1
