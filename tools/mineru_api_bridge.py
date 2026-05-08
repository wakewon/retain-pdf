#!/usr/bin/env python3
"""Expose an installed MinerU runtime as a small RetainPDF-compatible API.

Run this script inside a Python environment that already has MinerU installed
such as a uv-managed project or a conda environment. RetainPDF Docker containers
can then call it through http://host.docker.internal:<port>.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

try:
    from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import FileResponse
except ImportError as exc:  # pragma: no cover - startup dependency guard
    raise SystemExit(
        "mineru-api-bridge requires fastapi and uvicorn in the active Python environment. "
        "Install them in the MinerU environment, for example: pip install fastapi uvicorn python-multipart"
    ) from exc


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18080


@dataclass(frozen=True)
class BridgeConfig:
    configured_python: str
    backend: str
    default_lang: str
    workdir: Path
    timeout_secs: int
    command: str
    command_template: str


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be > 0")
    return value


def load_config(args: argparse.Namespace) -> BridgeConfig:
    workdir = Path(args.workdir or os.environ.get("MINERU_WORKDIR", "") or tempfile.gettempdir())
    return BridgeConfig(
        configured_python=os.environ.get("MINERU_PYTHON", "").strip(),
        backend=args.backend or os.environ.get("MINERU_BACKEND", "pipeline"),
        default_lang=args.lang or os.environ.get("MINERU_LANG", "ch"),
        workdir=workdir.expanduser().resolve(),
        timeout_secs=args.timeout_secs or _env_int("MINERU_TIMEOUT_SECS", 1800),
        command=os.environ.get("MINERU_COMMAND", "").strip(),
        command_template=os.environ.get("MINERU_COMMAND_TEMPLATE", "").strip(),
    )


def bool_form_value(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def safe_stem(filename: str) -> str:
    stem = Path(filename or "upload.pdf").stem.strip()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in stem)
    return safe or "upload"


def create_app(config: BridgeConfig) -> FastAPI:
    app = FastAPI(title="RetainPDF MinerU API Bridge", version="1.0")
    config.workdir.mkdir(parents=True, exist_ok=True)

    @app.get("/health")
    def health() -> dict:
        mineru_importable = False
        mineru_error = None
        if not config.command and not config.command_template:
            try:
                import mineru.cli.common  # noqa: F401
                import mineru.utils.enum_class  # noqa: F401

                mineru_importable = True
            except Exception as exc:  # pragma: no cover - environment-specific
                mineru_error = str(exc)
        else:
            mineru_importable = True

        return {
            "status": "healthy" if mineru_importable else "degraded",
            "mode": "command" if config.command or config.command_template else "python_api",
            "backend": config.backend,
            "lang": config.default_lang,
            "workdir": str(config.workdir),
            "timeout_secs": config.timeout_secs,
            "python_executable": sys.executable,
            "configured_python": config.configured_python or None,
            "mineru_importable": mineru_importable,
            "mineru_error": mineru_error,
            "command": config.command or None,
            "command_template": bool(config.command_template),
        }

    @app.post("/file_parse")
    async def file_parse(
        background_tasks: BackgroundTasks,
        files: Annotated[UploadFile, File()],
        parse_method: Annotated[str, Form()] = "auto",
        lang: Annotated[str, Form()] = "",
        formula_enable: Annotated[str, Form()] = "true",
        table_enable: Annotated[str, Form()] = "true",
        return_md: Annotated[str, Form()] = "true",
        return_middle_json: Annotated[str, Form()] = "true",
        return_model_output: Annotated[str, Form()] = "true",
        return_content_list: Annotated[str, Form()] = "true",
        return_images: Annotated[str, Form()] = "true",
        return_original_file: Annotated[str, Form()] = "false",
        response_format_zip: Annotated[str, Form()] = "true",
    ) -> FileResponse:
        if not bool_form_value(response_format_zip, True):
            raise HTTPException(status_code=400, detail="mineru-api-bridge only supports zip responses")

        stem = safe_stem(files.filename or "upload.pdf")
        run_root = Path(tempfile.mkdtemp(prefix="retainpdf_mineru_", dir=str(config.workdir)))
        source_pdf = run_root / f"{stem}.pdf"
        output_dir = run_root / "out"
        zip_path = run_root / "provider.zip"
        try:
            with source_pdf.open("wb") as writer:
                while True:
                    chunk = await files.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)

            effective_lang = (lang or config.default_lang).strip() or config.default_lang
            started_at = time.time()
            if config.command or config.command_template:
                run_mineru_command(config, source_pdf, output_dir, parse_method, effective_lang)
            else:
                run_mineru_python_api(
                    config=config,
                    source_pdf=source_pdf,
                    output_dir=output_dir,
                    parse_method=parse_method,
                    lang=effective_lang,
                    formula_enable=bool_form_value(formula_enable, True),
                    table_enable=bool_form_value(table_enable, True),
                    dump_md=bool_form_value(return_md, True),
                    dump_middle_json=bool_form_value(return_middle_json, True),
                    dump_model_output=bool_form_value(return_model_output, True),
                    dump_content_list=bool_form_value(return_content_list, True),
                    dump_orig_pdf=bool_form_value(return_original_file, False),
                )
            ensure_required_mineru_outputs(output_dir)
            write_zip(output_dir, zip_path, metadata={
                "bridge_mode": "command" if config.command or config.command_template else "python_api",
                "backend": config.backend,
                "lang": effective_lang,
                "parse_method": parse_method,
                "elapsed_seconds": round(time.time() - started_at, 3),
            })
            background_tasks.add_task(shutil.rmtree, run_root, ignore_errors=True)
            return FileResponse(
                zip_path,
                media_type="application/zip",
                filename="mineru-provider.zip",
                background=background_tasks,
            )
        except HTTPException:
            shutil.rmtree(run_root, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(run_root, ignore_errors=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


def run_mineru_python_api(
    *,
    config: BridgeConfig,
    source_pdf: Path,
    output_dir: Path,
    parse_method: str,
    lang: str,
    formula_enable: bool,
    table_enable: bool,
    dump_md: bool,
    dump_middle_json: bool,
    dump_model_output: bool,
    dump_content_list: bool,
    dump_orig_pdf: bool,
) -> None:
    from mineru.cli.common import do_parse
    from mineru.utils.enum_class import MakeMode

    output_dir.mkdir(parents=True, exist_ok=True)
    do_parse(
        output_dir=str(output_dir),
        pdf_file_names=[source_pdf.stem],
        pdf_bytes_list=[source_pdf.read_bytes()],
        p_lang_list=[lang],
        backend=config.backend,
        parse_method=parse_method or "auto",
        formula_enable=formula_enable,
        table_enable=table_enable,
        f_draw_layout_bbox=False,
        f_draw_span_bbox=False,
        f_dump_md=dump_md,
        f_dump_middle_json=dump_middle_json,
        f_dump_model_output=dump_model_output,
        f_dump_orig_pdf=dump_orig_pdf,
        f_dump_content_list=dump_content_list,
        f_make_md_mode=MakeMode.NLP_MD,
    )


def run_mineru_command(
    config: BridgeConfig,
    source_pdf: Path,
    output_dir: Path,
    parse_method: str,
    lang: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if config.command_template:
        command_text = config.command_template.format(
            input=str(source_pdf),
            output=str(output_dir),
            backend=config.backend,
            parse_method=parse_method or "auto",
            lang=lang,
        )
        command = shlex.split(command_text)
    else:
        command = shlex.split(config.command) + [
            "-p",
            str(source_pdf),
            "-o",
            str(output_dir),
            "-b",
            config.backend,
            "-m",
            parse_method or "auto",
            "-l",
            lang,
        ]
    completed = subprocess.run(
        command,
        cwd=str(output_dir),
        timeout=config.timeout_secs,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        raise RuntimeError(f"MinerU command failed with exit code {completed.returncode}: {detail}")


def ensure_required_mineru_outputs(output_dir: Path) -> None:
    if not output_dir.exists():
        raise RuntimeError(f"MinerU output directory was not created: {output_dir}")
    has_md = any(path.is_file() and path.suffix.lower() == ".md" for path in output_dir.rglob("*"))
    has_layout_json = any(is_layout_json_path(path) for path in output_dir.rglob("*"))
    if not has_md:
        raise RuntimeError(f"MinerU output did not contain Markdown under {output_dir}")
    if not has_layout_json:
        raise RuntimeError(f"MinerU output did not contain layout.json or middle JSON under {output_dir}")


def write_zip(output_dir: Path, zip_path: Path, metadata: dict) -> None:
    import json

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bridge-result.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir).as_posix())


def is_layout_json_path(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name.lower()
    return name in {"layout.json", "middle.json"} or name.endswith("_middle.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a RetainPDF-compatible host MinerU API bridge.")
    parser.add_argument("--host", default=os.environ.get("MINERU_BRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=_env_int("MINERU_BRIDGE_PORT", DEFAULT_PORT))
    parser.add_argument("--backend", default="")
    parser.add_argument("--lang", default="")
    parser.add_argument("--workdir", default="")
    parser.add_argument("--timeout-secs", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args)
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - startup dependency guard
        raise SystemExit("mineru-api-bridge requires uvicorn in the active Python environment.") from exc
    uvicorn.run(create_app(config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
