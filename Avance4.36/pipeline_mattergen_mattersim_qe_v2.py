#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import argparse
import sys
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List
import subprocess
import shlex

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from ase.io import write, read
from ase.optimize import FIRE
from ase import units
from ase.md.langevin import Langevin
from ase.md.verlet import VelocityVerlet
from ase.io.trajectory import Trajectory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from mattersim.forcefield import MatterSimCalculator
    MATTERSIM_AVAILABLE = True
    print("MatterSim importado correctamente")
except ImportError as e:
    print(f" Error importando MatterSim: {e}")
    MATTERSIM_AVAILABLE = False

MATTERGEN_AVAILABLE = False
try:
    import mattergen
    MATTERGEN_AVAILABLE = True
    print("MatterGen disponible")
except ImportError as e:
    print(f"Error importando MatterGen: {e}")

# -------------------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Pipeline MatterGen - Con opciones de temperatura")
    
    p.add_argument("--composition", type=str, required=True, help="Composición química (ej. 'Si C')")
    p.add_argument("--model_name", type=str, default="chemical_system", help="Nombre del modelo")
    p.add_argument("--n_candidates", type=int, default=5, help="Número de candidatos a generar")
    p.add_argument("--top_k", type=int, default=3, help="Número de mejores candidatos a exportar")
    p.add_argument("--device", type=str, default="cuda", choices=["cpu", "cuda"], help="Dispositivo")
    p.add_argument("--fmax", type=float, default=0.05, help="Umbral de convergencia en fuerzas (eV/Å)")
    p.add_argument("--dmin", type=float, default=1.0, help="Distancia mínima aceptable (Å)")
    p.add_argument("--outdir", type=str, default="pipeline_output", help="Carpeta de salida")
    p.add_argument("--guidance_factor", type=float, default=2.0, help="Factor de guía de difusión")
    
    # NUEVOS ARGUMENTOS PARA TEMPERATURA
    p.add_argument("--temperature", type=float, default=0.0, 
                   help="Temperatura en Kelvin (0.0 = relajación a T=0)")
    p.add_argument("--relaxation_type", type=str, default="static", 
                   choices=["static", "md", "annealing"],
                   help="Tipo de relajación: static (T=0), md (dinámica molecular), annealing")
    p.add_argument("--md_steps", type=int, default=1000,
                   help="Pasos de dinámica molecular")
    p.add_argument("--timestep", type=float, default=1.0,
                   help="Timestep en femtosegundos para MD")
    p.add_argument("--annealing_steps", type=int, default=500,
                   help="Pasos de annealing")
    p.add_argument("--friction", type=float, default=0.02,
                   help="Coeficiente de fricción para Langevin (solo MD)")
    
    return p.parse_args()

# -------------------------------------------------------------------------
# FUNCIONES AUXILIARES
# -------------------------------------------------------------------------

def pick_device(device: str) -> str:
    if device == "cuda":
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            else:
                print("CUDA no disponible, usando CPU")
                return "cpu"
        except ImportError:
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

def verify_composition(atoms_list, expected_composition):
    expected_elements = set(expected_composition.split())
    valid_structures = []
    for i, atoms in enumerate(atoms_list):
        actual_elements = set(atoms.get_chemical_symbols())
        if actual_elements == expected_elements:
            valid_structures.append(atoms)
            print(f"    Estructura {i}: {len(atoms)} átomos - Composición CORRECTA: {actual_elements}")
        else:
            print(f"    Estructura {i}: Composición INCORRECTA. Esperado: {expected_elements}, Obtenido: {actual_elements}")
    return valid_structures

def maxwell_boltzmann_distribution(atoms, temperature_K):
    """Asigna velocidades según distribución Maxwell-Boltzmann"""
    masses = atoms.get_masses()
    n = len(atoms)
    
    # Velocidades aleatorias
    velocities = np.random.standard_normal((n, 3))
    
    # Escalar para temperatura deseada
    sigma = np.sqrt(units.kB * temperature_K / masses.reshape(n, 1))
    velocities *= sigma
    
    atoms.set_velocities(velocities)

def create_failed_result(idx, composition, natoms):
    """Crea un resultado fallido"""
    return CandidateResult(
        idx=idx, composition=composition, natoms=natoms,
        energy_eV=float('nan'), energy_per_atom_eV=float('nan'),
        fmax_eV_per_A=float('nan'), dmin_A=float('nan'), volume_A3=float('nan'),
        cif_path=Path(""), traj_path=Path(""), accepted=False
    )

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
    cif_path: Path
    traj_path: Path
    accepted: bool


