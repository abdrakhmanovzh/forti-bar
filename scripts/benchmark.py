#!/usr/bin/env python3
"""Measure the DMS controller; no display, credentials or VPN connection needed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

source = Path(__file__).resolve().parent.parent
process = subprocess.Popen([sys.executable, str(source / "forti-bar"), "--dms-bridge", "--demo"],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    assert "snapshot" in json.loads(process.stdout.readline())
    values = Path(f"/proc/{process.pid}/smaps_rollup").read_text().splitlines()
    pss = next(int(line.split()[1]) / 1024 for line in values if line.startswith("Pss:"))
    def ticks():
        fields = Path(f"/proc/{process.pid}/stat").read_text().split()
        return int(fields[13]) + int(fields[14])
    initial = ticks()
    start = time.monotonic()
    time.sleep(2)
    elapsed = time.monotonic() - start
    percent = (ticks() - initial) / os.sysconf("SC_CLK_TCK") / elapsed * 100
    print(json.dumps({"mode": "native DMS headless controller; no VPN", "controller_pss_mib": round(pss, 2),
                      "idle_cpu_percent_one_core": round(percent, 3)}, indent=2))
finally:
    process.stdin.close()
    process.wait(timeout=5)
