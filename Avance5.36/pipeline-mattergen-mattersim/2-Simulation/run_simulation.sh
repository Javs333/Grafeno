#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STEP_DIR="$PROJECT_ROOT/2-Simulation"
VENV_SIM="$PROJECT_ROOT/environments/.venv-sim"

green()  { echo -e "\033[1;32m==> $*\033[0m"; }
yellow() { echo -e "\033[1;33m    $*\033[0m"; }
red()    { echo -e "\033[1;31mERROR: $*\033[0m" >&2; }

usage() {
  cat <<EOF
Usage: $0 --run-dir <path> [options]
  --composition <str>         (default: Si C)
  --device <cpu|cuda>         (default: cuda)
  --fmax <float>              (default: 0.05)
  --dmin <float>              (default: 1.0)
  --top-k <n>                 (default: 3)
  --temperature <K>           (default: 0.0)
  --relaxation-type <type>    (default: static; static|md|annealing)
  --md-steps <n>              (default: 1000)
  --annealing-steps <n>       (default: 500)
  --timestep <fs>             (default: 1.0)
  --friction <float>          (default: 0.02)
EOF
}

RUN_DIR=""
COMPOSITION="Si C"
DEVICE="cuda"
FMAX=0.05
DMIN=1.0
TOP_K=3
TEMPERATURE=0.0
RELAXATION_TYPE="static"
MD_STEPS=1000
ANNEALING_STEPS=500
TIMESTEP=1.0
FRICTION=0.02

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --composition) COMPOSITION="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --fmax) FMAX="$2"; shift 2 ;;
    --dmin) DMIN="$2"; shift 2 ;;
    --top-k) TOP_K="$2"; shift 2 ;;
    --temperature) TEMPERATURE="$2"; shift 2 ;;
    --relaxation-type) RELAXATION_TYPE="$2"; shift 2 ;;
    --md-steps) MD_STEPS="$2"; shift 2 ;;
    --annealing-steps) ANNEALING_STEPS="$2"; shift 2 ;;
    --timestep) TIMESTEP="$2"; shift 2 ;;
    --friction) FRICTION="$2"; shift 2 ;;
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

GEN_CIF_DIR="$RUN_DIR/raw_structures/cif"
if [[ ! -d "$GEN_CIF_DIR" ]]; then
  red "Relax stage requires generation outputs: $GEN_CIF_DIR"
  exit 1
fi

green "[Step 2] Relaxation (.venv-sim)"
yellow "Run dir: $RUN_DIR"
source "$VENV_SIM/bin/activate"
python "$STEP_DIR/relax.py" \
  --input-dir "$GEN_CIF_DIR" \
  --composition "$COMPOSITION" \
  --outdir "$RUN_DIR" \
  --device "$DEVICE" \
  --fmax "$FMAX" \
  --dmin "$DMIN" \
  --top-k "$TOP_K" \
  --temperature "$TEMPERATURE" \
  --relaxation-type "$RELAXATION_TYPE" \
  --md-steps "$MD_STEPS" \
  --annealing-steps "$ANNEALING_STEPS" \
  --timestep "$TIMESTEP" \
  --friction "$FRICTION"
deactivate

green "Simulation completed"
yellow "Artifacts in: $RUN_DIR"