def generate_with_mattergen_corrected(composition: str, n_candidates: int, model_name: str, guidance_factor: float, outdir: Path):
    """Genera estructuras usando el formato de comando que SÍ funciona"""
    import subprocess
    from ase.io import read

    raw_dir = outdir / "raw_structures"
    raw_dir.mkdir(parents=True, exist_ok=True)

    structures = []

    # Formatear composición como en tu comando que funciona
    chemical_system = composition.replace(" ", "-")  # "Si C" -> "Si-C"

    print(f"[*] Generando {n_candidates} estructuras de {chemical_system}...")
    print(f"    Usando modelo: {model_name}, guidance: {guidance_factor}")

    # CONSTRUIR COMANDO EXACTAMENTE COMO TU EJEMPLO QUE FUNCIONA
    cmd = [
        "mattergen-generate",
        str(raw_dir),
        f"--pretrained-name={model_name}",  # FORMATO CORREGIDO: con =
        f"--batch_size={n_candidates}",     # FORMATO CORREGIDO: con =
        "--num_batches", "1",
        f"--properties_to_condition_on=\"{{'chemical_system': '{chemical_system}'}}\"",  # COMILLAS CORRECTAS
        f"--diffusion_guidance_factor={guidance_factor}",
    ]

    print(f"  - Comando: {' '.join(cmd)}")

    try:
        # EJECUTAR CON shell=True PARA MANEJAR CORRECTAMENTE LAS COMILLAS
        result = subprocess.run(
            ' '.join(cmd),
            shell=True,  # IMPORTANTE: permite manejar comillas correctamente
            capture_output=True,
            text=True,
            timeout=900,
        )

        if result.returncode != 0:
            print("Error en generación MatterGen")
            print("STDERR:", result.stderr[:500])
            if result.stdout:
                print("STDOUT:", result.stdout[:500])
            return structures

        print("Generación exitosa")

        # Buscar archivo de resultados
        extxyz_file = raw_dir / "generated_crystals.extxyz"
        if not extxyz_file.exists():
            # Intentar encontrar otros posibles nombres de archivo
            for f in raw_dir.glob("*.extxyz"):
                extxyz_file = f
                break

        if not extxyz_file.exists():
            print(f" No se encontró archivo .extxyz en {raw_dir}")
            # Listar archivos para debug
            for f in raw_dir.iterdir():
                print(f"    📁 {f.name}")
            return structures

        # Leer estructuras
        atoms_list = read(extxyz_file, index=":")
        print(f"    📖 Leyendo {len(atoms_list)} estructuras desde {extxyz_file}")

        if len(atoms_list) > n_candidates:
            atoms_list = atoms_list[:n_candidates]

        for i, at in enumerate(atoms_list):
            elements = set(at.get_chemical_symbols())
            print(f"    Estructura {i}: {len(at)} átomos - Elementos: {elements}")
            structures.append(at)

    except subprocess.TimeoutExpired:
        print("Timeout en generación MatterGen")
    except Exception as e:
        print(f" Error ejecutando MatterGen: {e}")

    return structures

# -------------------------------------------------------------------------
# RELAJACIÓN CON OPCIONES DE TEMPERATURA
# -------------------------------------------------------------------------

def relax_with_temperature(atoms, idx: int, composition: str, device: str, args, outdir: Path):
    """Relajación con opciones de temperatura"""
    
    if args.temperature == 0.0 or args.relaxation_type == "static":
        # Relajación tradicional a T=0
        return relax_with_mattersim_static(atoms, idx, composition, device, args.fmax, 200, outdir)
    
    elif args.relaxation_type == "md":
        # Dinámica Molecular a temperatura constante
        return relax_with_mattersim_temperature(
            atoms, idx, composition, device, 
            args.temperature, args.timestep, args.md_steps, args.friction, outdir
        )
    
    elif args.relaxation_type == "annealing":
        # Annealing desde alta temperatura
        return relax_with_annealing(
            atoms, idx, composition, device,
            initial_temp=args.temperature,
            final_temp=0.0,  # Terminar en 0K
            annealing_steps=args.annealing_steps,
            timestep=args.timestep,
            outdir=outdir
        )

