"""CLI for the native DMS widget and its headless VPN controller."""

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Forti Bar — VPN controls in the DMS top-bar menu")
    parser.add_argument("--install-dms", action="store_true", help="Install and enable the DMS widget beside the system tray")
    parser.add_argument("--restart-dms", action="store_true", help="Restart the DMS desktop service after installing the widget (refreshes plugin discovery)")
    parser.add_argument("--dms-bridge", action="store_true", help="Run the headless controller owned by DMS")
    parser.add_argument("--demo", action="store_true", help="Use an ephemeral preview profile; never starts a VPN")
    parser.add_argument("--screenshot", metavar="PATH", help="Render the native DMS popout content offscreen (requires --demo)")
    parser.add_argument("--page", choices=("connections", "edit", "activity"), default="connections", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.dms_bridge:
        from .dms_bridge import main as bridge_main
        return bridge_main(demo=args.demo)
    if args.screenshot:
        if not args.demo:
            parser.error("--screenshot requires --demo")
        from .dms_preview import render
        return render(args.screenshot, page=args.page)
    if args.demo:
        parser.error("Use --demo with --screenshot to preview without changing desktop settings.")
    from .dms_install import install
    try:
        if args.restart_dms:
            install(restart_dms=True)
        else:
            install()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Forti Bar could not open its DMS menu: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
