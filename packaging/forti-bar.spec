Name:           forti-bar
Version:        0.2.2
Release:        1%{?dist}
Summary:        A native Fortinet VPN companion with DMS integration
License:        MIT
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
AutoReqProv:    no
Requires:       python3
Requires:       python3-pyside6 >= 6.7
Requires:       ppp >= 2.5
Requires:       libsecret
Requires:       openfortivpn
Requires:       polkit
Requires:       systemd-resolved

# The entrypoints use fixed isolated import paths, so list runtime dependencies
# explicitly rather than generating requirements for source-only Python files.

%description
A native DankMaterialShell top-bar menu for openfortivpn. Includes saved
profiles, desktop keyring integration and native shell controls and theming.

%prep
%setup -q -T -c
tar -xzf %{SOURCE0} --strip-components=1

%build

%install
DESTDIR=%{buildroot} bash scripts/install.sh

%files
%license LICENSE
%doc README.md
/usr/bin/forti-bar
/usr/libexec/forti-bar-helper
/usr/lib/forti-bar/
%dir %{_sysconfdir}/xdg/quickshell/dms-plugins
%{_sysconfdir}/xdg/quickshell/dms-plugins/FortiBar/
%{_datadir}/applications/io.github.fortibar.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.fortibar.svg
%{_datadir}/polkit-1/actions/io.github.fortibar.policy

%changelog
* Tue Nov 10 2026 Forti Bar contributors - 0.2.2-1
- Ship the DMS widget in the system plugin directory; forti-bar only enables it per user.
- Compact popout redesign: click a connection to connect, smaller header, fields and switch.

* Mon Nov 09 2026 Forti Bar contributors - 0.2.1-1
- Offer explicit shell restart recovery when first-install discovery is stuck.
- Detect the installed DMS IPC syntax and verify the widget loads and opens.
- Report activation failures instead of claiming installation succeeded.
- Use a native DMS top-bar popout for connections, profiles and certificate review.