def relax_with_mattersim_static(atoms, idx: int, composition: str, device: str, 
                               fmax: float, max_steps: int, outdir: Path):
    """Relajación estática tradicional (T=0)"""
    natoms = len(atoms)
    
    try:
        calc = MatterSimCalculator(device=device)
        atoms.calc = calc
    except Exception as e:
        print(f"❌ Error inicializando MatterSim: {e}")
        return create_failed_result(idx, composition, natoms)
    
    out_prefix = outdir / f"cand_{idx:03d}_static"
    log_file = out_prefix.with_suffix(".log")
    
    print(f"[*] Relajando candidato {idx} (T=0 K)...")
    
    try:
        opt = FIRE(atoms, logfile=str(log_file))
        opt.run(fmax=fmax, steps=max_steps)

        energy = atoms.get_potential_energy()
        forces = atoms.get_forces()
        fmax_val = float(np.linalg.norm(forces, axis=1).max()) if len(forces) else float("nan")
        dmin = min_interatomic_distance(atoms)
        vol = atoms.get_volume()
        epa = energy / natoms if natoms > 0 else float("nan")

        cif_path = out_prefix.with_suffix(".cif")
        traj_path = out_prefix.with_suffix(".traj")

        write(cif_path, atoms)
        write(traj_path, atoms)

        print(f"    E={energy:.3f} eV, E/atom={epa:.3f} eV, fmax={fmax_val:.3f} eV/Å, dmin={dmin:.2f} Å")

        return CandidateResult(
            idx=idx, composition=composition, natoms=natoms,
            energy_eV=energy, energy_per_atom_eV=epa,
            fmax_eV_per_A=fmax_val, dmin_A=dmin, volume_A3=vol,
            cif_path=cif_path, traj_path=traj_path, accepted=False
        )
        
    except Exception as e:
        print(f"    Error en relajación: {e}")
        return create_failed_result(idx, composition, natoms)

def relax_with_mattersim_temperature(atoms, idx: int, composition: str, device: str, 
                                   temperature: float, timestep: float, steps: int, 
                                   friction: float, outdir: Path):
    """Relaja a temperatura específica usando dinámica molecular"""
    
    try:
        calc = MatterSimCalculator(device=device)
        atoms.calc = calc
    except Exception as e:
        print(f"❌ Error inicializando MatterSim: {e}")
        return create_failed_result(idx, composition, len(atoms))
    
    # Asignar velocidades iniciales según temperatura
    maxwell_boltzmann_distribution(atoms, temperature)
    
    # Crear dinámica molecular con termostato Langevin
    dyn = Langevin(atoms, timestep=timestep * units.fs, 
                   temperature_K=temperature, friction=friction)
    
    # Archivos de salida
    traj_path = outdir / f"cand_{idx:03d}_md.traj"
    log_path = outdir / f"cand_{idx:03d}_md.log"
    
    # Guardar trayectoria
    traj = Trajectory(traj_path, 'w', atoms)
    dyn.attach(traj.write, interval=10)
    
    # Función para imprimir progreso
    def print_status():
        energy = atoms.get_potential_energy()
        temp_instant = atoms.get_kinetic_energy() / (1.5 * units.kB * len(atoms))
        print(f"    Step {dyn.get_number_of_steps():4d}: E={energy:.3f} eV, T={temp_instant:.1f} K")
    
    dyn.attach(print_status, interval=50)
    
    print(f"[*] Ejecutando MD a {temperature} K durante {steps} pasos (timestep={timestep} fs)...")
    
    # Ejecutar dinámica molecular
    dyn.run(steps)
    
    # Calcular propiedades finales
    energy = atoms.get_potential_energy()
    forces = atoms.get_forces()
    fmax_val = float(np.linalg.norm(forces, axis=1).max())
    dmin = min_interatomic_distance(atoms)
    epa = energy / len(atoms)
    
    # Guardar estructura final
    cif_path = outdir / f"cand_{idx:03d}_final.cif"
    write(cif_path, atoms)
    
    print(f"    MD completado: E={energy:.3f} eV, E/atom={epa:.3f} eV, dmin={dmin:.2f} Å")
    
    return CandidateResult(
        idx=idx, composition=composition, natoms=len(atoms),
        energy_eV=energy, energy_per_atom_eV=epa,
        fmax_eV_per_A=fmax_val, dmin_A=dmin, volume_A3=atoms.get_volume(),
        cif_path=cif_path, traj_path=traj_path, accepted=False
    )

