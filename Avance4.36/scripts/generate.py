#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path

from ase.io import read, write


def parse_args():
    p = argparse.ArgumentParser(description="MatterGen generation step")
    p.add_argument("--composition", required=True, help="Composition like 'Si C'")
    p.add_argument("--model-name", default="chemical_system", help="MatterGen model name")
    p.add_argument("--n-candidates", type=int, default=5, help="Number of candidates")
    p.add_argument("--guidance-factor", type=float, default=2.0, help="Diffusion guidance factor")
    p.add_argument("--outdir", required=True, help="Run output directory")
    return p.parse_args()


def generate_with_mattergen(composition: str, n_candidates: int, model_name: str, guidance_factor: float, outdir: Path):
    raw_dir = outdir / "raw_structures"
    raw_dir.mkdir(parents=True, exist_ok=True)
    chemical_system = composition.replace(" ", "-")

    cmd = [
        "mattergen-generate",
        str(raw_dir),
        f"--pretrained-name={model_name}",
        f"--batch_size={n_candidates}",
        "--num_batches",
        "1",
        f"--properties_to_condition_on=\"{{'chemical_system': '{chemical_system}'}}\"",
        f"--diffusion_guidance_factor={guidance_factor}",
    ]

    print(f"[Generation] Running: {' '.join(cmd)}")
    result = subprocess.run(" ".join(cmd), shell=True, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f"MatterGen failed.\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")

    extxyz = raw_dir / "generated_crystals.extxyz"
    if not extxyz.exists():
        matches = list(raw_dir.glob("*.extxyz"))
        if not matches:
            raise FileNotFoundError(f"No .extxyz file generated in {raw_dir}")
        extxyz = matches[0]

    atoms_list = read(extxyz, index=":")
    if not isinstance(atoms_list, list):
        atoms_list = [atoms_list]
    return atoms_list[:n_candidates], extxyz, raw_dir


def write_candidates(atoms_list, raw_dir: Path):
    cif_dir = raw_dir / "cif"
    cif_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, atoms in enumerate(atoms_list):
        cif_path = cif_dir / f"cand_{idx:03d}.cif"
        write(cif_path, atoms)
        paths.append(str(cif_path))
    return paths


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    atoms_list, extxyz_path, raw_dir = generate_with_mattergen(
        composition=args.composition,
        n_candidates=args.n_candidates,
        model_name=args.model_name,
        guidance_factor=args.guidance_factor,
        outdir=outdir,
    )
    cif_paths = write_candidates(atoms_list, raw_dir)

    manifest = {
        "composition": args.composition,
        "model_name": args.model_name,
        "guidance_factor": args.guidance_factor,
        "n_candidates_requested": args.n_candidates,
        "n_candidates_generated": len(cif_paths),
        "extxyz_path": str(extxyz_path),
        "cif_paths": cif_paths,
    }
    manifest_path = outdir / "generation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[Generation] Done. Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
