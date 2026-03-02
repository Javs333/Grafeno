#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data"
STEP_DIR="$PROJECT_ROOT/1-Generation"
VENV_GEN="$PROJECT_ROOT/environments/.venv-gen"

green()  { echo -e "\033[1;32m==> $*\033[0m"; }
yellow() { echo -e "\033[1;33m    $*\033[0m"; }
red()    { echo -e "\033[1;31mERROR: $*\033[0m" >&2; }

usage() {
  cat <<EOF
Usage: $0 [options]
  --composition <str>         (default: Si C)
  --model-name <name>         (default: chemical_system)
  --n-candidates <n>          (default: 5)
  --guidance-factor <float>   (default: 2.0)
  --run-dir <path>            (optional existing/new run directory)
EOF
}

COMPOSITION="Si C"
MODEL_NAME="chemical_system"
N_CANDIDATES=5
GUIDANCE_FACTOR=2.0
RUN_DIR_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --composition) COMPOSITION="$2"; shift 2 ;;
    --model-name) MODEL_NAME="$2"; shift 2 ;;
    --n-candidates) N_CANDIDATES="$2"; shift 2 ;;
    --guidance-factor) GUIDANCE_FACTOR="$2"; shift 2 ;;
    --run-dir) RUN_DIR_ARG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) red "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ ! -d "$VENV_GEN" || ! -f "$VENV_GEN/bin/activate" ]]; then
  red "Generation environment missing: $VENV_GEN"
  exit 1
fi

if [[ -n "$RUN_DIR_ARG" ]]; then
  RUN_DIR="$RUN_DIR_ARG"
else
  RUN_ID="run_$(date +%Y%m%d_%H%M%S)_${COMPOSITION// /_}"
  RUN_DIR="$DATA_DIR/$RUN_ID"
fi
mkdir -p "$RUN_DIR"

green "[Step 1] Generation (.venv-gen)"
yellow "Output: $RUN_DIR"
source "$VENV_GEN/bin/activate"
python "$STEP_DIR/generate.py" \
  --composition "$COMPOSITION" \
  --model-name "$MODEL_NAME" \
  --n-candidates "$N_CANDIDATES" \
  --guidance-factor "$GUIDANCE_FACTOR" \
  --outdir "$RUN_DIR"
deactivate

green "Generation completed"
yellow "Artifacts in: $RUN_DIR"