def relax_with_annealing(atoms, idx: int, composition: str, device: str,
                        initial_temp: float, final_temp: float, 
                        annealing_steps: int, timestep: float, outdir: Path):
    """Relajación con annealing (enfriamiento gradual)"""
    
    try:
        calc = MatterSimCalculator(device=device)
        atoms.calc = calc
    except Exception as e:
        print(f" Error inicializando MatterSim: {e}")
        return create_failed_result(idx, composition, len(atoms))
    
    # Asignar velocidades iniciales
    maxwell_boltzmann_distribution(atoms, initial_temp)
    
    traj_path = outdir / f"cand_{idx:03d}_anneal.traj"
    log_path = outdir / f"cand_{idx:03d}_anneal.log"
    
    # Annealing gradual
    current_temp = initial_temp
    temp_step = (initial_temp - final_temp) / annealing_steps
    
    print(f"[*] Ejecutando annealing: {initial_temp} K → {final_temp} K en {annealing_steps} pasos...")
    
    # Guardar trayectoria
    traj = Trajectory(traj_path, 'w', atoms)
    
    for step in range(annealing_steps):
        # Ejecutar MD corto a temperatura actual
        dyn = Langevin(atoms, timestep=timestep * units.fs, 
                       temperature_K=current_temp, friction=0.02)
        dyn.run(10)  # 10 pasos de MD por step de annealing
        
        # Guardar frame en trayectoria
        if step % 10 == 0:
            traj.write(atoms)
        
        # Reducir temperatura
        current_temp -= temp_step
        
        # Re-escalar velocidades para nueva temperatura
        if current_temp > 0:
            scale = np.sqrt(max(current_temp, 1) / max(current_temp + temp_step, 1))
            atoms.set_velocities(atoms.get_velocities() * scale)
        
        if step % 50 == 0:
            energy = atoms.get_potential_energy()
            print(f"    Annealing step {step}: T={current_temp:.1f} K, E={energy:.3f} eV")
    
    # Relajación final a T=0 para asegurar mínimo local
    opt = FIRE(atoms)
    opt.run(fmax=0.05, steps=100)
    
    # Calcular propiedades finales
    energy = atoms.get_potential_energy()
    forces = atoms.get_forces()
    fmax_val = float(np.linalg.norm(forces, axis=1).max())
    dmin = min_interatomic_distance(atoms)
    epa = energy / len(atoms)
    
    cif_path = outdir / f"cand_{idx:03d}_annealed.cif"
    write(cif_path, atoms)
    
    print(f"    Annealing completado: E={energy:.3f} eV, E/atom={epa:.3f} eV, dmin={dmin:.2f} Å")
    
    return CandidateResult(
        idx=idx, composition=composition, natoms=len(atoms),
        energy_eV=energy, energy_per_atom_eV=epa,
        fmax_eV_per_A=fmax_val, dmin_A=dmin, volume_A3=atoms.get_volume(),
        cif_path=cif_path, traj_path=traj_path, accepted=False
    )

# -------------------------------------------------------------------------
# GRÁFICAS DE RESUMEN - FUNCIÓN COMPLETA
# -------------------------------------------------------------------------

