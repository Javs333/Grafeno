#!/usr/bin/env python3
import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from ase import units
from ase.io import read, write
from ase.io.trajectory import Trajectory
from ase.md.langevin import Langevin
from ase.optimize import FIRE

from mattersim.forcefield import MatterSimCalculator


def _json_default(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def parse_args():
    p = argparse.ArgumentParser(description="MatterSim relaxation step")
    p.add_argument("--input-dir", required=True, help="Directory with generated CIF files")
    p.add_argument("--composition", required=True, help="Expected composition like 'Si C'")
    p.add_argument("--outdir", required=True, help="Run output directory")
    p.add_argument("--device", default="cuda", choices=["cpu", "cuda"])
    p.add_argument("--fmax", type=float, default=0.05)
    p.add_argument("--dmin", type=float, default=1.0)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--relaxation-type", default="static", choices=["static", "md", "annealing"])
    p.add_argument("--md-steps", type=int, default=1000)
    p.add_argument("--timestep", type=float, default=1.0)
    p.add_argument("--annealing-steps", type=int, default=500)
    p.add_argument("--friction", type=float, default=0.02)
    return p.parse_args()


def pick_device(device: str) -> str:
    if device == "cuda":
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass
        print("CUDA not available, using CPU")
        return "cpu"
    return device


def min_interatomic_distance(atoms) -> float:
    n = len(atoms)
    if n < 2:
        return float("nan")
    d_min = float("inf")
    for i in range(n):
        for j in range(i + 1, n):
            d = atoms.get_distance(i, j, mic=True)
            if d < d_min:
                d_min = d
    return d_min


def verify_composition(atoms, expected_composition: str) -> bool:
    expected_elements = set(expected_composition.split())
    actual_elements = set(atoms.get_chemical_symbols())
    return actual_elements == expected_elements


def maxwell_boltzmann_distribution(atoms, temperature_K):
    masses = atoms.get_masses()
    n = len(atoms)
    velocities = np.random.standard_normal((n, 3))
    sigma = np.sqrt(units.kB * temperature_K / masses.reshape(n, 1))
    velocities *= sigma
    atoms.set_velocities(velocities)


@dataclass
class CandidateResult:
    idx: int
    composition: str
    natoms: int
    energy_eV: float
    energy_per_atom_eV: float
    fmax_eV_per_A: float
    dmin_A: float
    volume_A3: float
    cif_path: str
    traj_path: str
    accepted: bool


def create_failed_result(idx, composition, natoms):
    return CandidateResult(
        idx=idx,
        composition=composition,
        natoms=natoms,
        energy_eV=float("nan"),
        energy_per_atom_eV=float("nan"),
        fmax_eV_per_A=float("nan"),
        dmin_A=float("nan"),
        volume_A3=float("nan"),
        cif_path="",
        traj_path="",
        accepted=False,
    )


def relax_with_mattersim_static(atoms, idx: int, composition: str, device: str, fmax: float, max_steps: int, outdir: Path):
    natoms = len(atoms)
    try:
        calc = MatterSimCalculator(device=device)
        atoms.calc = calc
        opt = FIRE(atoms, logfile=str(outdir / f"cand_{idx:03d}_static.log"))
        opt.run(fmax=fmax, steps=max_steps)
        energy = atoms.get_potential_energy()
        forces = atoms.get_forces()
        fmax_val = float(np.linalg.norm(forces, axis=1).max()) if len(forces) else float("nan")
        dmin = min_interatomic_distance(atoms)
        vol = atoms.get_volume()
        epa = energy / natoms if natoms else float("nan")
        cif_path = outdir / f"cand_{idx:03d}_static.cif"
        traj_path = outdir / f"cand_{idx:03d}_static.traj"
        write(cif_path, atoms)
        write(traj_path, atoms)
        return CandidateResult(idx, composition, natoms, energy, epa, fmax_val, dmin, vol, str(cif_path), str(traj_path), False)
    except Exception:
        return create_failed_result(idx, composition, natoms)


def relax_with_mattersim_temperature(atoms, idx: int, composition: str, device: str, temperature: float, timestep: float, steps: int, friction: float, outdir: Path):
    natoms = len(atoms)
    try:
        calc = MatterSimCalculator(device=device)
        atoms.calc = calc
        maxwell_boltzmann_distribution(atoms, temperature)
        dyn = Langevin(atoms, timestep=timestep * units.fs, temperature_K=temperature, friction=friction)
        traj_path = outdir / f"cand_{idx:03d}_md.traj"
        traj = Trajectory(traj_path, "w", atoms)
        dyn.attach(traj.write, interval=10)
        dyn.run(steps)
        energy = atoms.get_potential_energy()
        forces = atoms.get_forces()
        fmax_val = float(np.linalg.norm(forces, axis=1).max()) if len(forces) else float("nan")
        dmin = min_interatomic_distance(atoms)
        epa = energy / natoms if natoms else float("nan")
        cif_path = outdir / f"cand_{idx:03d}_final.cif"
        write(cif_path, atoms)
        return CandidateResult(idx, composition, natoms, energy, epa, fmax_val, dmin, atoms.get_volume(), str(cif_path), str(traj_path), False)
    except Exception:
        return create_failed_result(idx, composition, natoms)


def relax_with_annealing(atoms, idx: int, composition: str, device: str, initial_temp: float, final_temp: float, annealing_steps: int, timestep: float, outdir: Path):
    natoms = len(atoms)
    try:
        calc = MatterSimCalculator(device=device)
        atoms.calc = calc
        maxwell_boltzmann_distribution(atoms, initial_temp)
        traj_path = outdir / f"cand_{idx:03d}_anneal.traj"
        traj = Trajectory(traj_path, "w", atoms)
        current_temp = initial_temp
        temp_step = (initial_temp - final_temp) / max(annealing_steps, 1)
        for step in range(annealing_steps):
            dyn = Langevin(atoms, timestep=timestep * units.fs, temperature_K=current_temp, friction=0.02)
            dyn.run(10)
            if step % 10 == 0:
                traj.write(atoms)
            current_temp -= temp_step
        opt = FIRE(atoms)
        opt.run(fmax=0.05, steps=100)
        energy = atoms.get_potential_energy()
        forces = atoms.get_forces()
        fmax_val = float(np.linalg.norm(forces, axis=1).max()) if len(forces) else float("nan")
        dmin = min_interatomic_distance(atoms)
        epa = energy / natoms if natoms else float("nan")
        cif_path = outdir / f"cand_{idx:03d}_annealed.cif"
        write(cif_path, atoms)
        return CandidateResult(idx, composition, natoms, energy, epa, fmax_val, dmin, atoms.get_volume(), str(cif_path), str(traj_path), False)
    except Exception:
        return create_failed_result(idx, composition, natoms)


def relax_with_temperature(atoms, idx: int, composition: str, device: str, args, outdir: Path):
    if args.temperature == 0.0 or args.relaxation_type == "static":
        return relax_with_mattersim_static(atoms, idx, composition, device, args.fmax, 200, outdir)
    if args.relaxation_type == "md":
        return relax_with_mattersim_temperature(atoms, idx, composition, device, args.temperature, args.timestep, args.md_steps, args.friction, outdir)
    return relax_with_annealing(atoms, idx, composition, device, args.temperature, 0.0, args.annealing_steps, args.timestep, outdir)


def plot_summary(df: pd.DataFrame, outdir: Path):
    plot_dir = outdir / "plots"
    plot_dir.mkdir(exist_ok=True)
    valid_energy = df[df["energy_per_atom_eV"].notna()]
    if len(valid_energy) == 0:
        return
    plt.figure(figsize=(10, 6))
    colors = ["green" if acc else "red" for acc in valid_energy["accepted"]]
    plt.scatter(valid_energy["idx"], valid_energy["energy_per_atom_eV"], c=colors)
    plt.xlabel("Candidate")
    plt.ylabel("Energy per atom (eV)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(plot_dir / "summary_plots.png", dpi=150, bbox_inches="tight")
    plt.close()


def write_qe_input(atoms, path: Path):
    pseudopotentials = {
        "C": "C.pbe-n-kjpaw_psl.1.0.0.UPF",
        "Si": "Si.pbe-n-kjpaw_psl.1.0.0.UPF",
        "Cu": "Cu.pbe-dn-kjpaw_psl.1.0.0.UPF",
        "Mo": "Mo.pbe-n-kjpaw_psl.1.0.0.UPF",
        "S": "S.pbe-n-kjpaw_psl.1.0.0.UPF",
    }
    try:
        write(path, atoms, format="espresso-in", pseudopotentials=pseudopotentials, tprnfor=True, tstress=True, kpts=(4, 4, 4))
    except Exception:
        return False
    return True


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    input_dir = Path(args.input_dir)
    cif_files = sorted(input_dir.glob("*.cif"))
    if not cif_files:
        raise FileNotFoundError(f"No CIF files found in {input_dir}")

    device = pick_device(args.device)
    results = []
    for idx, cif_file in enumerate(cif_files):
        atoms = read(cif_file)
        if not verify_composition(atoms, args.composition):
            continue
        result = relax_with_temperature(atoms, idx, args.composition, device, args, outdir)
        result.accepted = (
            not np.isnan(result.energy_per_atom_eV)
            and not np.isnan(result.fmax_eV_per_A)
            and not np.isnan(result.dmin_A)
            and result.fmax_eV_per_A <= args.fmax
            and result.dmin_A >= args.dmin
        )
        results.append(result)

    if not results:
        raise RuntimeError("No valid structures after composition filtering/relaxation")

    df = pd.DataFrame([asdict(r) for r in results])
    summary_csv = outdir / "candidates_summary.csv"
    df.to_csv(summary_csv, index=False)
    plot_summary(df, outdir)

    accepted = [r for r in results if r.accepted]
    if not accepted:
        accepted = [r for r in results if not np.isnan(r.energy_per_atom_eV)]
    accepted.sort(key=lambda r: r.energy_per_atom_eV)

    qe_dir = outdir / "qe_inputs"
    qe_dir.mkdir(exist_ok=True)
    qe_paths = []
    for r in accepted[: args.top_k]:
        if not r.cif_path:
            continue
        atoms = read(r.cif_path)
        qe_input_path = qe_dir / f"cand_{r.idx:03d}_scf.in"
        if write_qe_input(atoms, qe_input_path):
            qe_paths.append(str(qe_input_path))

    # Compatibility artifact for downstream scripts expecting a single relaxed.cif
    if accepted and accepted[0].cif_path:
        shutil.copyfile(accepted[0].cif_path, outdir / "relaxed.cif")

    manifest = {
        "input_dir": str(input_dir),
        "composition": args.composition,
        "device": device,
        "total_results": len(results),
        "accepted_results": len([r for r in results if r.accepted]),
        "summary_csv": str(summary_csv),
        "qe_inputs": qe_paths,
        "top_candidates": [asdict(r) for r in accepted[: args.top_k]],
    }
    manifest_path = outdir / "relaxation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=_json_default), encoding="utf-8")
    print(f"[Relaxation] Done. Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
