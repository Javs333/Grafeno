#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$PROJECT_ROOT/data"
STEP1_DIR="$PROJECT_ROOT/1-Generation"
STEP2_DIR="$PROJECT_ROOT/2-Simulation"
STEP3_DIR="$PROJECT_ROOT/3-Evaluation"
STEP4_DIR="$PROJECT_ROOT/4-store"
VENV_GEN="$PROJECT_ROOT/environments/.venv-gen"
VENV_SIM="$PROJECT_ROOT/environments/.venv-sim"
VENV_EVAL="$PROJECT_ROOT/environments/.venv-eval"
ENV_FILE="$PROJECT_ROOT/.env"

green()  { echo -e "\033[1;32m==> $*\033[0m"; }
yellow() { echo -e "\033[1;33m    $*\033[0m"; }
red()    { echo -e "\033[1;31mERROR: $*\033[0m" >&2; }

usage() {
  cat <<EOF
Usage: $0 --composition "Si C" [options]
  Run ./setup.sh once before your first pipeline execution.
  --model-name <name>          (default: chemical_system)
  --n-candidates <n>           (default: 5)
  --guidance-factor <float>    (default: 2.0)
  --device <cpu|cuda>          (default: cuda)
  --fmax <float>               (default: 0.05)
  --dmin <float>               (default: 1.0)
  --top-k <n>                  (default: 3)
  --temperature <K>            (default: 0.0)
  --relaxation-type <type>     (default: static; static|md|annealing)
  --md-steps <n>               (default: 1000)
  --annealing-steps <n>        (default: 500)
  --timestep <fs>              (default: 1.0)
  --friction <float>           (default: 0.02)
  --stages <csv>               (default: generate,relax,evaluate,store)
                               allowed: generate,relax,evaluate,store
  --run-dir <path>             (optional existing/new run directory)
EOF
}

COMPOSITION="Si C"
MODEL_NAME="chemical_system"
N_CANDIDATES=5
GUIDANCE_FACTOR=2.0
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
STAGES="generate,relax,evaluate,store"
RUN_DIR_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --composition) COMPOSITION="$2"; shift 2 ;;
    --model-name) MODEL_NAME="$2"; shift 2 ;;
    --n-candidates) N_CANDIDATES="$2"; shift 2 ;;
    --guidance-factor) GUIDANCE_FACTOR="$2"; shift 2 ;;
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
    --stages) STAGES="$2"; shift 2 ;;
    --run-dir) RUN_DIR_ARG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) red "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$COMPOSITION" ]]; then
  red "--composition is required"
  usage
  exit 1
fi
IFS=',' read -r -a STAGE_LIST <<< "$STAGES"
RUN_GENERATE=false
RUN_RELAX=false
RUN_EVALUATE=false
RUN_STORE=false
for stage in "${STAGE_LIST[@]}"; do
  case "$stage" in
    generate) RUN_GENERATE=true ;;
    relax) RUN_RELAX=true ;;
    evaluate) RUN_EVALUATE=true ;;
    store) RUN_STORE=true ;;
    *) red "Invalid stage in --stages: $stage"; usage; exit 1 ;;
  esac
done

if [[ -n "$RUN_DIR_ARG" ]]; then
  RUN_DIR="$RUN_DIR_ARG"
else
  RUN_ID="run_$(date +%Y%m%d_%H%M%S)_${COMPOSITION// /_}"
  RUN_DIR="$DATA_DIR/$RUN_ID"
fi
mkdir -p "$RUN_DIR"
GEN_CIF_DIR="$RUN_DIR/raw_structures/cif"
RELAXED_CIF="$RUN_DIR/relaxed.cif"

green "Starting pipeline"
yellow "Output: $RUN_DIR"
yellow "Stages: $STAGES"
yellow "Execution plan:"
yellow "  [Step 1] Generation: $([[ "$RUN_GENERATE" == true ]] && echo "enabled" || echo "disabled")"
yellow "  [Step 2] Relaxation: $([[ "$RUN_RELAX" == true ]] && echo "enabled" || echo "disabled")"
yellow "  [Step 3] Evaluation: $([[ "$RUN_EVALUATE" == true ]] && echo "enabled" || echo "disabled")"
yellow "  [Step 4] Storage: $([[ "$RUN_STORE" == true ]] && echo "enabled" || echo "disabled")"