def plot_summary(df: pd.DataFrame, outdir: Path):
    """Genera gráficas de resumen completas"""
    print("\n[*] Generando gráficas de resumen...")

    plot_dir = outdir / "plots"
    plot_dir.mkdir(exist_ok=True)

    # Crear figura con subplots
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(f'Resumen de Candidatos - {len(df)} estructuras', fontsize=16, fontweight='bold')

    # 1) Energía por átomo vs índice
    ax1 = axes[0, 0]
    valid_energy = df[df['energy_per_atom_eV'].notna()]
    if len(valid_energy) > 0:
        colors = ['green' if acc else 'red' for acc in valid_energy['accepted']]
        ax1.scatter(valid_energy["idx"], valid_energy["energy_per_atom_eV"], c=colors, alpha=0.7, s=60)
        ax1.set_xlabel("Índice del candidato")
        ax1.set_ylabel("Energía por átomo (eV/átomo)")
        ax1.set_title("Energía por átomo")
        ax1.grid(True, alpha=0.3)
        # Añadir leyenda
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor='green', label='Aceptado'),
            Patch(facecolor='red', label='Rechazado')
        ]
        ax1.legend(handles=legend_elements)

    # 2) Fuerza máxima vs Energía
    ax2 = axes[0, 1]
    valid_points = df[df['energy_per_atom_eV'].notna() & df['fmax_eV_per_A'].notna()]
    if len(valid_points) > 0:
        colors = ['green' if acc else 'red' for acc in valid_points['accepted']]
        scatter = ax2.scatter(valid_points["energy_per_atom_eV"], valid_points["fmax_eV_per_A"], 
                             c=colors, alpha=0.7, s=60)
        ax2.set_xlabel("Energía por átomo (eV/átomo)")
        ax2.set_ylabel("Fuerza máxima (eV/Å)")
        ax2.set_title("Fuerza máxima vs Energía")
        ax2.grid(True, alpha=0.3)

    # 3) Histograma de energía
    ax3 = axes[1, 0]
    if len(valid_energy) > 0:
        accepted_energy = valid_energy[valid_energy['accepted']]['energy_per_atom_eV']
        rejected_energy = valid_energy[~valid_energy['accepted']]['energy_per_atom_eV']
        
        if len(accepted_energy) > 0:
            ax3.hist(accepted_energy, bins=8, alpha=0.7, color='green', label='Aceptados')
        if len(rejected_energy) > 0:
            ax3.hist(rejected_energy, bins=8, alpha=0.7, color='red', label='Rechazados')
        
        ax3.set_xlabel("Energía por átomo (eV/átomo)")
        ax3.set_ylabel("Frecuencia")
        ax3.set_title("Distribución de energía")
        ax3.grid(True, alpha=0.3)
        ax3.legend()

    # 4) Distancia mínima interatómica
    ax4 = axes[1, 1]
    valid_dmin = df[df['dmin_A'].notna()]
    if len(valid_dmin) > 0:
        colors = ['green' if acc else 'red' for acc in valid_dmin['accepted']]
        ax4.scatter(valid_dmin["idx"], valid_dmin["dmin_A"], c=colors, alpha=0.7, s=60)
        ax4.axhline(y=1.0, color='orange', linestyle='--', alpha=0.7, label='Límite dmin')
        ax4.set_xlabel("Índice del candidato")
        ax4.set_ylabel("Distancia mínima (Å)")
        ax4.set_title("Distancia mínima interatómica")
        ax4.grid(True, alpha=0.3)
        ax4.legend()

    plt.tight_layout()
    
    # Guardar gráfica
    plot_path = plot_dir / "summary_plots.png"
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f" Gráficas guardadas en: {plot_path}")

    # Gráfica adicional: ranking de mejores candidatos
    if len(valid_energy) > 0:
        plt.figure(figsize=(10, 6))
        sorted_df = valid_energy.sort_values('energy_per_atom_eV')
        colors = ['green' if acc else 'red' for acc in sorted_df['accepted']]
        
        bars = plt.bar(range(len(sorted_df)), sorted_df['energy_per_atom_eV'], color=colors, alpha=0.7)
        plt.xlabel('Ranking de Candidatos')
        plt.ylabel('Energía por átomo (eV/átomo)')
        plt.title('Ranking de Candidatos por Energía')
        plt.grid(True, alpha=0.3)
        
        # Añadir valores en las barras
        for i, (idx, row) in enumerate(sorted_df.iterrows()):
            plt.text(i, row['energy_per_atom_eV'] + 0.01, f'{row["energy_per_atom_eV"]:.3f}', 
                    ha='center', va='bottom', fontsize=8)
        
        plt.tight_layout()
        ranking_path = plot_dir / "energy_ranking.png"
        plt.savefig(ranking_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"Ranking de energías guardado en: {ranking_path}")

# -------------------------------------------------------------------------
# EXPORT A QUANTUM ESPRESSO
# -------------------------------------------------------------------------

def write_qe_input(atoms, path: Path, system_label: str = "candidate"):
    """Exporta input SCF de Quantum ESPRESSO"""
    pseudopotentials = {
        "C": "C.pbe-n-kjpaw_psl.1.0.0.UPF",
        "Si": "Si.pbe-n-kjpaw_psl.1.0.0.UPF",
        "Cu": "Cu.pbe-dn-kjpaw_psl.1.0.0.UPF",
        "Mo": "Mo.pbe-n-kjpaw_psl.1.0.0.UPF",
        "S": "S.pbe-n-kjpaw_psl.1.0.0.UPF",
    }

    # Determinar k-points basado en periodicidad
    pbc = atoms.get_pbc()
    if sum(pbc) == 2:  # 2D material
        kpts = (6, 6, 1)
    elif sum(pbc) == 3:  # 3D material  
        kpts = (4, 4, 4)
    else:  # 0D o 1D
        kpts = (1, 1, 1)

    print(f"[*] Escribiendo input QE: {path.name}")
    
    try:
        write(
            path,
            atoms,
            format="espresso-in",
            pseudopotentials=pseudopotentials,
            tprnfor=True,
            tstress=True,
            kpts=kpts,
        )
        return True
    except Exception as e:
        print(f" Error escribiendo input QE: {e}")
        return False

