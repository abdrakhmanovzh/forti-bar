#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$source_dir/dist"
tar --exclude='__pycache__' --exclude='*.pyc' --transform='s,^,forti-bar-0.2.2/,' -czf "$source_dir/dist/forti-bar-0.2.2.tar.gz" \
    -C "$source_dir" fortibar dms packaging scripts tests README.md LICENSE pyproject.toml uv.lock .python-version forti-bar
echo "$source_dir/dist/forti-bar-0.2.2.tar.gz"
