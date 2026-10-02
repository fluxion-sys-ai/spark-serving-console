#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$ROOT/frontend/dist/index.html" ]] || { echo 'Build the frontend locally before installing this bundle.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'python3 is required.' >&2; exit 1; }
command -v tmux >/dev/null || { echo 'tmux is required to run the console independently of SSH.' >&2; exit 1; }
export MAX_JOBS=1 PIP_DISABLE_PIP_VERSION_CHECK=1
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --only-binary=:all: --no-cache-dir -r "$ROOT/requirements.txt"
"$ROOT/.venv/bin/python" -m pip check
echo "Console environment ready: $ROOT/.venv"
echo 'Start it with ./console.sh start'
