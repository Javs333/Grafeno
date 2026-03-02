#!/usr/bin/env python3
import argparse
import csv
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.storage.blob import BlobServiceClient
import psycopg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload artifacts to Azure Blob and persist metadata to Postgres")
    parser.add_argument("run_dir", help="Pipeline run directory")
    parser.add_argument("--blob-prefix", default="runs", help="Top-level blob prefix")
    parser.add_argument("--skip-blob", action="store_true", help="Skip Azure Blob upload")
    parser.add_argument("--skip-postgres", action="store_true", help="Skip PostgreSQL persistence")
    return parser.parse_args()


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def collect_artifacts(run_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in run_dir.rglob("*"):
        if path.is_file():
            files.append(path)
    return sorted(files)


def upload_to_blob(run_id: str, run_dir: Path, files: list[Path], blob_prefix: str) -> dict[str, Any]:
    connection_string = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    container_name = os.getenv("AZURE_BLOB_CONTAINER")
    if not connection_string or not container_name:
        return {
            "status": "skipped",
            "reason": "Missing AZURE_BLOB_CONNECTION_STRING or AZURE_BLOB_CONTAINER",
            "uploaded": [],
        }

    service = BlobServiceClient.from_connection_string(connection_string)
    container = service.get_container_client(container_name)
    uploaded: list[dict[str, Any]] = []
    prefix = f"{blob_prefix.rstrip('/')}/{run_id}"

    for file_path in files:
        rel = file_path.relative_to(run_dir).as_posix()
        blob_name = f"{prefix}/{rel}"
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as fh:
            container.upload_blob(name=blob_name, data=fh, overwrite=True, content_type=content_type)
        uploaded.append({"local_path": str(file_path), "blob_name": blob_name, "content_type": content_type})

    return {
        "status": "uploaded",
        "container": container_name,
        "prefix": prefix,
        "uploaded_count": len(uploaded),
        "uploaded": uploaded,
    }


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                run_dir TEXT NOT NULL,
                composition TEXT,
                generation_manifest JSONB,
                relaxation_manifest JSONB,
                evaluation JSONB,
                storage_manifest JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_candidates (
                run_id TEXT NOT NULL,
                candidate_idx INTEGER NOT NULL,
                composition TEXT,
                natoms INTEGER,
                energy_eV DOUBLE PRECISION,
                energy_per_atom_eV DOUBLE PRECISION,
                fmax_eV_per_A DOUBLE PRECISION,
                dmin_A DOUBLE PRECISION,
                volume_A3 DOUBLE PRECISION,
                accepted BOOLEAN,
                cif_path TEXT,
                traj_path TEXT,
                PRIMARY KEY (run_id, candidate_idx),
                FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id) ON DELETE CASCADE
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_judgements (
                run_id TEXT NOT NULL,
                candidate_idx INTEGER NOT NULL DEFAULT 0,
                model TEXT,
                status TEXT NOT NULL,
                likely_stable BOOLEAN,
                stability_score DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                summary TEXT,
                key_findings JSONB,
                risks JSONB,
                recommended_next_step TEXT,
                raw_evaluation JSONB,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (run_id, candidate_idx),
                FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id) ON DELETE CASCADE
            );
            """
        )
    conn.commit()


def persist_postgres(
    run_id: str,
    run_dir: Path,
    generation_manifest: dict[str, Any] | None,
    relaxation_manifest: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    storage_manifest: dict[str, Any],
) -> dict[str, Any]:
    dsn = os.getenv("AZURE_POSTGRES_DSN")
    if not dsn:
        return {"status": "skipped", "reason": "Missing AZURE_POSTGRES_DSN"}

    with psycopg.connect(dsn) as conn:
        ensure_schema(conn)
        composition = None
        if generation_manifest:
            composition = generation_manifest.get("composition")
        elif relaxation_manifest:
            composition = relaxation_manifest.get("composition")

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, run_dir, composition, generation_manifest, relaxation_manifest, evaluation, storage_manifest, updated_at
                ) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,NOW())
                ON CONFLICT (run_id) DO UPDATE SET
                    run_dir = EXCLUDED.run_dir,
                    composition = EXCLUDED.composition,
                    generation_manifest = EXCLUDED.generation_manifest,
                    relaxation_manifest = EXCLUDED.relaxation_manifest,
                    evaluation = EXCLUDED.evaluation,
                    storage_manifest = EXCLUDED.storage_manifest,
                    updated_at = NOW();
                """,
                (
                    run_id,
                    str(run_dir),
                    composition,
                    json.dumps(generation_manifest) if generation_manifest is not None else None,
                    json.dumps(relaxation_manifest) if relaxation_manifest is not None else None,
                    json.dumps(evaluation) if evaluation is not None else None,
                    json.dumps(storage_manifest),
                ),
            )

            summary_csv = run_dir / "candidates_summary.csv"
            if summary_csv.exists():
                with summary_csv.open("r", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        idx = int(row["idx"])
                        cur.execute(
                            """
                            INSERT INTO pipeline_candidates (
                                run_id, candidate_idx, composition, natoms, energy_eV, energy_per_atom_eV, fmax_eV_per_A,
                                dmin_A, volume_A3, accepted, cif_path, traj_path
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (run_id, candidate_idx) DO UPDATE SET
                                composition = EXCLUDED.composition,
                                natoms = EXCLUDED.natoms,
                                energy_eV = EXCLUDED.energy_eV,
                                energy_per_atom_eV = EXCLUDED.energy_per_atom_eV,
                                fmax_eV_per_A = EXCLUDED.fmax_eV_per_A,
                                dmin_A = EXCLUDED.dmin_A,
                                volume_A3 = EXCLUDED.volume_A3,
                                accepted = EXCLUDED.accepted,
                                cif_path = EXCLUDED.cif_path,
                                traj_path = EXCLUDED.traj_path;
                            """,
                            (
                                run_id,
                                idx,
                                row.get("composition"),
                                int(row["natoms"]) if row.get("natoms") else None,
                                float(row["energy_eV"]) if row.get("energy_eV") else None,
                                float(row["energy_per_atom_eV"]) if row.get("energy_per_atom_eV") else None,
                                float(row["fmax_eV_per_A"]) if row.get("fmax_eV_per_A") else None,
                                float(row["dmin_A"]) if row.get("dmin_A") else None,
                                float(row["volume_A3"]) if row.get("volume_A3") else None,
                                str(row.get("accepted", "")).lower() == "true",
                                row.get("cif_path"),
                                row.get("traj_path"),
                            ),
                        )

            if evaluation:
                judge = evaluation.get("judge") or {}
                cur.execute(
                    """
                    INSERT INTO pipeline_judgements (
                        run_id, candidate_idx, model, status, likely_stable, stability_score, confidence, summary,
                        key_findings, risks, recommended_next_step, raw_evaluation, updated_at
                    ) VALUES (%s,0,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,NOW())
                    ON CONFLICT (run_id, candidate_idx) DO UPDATE SET
                        model = EXCLUDED.model,
                        status = EXCLUDED.status,
                        likely_stable = EXCLUDED.likely_stable,
                        stability_score = EXCLUDED.stability_score,
                        confidence = EXCLUDED.confidence,
                        summary = EXCLUDED.summary,
                        key_findings = EXCLUDED.key_findings,
                        risks = EXCLUDED.risks,
                        recommended_next_step = EXCLUDED.recommended_next_step,
                        raw_evaluation = EXCLUDED.raw_evaluation,
                        updated_at = NOW();
                    """,
                    (
                        run_id,
                        evaluation.get("model"),
                        evaluation.get("status", "unknown"),
                        judge.get("likely_stable"),
                        judge.get("stability_score"),
                        judge.get("confidence"),
                        judge.get("summary"),
                        json.dumps(judge.get("key_findings", [])),
                        json.dumps(judge.get("risks", [])),
                        judge.get("recommended_next_step"),
                        json.dumps(evaluation),
                    ),
                )
        conn.commit()
    return {"status": "persisted"}


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")
    run_id = run_dir.name
    print(f"[Storage] Processing run: {run_id}")

    generation_manifest = read_json_if_exists(run_dir / "generation_manifest.json")
    relaxation_manifest = read_json_if_exists(run_dir / "relaxation_manifest.json")
    evaluation = read_json_if_exists(run_dir / "evaluation.json")
    files = collect_artifacts(run_dir)

    blob_status = {"status": "skipped", "reason": "--skip-blob provided"}
    if not args.skip_blob:
        blob_status = upload_to_blob(run_id, run_dir, files, args.blob_prefix)

    storage_manifest = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(files),
        "blob": blob_status,
    }
    storage_manifest_path = run_dir / "storage_manifest.json"
    storage_manifest_path.write_text(json.dumps(storage_manifest, indent=2), encoding="utf-8")

    postgres_status = {"status": "skipped", "reason": "--skip-postgres provided"}
    if not args.skip_postgres:
        postgres_status = persist_postgres(
            run_id=run_id,
            run_dir=run_dir,
            generation_manifest=generation_manifest,
            relaxation_manifest=relaxation_manifest,
            evaluation=evaluation,
            storage_manifest=storage_manifest,
        )

    print(f"[Storage] Blob status: {blob_status['status']}")
    print(f"[Storage] Postgres status: {postgres_status['status']}")
    print(f"[Storage] Manifest: {storage_manifest_path}")


if __name__ == "__main__":
    main()
