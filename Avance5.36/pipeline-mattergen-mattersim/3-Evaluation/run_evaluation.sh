#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STEP_DIR="$PROJECT_ROOT/3-Evaluation"
VENV_EVAL="$PROJECT_ROOT/environments/.venv-eval"
ENV_FILE="$PROJECT_ROOT/.env"

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

RELAXED_CIF="$RUN_DIR/relaxed.cif"
if [[ ! -f "$RELAXED_CIF" ]]; then
  red "Evaluate stage requires file: $RELAXED_CIF"
  exit 1
fi
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
else
  red "Missing environment file: $ENV_FILE"
  exit 1
fi
if [[ ! -d "$VENV_EVAL" || ! -f "$VENV_EVAL/bin/activate" ]]; then
  red "Evaluation environment missing: $VENV_EVAL"
  exit 1
fi

green "[Step 3] Evaluation (.venv-eval)"
yellow "Run dir: $RUN_DIR"
source "$VENV_EVAL/bin/activate"
python "$STEP_DIR/evaluate.py" "$RELAXED_CIF"
deactivate

green "Evaluation completed"
yellow "Artifacts in: $RUN_DIR"
