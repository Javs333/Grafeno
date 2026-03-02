#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STEP_DIR="$PROJECT_ROOT/4-store"
VENV_SIM="$PROJECT_ROOT/environments/.venv-sim"

green()  { echo -e "\033[1;32m==> $*\033[0m"; }
yellow() { echo -e "\033[1;33m    $*\033[0m"; }
red()    { echo -e "\033[1;31mERROR: $*\033[0m" >&2; }

usage() {
  cat <<EOF
Usage: $0 --run-dir <path>
EOF
}

RUN_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) red "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$RUN_DIR" ]]; then
  red "--run-dir is required"
  usage
  exit 1
fi
if [[ ! -d "$VENV_SIM" || ! -f "$VENV_SIM/bin/activate" ]]; then
  red "Simulation environment missing: $VENV_SIM"
  exit 1
fi

green "[Step 4] Storage"
yellow "Run dir: $RUN_DIR"
source "$VENV_SIM/bin/activate"
python "$STEP_DIR/store.py" "$RUN_DIR"
deactivate

green "Storage completed"
yellow "Artifacts in: $RUN_DIR"