# -------------------------------------------------------------------------
# MAIN PIPELINE
# -------------------------------------------------------------------------

def main():
    args = parse_args()
    
    if not MATTERGEN_AVAILABLE or not MATTERSIM_AVAILABLE:
        print("Error!!! No se pueden importar las dependencias necesarias")
        return

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    
    device = pick_device(args.device)

    print("========== PIPELINE MATTERGEN - CON TEMPERATURA ==========")
    print(f"Composición      : {args.composition}")
    print(f"Modelo           : {args.model_name}")
    print(f"N candidatos     : {args.n_candidates}")
    print(f"Guidance factor  : {args.guidance_factor}")
    print(f"Temperatura      : {args.temperature} K")
    print(f"Tipo relajación  : {args.relaxation_type}")
    print(f"Device           : {device}")
    print(f"Output dir       : {outdir.resolve()}")
    print("=========================================================")

    # 1) Generar candidatos con el formato CORREGIDO
    structures = generate_with_mattergen_corrected(
        composition=args.composition,
        n_candidates=args.n_candidates,
        model_name=args.model_name,
        guidance_factor=args.guidance_factor,
        outdir=outdir
    )
    
    if not structures:
        print(" Error!!! No se generaron estructuras.")
        return

    print(f"Generadas {len(structures)} estructuras")

    # 2) Verificar composición
    print("\n[*] Verificando composición de las estructuras...")
    structures = verify_composition(structures, args.composition)

    if not structures:
        print("Error!! Ninguna estructura tiene la composición correcta.")
        return

    # 3) Relajar estructuras CON OPCIONES DE TEMPERATURA
    results = []
    for i, atoms in enumerate(structures):
        result = relax_with_temperature(
            atoms=atoms,
            idx=i,
            composition=args.composition,
            device=device,
            args=args,
            outdir=outdir
        )
        results.append(result)

    # 4) Procesar resultados
    for r in results:
        r.accepted = (
            not np.isnan(r.energy_per_atom_eV) and
            not np.isnan(r.fmax_eV_per_A) and 
            not np.isnan(r.dmin_A) and
            r.fmax_eV_per_A <= args.fmax and
            r.dmin_A >= args.dmin
        )

    df = pd.DataFrame([asdict(r) for r in results])
    summary_csv = outdir / "candidates_summary.csv"
    df.to_csv(summary_csv, index=False)
    print(f"[✓] Resumen guardado en: {summary_csv}")

    accepted = [r for r in results if r.accepted]
    if not accepted:
        print("⚠ Ningún candidato pasó los filtros.")
        accepted = [r for r in results if not np.isnan(r.energy_per_atom_eV)]

    accepted.sort(key=lambda r: r.energy_per_atom_eV)

    print("\n MEJORES CANDIDATOS:")
    for i, r in enumerate(accepted[:args.top_k]):
        status = "Aceptado" if r.accepted else "No aceptado"
        print(f"  {i+1:2d}. {status} cand_{r.idx:03d}: E/N = {r.energy_per_atom_eV:.4f} eV/átomo, dmin = {r.dmin_A:.2f} Å")

    # 5) GENERAR GRÁFICAS
    plot_summary(df, outdir)

    # 6) Exportar a QE los mejores candidatos
    qe_dir = outdir / "qe_inputs"
    qe_dir.mkdir(exist_ok=True)
    
    print(f"\n[*] Exportando {len(accepted[:args.top_k])} mejores candidatos a QE...")
    for r in accepted[:args.top_k]:
        try:
            atoms = read(r.cif_path)
            qe_input_path = qe_dir / f"cand_{r.idx:03d}_scf.in"
            success = write_qe_input(atoms, qe_input_path, f"cand_{r.idx:03d}")
            if success:
                print(f"    cand_{r.idx:03d} exportado a QE")
        except Exception as e:
            print(f"     Error exportando cand_{r.idx:03d}: {e}")

    print(f"\n PIPELINE COMPLETADO")
    print(f"   - Resultados en: {outdir.resolve()}")
    print(f"   - Gráficas en: {outdir / 'plots'}")
    print(f"   - Inputs QE en: {qe_dir}")

if __name__ == "__main__":
    main()