yellow "Preflight checks:"
if [[ "$RUN_GENERATE" == true ]]; then
  if [[ ! -d "$VENV_GEN" || ! -f "$VENV_GEN/bin/activate" ]]; then
    yellow "  [Step 1] Blocker: Generation environment missing: $VENV_GEN"
  else
    yellow "  [Step 1] Ready"
  fi
fi
if [[ "$RUN_RELAX" == true ]]; then
  if [[ "$RUN_GENERATE" != true && ! -d "$GEN_CIF_DIR" ]]; then
    yellow "  [Step 2] Blocker: Relax stage requires generation outputs: $GEN_CIF_DIR"
  elif [[ ! -d "$VENV_SIM" || ! -f "$VENV_SIM/bin/activate" ]]; then
    yellow "  [Step 2] Blocker: Simulation environment missing: $VENV_SIM"
  else
    yellow "  [Step 2] Ready"
  fi
fi
if [[ "$RUN_EVALUATE" == true ]]; then
  if [[ "$RUN_RELAX" != true && ! -f "$RELAXED_CIF" ]]; then
    yellow "  [Step 3] Blocker: Evaluate stage requires file: $RELAXED_CIF"
  elif [[ ! -f "$ENV_FILE" ]]; then
    yellow "  [Step 3] Blocker: Missing environment file: $ENV_FILE"
  elif [[ ! -d "$VENV_EVAL" || ! -f "$VENV_EVAL/bin/activate" ]]; then
    yellow "  [Step 3] Blocker: Evaluation environment missing: $VENV_EVAL"
  else
    yellow "  [Step 3] Ready"
  fi
fi
if [[ "$RUN_STORE" == true ]]; then
  if [[ ! -d "$VENV_SIM" || ! -f "$VENV_SIM/bin/activate" ]]; then
    yellow "  [Step 4] Blocker: Simulation environment missing: $VENV_SIM"
  else
    yellow "  [Step 4] Ready"
  fi
fi

if [[ "$RUN_GENERATE" == true ]]; then
  if [[ ! -d "$VENV_GEN" || ! -f "$VENV_GEN/bin/activate" ]]; then
    red "Generation environment missing: $VENV_GEN"
    exit 1
  fi
  green "[Step 1] Generation (.venv-gen)"
  source "$VENV_GEN/bin/activate"
  python "$STEP1_DIR/generate.py" \
    --composition "$COMPOSITION" \
    --model-name "$MODEL_NAME" \
    --n-candidates "$N_CANDIDATES" \
    --guidance-factor "$GUIDANCE_FACTOR" \
    --outdir "$RUN_DIR"
  deactivate
fi

if [[ "$RUN_RELAX" == true && ! -d "$GEN_CIF_DIR" ]]; then
  red "Relax stage requires generation outputs: $GEN_CIF_DIR"
  exit 1
fi

if [[ "$RUN_RELAX" == true || "$RUN_STORE" == true ]]; then
  if [[ ! -d "$VENV_SIM" || ! -f "$VENV_SIM/bin/activate" ]]; then
    red "Simulation environment missing: $VENV_SIM"
    exit 1
  fi
  source "$VENV_SIM/bin/activate"

  if [[ "$RUN_RELAX" == true ]]; then
    green "[Step 2] Relaxation (.venv-sim)"
    python "$STEP2_DIR/relax.py" \
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
  fi

  if [[ "$RUN_STORE" == true ]]; then
    green "[Step 4] Storage"
    python "$STEP4_DIR/store.py" "$RUN_DIR"
  fi
  deactivate
fi

if [[ "$RUN_EVALUATE" == true ]]; then
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
  source "$VENV_EVAL/bin/activate"
  green "[Step 3] Evaluation (.venv-eval)"
  python "$STEP3_DIR/evaluate.py" "$RELAXED_CIF"
  deactivate
fi

green "Pipeline completed"
yellow "Artifacts in: $RUN_DIR"
