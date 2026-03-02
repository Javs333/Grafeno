#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ase.io import read
from ase.visualize.plot import plot_atoms
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError


class JudgeResult(BaseModel):
    likely_stable: bool
    stability_score: float = Field(ge=0.0, le=10.0)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    key_findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommended_next_step: str


class EvaluationArtifact(BaseModel):
    status: str
    model: str | None = None
    input_cif_path: str
    image_path: str
    judge: JudgeResult | None = None
    parse_error: str | None = None
    api_error: str | None = None
    raw_response_text: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render crystal and run Gemini judge")
    parser.add_argument("input_cif_path", help="Path to relaxed CIF file")
    parser.add_argument("--image-format", default="png", choices=["png", "jpg"])
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview"))
    parser.add_argument("--request-timeout", type=int, default=60)
    return parser.parse_args()


def render_structure_image(cif_path: Path, image_path: Path, dpi: int) -> None:
    atoms = read(cif_path)
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111)
    plot_atoms(atoms, ax, radii=0.35, rotation=("20x,35y,0z"))
    ax.set_axis_off()
    fig.tight_layout(pad=0)
    fig.savefig(image_path, dpi=dpi, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def extract_json_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return stripped


def call_gemini(image_path: Path, model: str, timeout: int) -> tuple[str | None, str | None]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "Missing GEMINI_API_KEY"

    prompt = (
        "Analyze the crystal structure image and return JSON only.\n"
        "Be concise and only include the requested fields."
    )
    mime_type = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    client = genai.Client(api_key=api_key)
    _ = timeout
    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=JudgeResult,
                temperature=0.2,
            ),
        )
    except errors.APIError as exc:
        return None, f"Gemini API error: {str(exc)[:500]}"

    if response.parsed is not None:
        parsed = response.parsed
        if isinstance(parsed, JudgeResult):
            return parsed.model_dump_json(), None
        return json.dumps(parsed), None
    if response.text:
        return response.text, None
    return None, "Unexpected Gemini response: missing parsed and text content"


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_cif_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input CIF not found: {input_path}")
    output_dir = input_path.parent
    image_path = output_dir / f"structure.{args.image_format}"
    render_structure_image(input_path, image_path, args.dpi)

    raw_text, api_error = call_gemini(image_path, args.model, args.request_timeout)
    artifact = EvaluationArtifact(
        status="ok" if raw_text else "skipped",
        model=args.model if raw_text else None,
        input_cif_path=str(input_path),
        image_path=str(image_path),
        api_error=api_error,
    )

    if raw_text:
        try:
            parsed = JudgeResult.model_validate_json(extract_json_text(raw_text))
            artifact.judge = parsed
        except (ValidationError, json.JSONDecodeError) as exc:
            artifact.status = "parse_error"
            artifact.parse_error = str(exc)
            artifact.raw_response_text = raw_text

    eval_path = output_dir / "evaluation.json"
    eval_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    print(f"[Evaluation] Saved evaluation to {eval_path}")


if __name__ == "__main__":
    main()
