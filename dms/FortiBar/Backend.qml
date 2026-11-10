pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io
import qs.Services

Singleton {
    id: root
    property var state: ({profiles: [], selected: {}, status: "disconnected", error: "", busy: false})
    property bool available: false
    property string bridgeError: ""
    property int nextId: 0
    property var callbacks: ({})
    property var openPanels: ({})
    property var bridgeCommand: []
    property int widgetCount: 0
    property bool stoppingForDetach: false
    signal editRequested(var profile)
    signal passwordRequested(string name)
    signal certificateRequested()

    function request(method, args, callback) {
        if (!worker.running) {
            bridgeError = "VPN controller is not running. Check the Forti Bar installation."
            return
        }
        const id = ++nextId
        if (callback) callbacks[id] = callback
        worker.write(JSON.stringify({id: id, method: method, args: args || []}) + "\n")
    }

    function setPanelVisible(id, visible) {
        const panels = Object.assign({}, openPanels)
        if (visible) panels[id] = true
        else delete panels[id]
        openPanels = panels
        if (available) request("setVisible", [Object.keys(panels).length > 0])
    }

    function receive(line) {
        try {
            const packet = JSON.parse(line)
            if (packet.snapshot) {
                state = packet.snapshot
                const first = !available
                available = true
                bridgeError = ""
                if (first && Object.keys(openPanels).length) request("setVisible", [true])
            } else if (packet.reply !== undefined) {
                const callback = callbacks[packet.reply]
                delete callbacks[packet.reply]
                if (callback) callback(packet.result)
            } else if (packet.event === "edit") editRequested(packet.data)
            else if (packet.event === "password") passwordRequested(packet.data)
            else if (packet.event === "certificate") certificateRequested()
            else if (packet.event === "notification") ToastService.showInfo(packet.data[0], packet.data[1])
        } catch (error) {
            bridgeError = "The VPN controller returned an invalid response."
        }
    }

    function startBridge(command) {
        if (!Array.isArray(command) || !command.length || !command.every(value => typeof value === "string")) {
            bridgeError = "Invalid VPN controller command."
            return
        }
        bridgeCommand = command
        ensureRunning()
    }

    function ensureRunning() {
        if (worker.running || !bridgeCommand.length || widgetCount === 0) return
        const command = bridgeCommand
        worker.command = command
        worker.running = true
        startupTimeout.restart()
    }

    function attachWidget() {
        widgetCount++
        shutdownDelay.stop()
        ensureRunning()
    }

    function detachWidget() {
        widgetCount = Math.max(0, widgetCount - 1)
        if (!widgetCount) shutdownDelay.restart()
    }

    Timer {
        id: shutdownDelay
        interval: 300
        onTriggered: {
            if (!root.widgetCount && worker.running) {
                root.stoppingForDetach = true
                worker.signal(15)
            }
        }
    }

    Timer {
        id: startupTimeout
        interval: 5000
        onTriggered: if (!root.available) root.bridgeError = "Could not start Forti Bar. Check the controller installation."
    }

    FileView {
        preload: true
        printErrors: false
        path: Qt.resolvedUrl("backend.json").toString().replace("file://", "")
        onLoaded: {
            try { root.startBridge(JSON.parse(text())) }
            catch (error) { root.bridgeError = "Invalid VPN controller command." }
        }
        onLoadFailed: root.startBridge(["forti-bar", "--dms-bridge"])
    }

    Process {
        id: worker
        stdinEnabled: true
        stdout: SplitParser { onRead: data => root.receive(data) }
        // Do not collect or persist stderr: diagnostics and credentials stay
        // inside the backend's bounded, redacted activity stream.
        onExited: {
            const restart = root.stoppingForDetach && root.widgetCount > 0
            root.available = false
            root.state = Object.assign({}, root.state, {status: "disconnected", busy: false, localIp: ""})
            root.callbacks = ({})
            root.bridgeError = root.widgetCount > 0 && !root.stoppingForDetach ? "VPN controller stopped. Reload the Forti Bar widget to restart it." : ""
            root.stoppingForDetach = false
            if (restart) Qt.callLater(root.ensureRunning)
        }
    }
}
