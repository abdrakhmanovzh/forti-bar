#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_root="${DESTDIR:-}"

if [[ -z "$install_root" && "$EUID" -ne 0 ]]; then
    echo 'Run sudo ./scripts/install.sh to install the application and host helper.' >&2
    exit 1
fi

if [[ -z "$install_root" ]]; then
    if ! command -v openfortivpn >/dev/null; then
        if command -v rpm-ostree >/dev/null; then
            echo 'First run: sudo rpm-ostree install openfortivpn' >&2
            echo 'Reboot into the new deployment, then run this installer again.' >&2
        else
            echo 'Install openfortivpn with your distribution package manager, then run this installer again.' >&2
        fi
        exit 1
    fi
    if ! python3 -c 'from PySide6 import QtCore, QtGui, QtWidgets, QtQuick, QtQml, QtSvg; import ctypes; ctypes.CDLL("libsecret-1.so.0")' 2>/dev/null; then
        echo 'Install Python 3, PySide6 (Qt Quick, Widgets and SVG), and libsecret first.' >&2
        exit 1
    fi
fi

install -d -m 755 "$install_root/usr/lib/forti-bar/fortibar" \
    "$install_root/usr/bin" "$install_root/usr/libexec" \
    "$install_root/usr/share/polkit-1/actions" "$install_root/usr/share/applications" \
    "$install_root/usr/share/icons/hicolor/scalable/apps"
# DMS discovers system-wide plugins here; each user enables it in their own settings.
plugin_dir="$install_root/etc/xdg/quickshell/dms-plugins/FortiBar"
install -d -m 755 "$plugin_dir"
install -m 644 "$source_dir"/dms/FortiBar/* "$plugin_dir/"
printf '["/usr/bin/forti-bar", "--dms-bridge"]\n' > "$plugin_dir/backend.json"
chmod 644 "$plugin_dir/backend.json"
install -m 644 "$source_dir"/fortibar/*.py "$install_root/usr/lib/forti-bar/fortibar/"
install -m 755 "$source_dir/packaging/forti-bar-launcher" "$install_root/usr/bin/forti-bar"
install -m 755 "$source_dir/packaging/forti-bar-helper" "$install_root/usr/libexec/forti-bar-helper"
install -m 644 "$source_dir/packaging/io.github.fortibar.policy" "$install_root/usr/share/polkit-1/actions/"
install -m 644 "$source_dir/packaging/io.github.fortibar.desktop" "$install_root/usr/share/applications/"
install -m 644 "$source_dir/packaging/io.github.fortibar.svg" "$install_root/usr/share/icons/hicolor/scalable/apps/"

if [[ -z "$install_root" ]]; then
    chown -R root:root /usr/lib/forti-bar /etc/xdg/quickshell/dms-plugins/FortiBar
    chown root:root /usr/bin/forti-bar /usr/libexec/forti-bar-helper \
        /usr/share/polkit-1/actions/io.github.fortibar.policy
    command -v update-desktop-database >/dev/null && update-desktop-database /usr/share/applications || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
    echo 'Installed. Run forti-bar as your desktop user to add the VPN icon to your DMS bar.'
fi
