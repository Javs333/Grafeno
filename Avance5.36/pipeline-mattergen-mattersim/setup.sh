#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$PROJECT_ROOT/environments"
VENV_GEN="$ENV_DIR/.venv-gen"
VENV_SIM="$ENV_DIR/.venv-sim"
VENV_EVAL="$ENV_DIR/.venv-eval"
PYTHON_BIN="python3"
SETUP_GEN=true
SETUP_SIM=true
RUN_VERIFY=true

green()  { echo -e "\033[1;32m==> $*\033[0m"; }
yellow() { echo -e "\033[1;33m    $*\033[0m"; }
red()    { echo -e "\033[1;31mERROR: $*\033[0m" >&2; }

usage() {
  cat <<EOF
Usage: $0 [options]
  --python <binary>   Python executable to use (default: python3)
  --skip-gen          Skip creating/updating .venv-gen
  --skip-sim          Skip creating/updating .venv-sim
  Note: .venv-eval is expected at environments/.venv-eval (managed manually).
  --no-verify         Skip import verification checks
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --skip-gen) SETUP_GEN=false; shift ;;
    --skip-sim) SETUP_SIM=false; shift ;;
    --no-verify) RUN_VERIFY=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) red "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { red "Python binary not found: $PYTHON_BIN"; exit 1; }

create_venv() {
  local venv_path="$1"
  mkdir -p "$(dirname "$venv_path")"
  if [[ ! -f "$venv_path/bin/python" ]]; then
    green "Creating virtual environment: $venv_path"
    "$PYTHON_BIN" -m venv "$venv_path"
  else
    yellow "Reusing existing virtual environment: $venv_path"
  fi
}

install_gen() {
  create_venv "$VENV_GEN"
  green "Installing generation dependencies in .venv-gen"
  "$VENV_GEN/bin/python" -m pip install --upgrade pip setuptools wheel
  "$VENV_GEN/bin/python" -m pip install -r "$PROJECT_ROOT/requirements-gen.txt"
  "$VENV_GEN/bin/uv" pip install --python "$VENV_GEN/bin/python" -e "$PROJECT_ROOT/generation/mattergen"
}

install_sim() {
  create_venv "$VENV_SIM"
  green "Installing simulation dependencies in .venv-sim"
  "$VENV_SIM/bin/python" -m pip install --upgrade pip setuptools wheel
  "$VENV_SIM/bin/python" -m pip install -r "$PROJECT_ROOT/requirements-sim.txt"
}

verify_envs() {
  green "Running verification checks"
  if [[ "$SETUP_GEN" == true ]]; then
    "$VENV_GEN/bin/python" -c "import ase; import mattergen"
    yellow ".venv-gen verification passed"
  fi
  if [[ "$SETUP_SIM" == true ]]; then
    "$VENV_SIM/bin/python" -c "import ase, matplotlib, numpy, pandas, pydantic, requests; import azure.storage.blob, psycopg; from mattersim.forcefield import MatterSimCalculator"
    yellow ".venv-sim verification passed"
  fi
  if [[ ! -f "$VENV_EVAL/bin/python" ]]; then
    red "Evaluation environment missing: $VENV_EVAL"
    red "Create it manually and install your eval requirements before running evaluate stage."
    exit 1
  fi
  "$VENV_EVAL/bin/python" -c "import ase, matplotlib, pydantic, requests"
  yellow ".venv-eval verification passed"
}

if [[ "$SETUP_GEN" == true ]]; then
  install_gen
fi
if [[ "$SETUP_SIM" == true ]]; then
  install_sim
fi
if [[ "$RUN_VERIFY" == true ]]; then
  verify_envs
fi

green "Setup complete"
yellow "You can now run: ./run_pipeline.sh --composition \"Si C\""
