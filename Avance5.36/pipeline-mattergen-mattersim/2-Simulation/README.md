# 2-Simulation standalone runner

Run relaxation only:

```bash
./2-Simulation/run_simulation.sh --run-dir data/<your_run_dir> --composition "Si C"
```

Parameters:
- `--run-dir` (required, must contain `raw_structures/cif/`)
- `--composition` (optional, default: `Si C`)
- `--device` (optional, default: `cuda`)
- `--fmax` (optional, default: `0.05`)
- `--dmin` (optional, default: `1.0`)
- `--top-k` (optional, default: `3`)
- `--temperature` (optional, default: `0.0`)
- `--relaxation-type` (optional, default: `static`; `static|md|annealing`)
- `--md-steps` (optional, default: `1000`)
- `--annealing-steps` (optional, default: `500`)
- `--timestep` (optional, default: `1.0`)
- `--friction` (optional, default: `0.02`)

