# 1-Generation standalone runner

Run generation only:

```bash
./1-Generation/run_generation.sh --composition "Si C"
```

Parameters:
- `--composition` (optional, default: `Si C`)
- `--model-name` (optional, default: `chemical_system`)
- `--n-candidates` (optional, default: `5`)
- `--guidance-factor` (optional, default: `2.0`)
- `--run-dir` (optional, output directory; if omitted, one is created under `data/`)

