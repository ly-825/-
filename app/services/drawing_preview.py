from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os
import shlex
import shutil
import subprocess

from app.config import settings
from app.models import ProductDrawing


WINDOWS_QCAD_CONVERTER_CANDIDATES = (
    r"C:\Program Files\QCAD\dwg2pdf.bat",
    r"C:\Program Files\QCAD Professional\dwg2pdf.bat",
    r"C:\Program Files\QCADCAM\dwg2pdf.bat",
    r"C:\Program Files (x86)\QCAD\dwg2pdf.bat",
)

MAC_QCAD_CONVERTER_CANDIDATES = (
    "/Applications/QCAD-Pro.app/Contents/Resources/dwg2pdf",
    "/Applications/QCAD.app/Contents/Resources/dwg2pdf",
)


@dataclass
class DrawingPreviewResult:
    status: str
    file_path: str | None = None
    error: str | None = None


def _truncate(value: str, limit: int = 500) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _split_converter_args(value: str) -> list[str]:
    if not value.strip():
        return []
    return shlex.split(value, posix=os.name != "nt")


def _resolve_converter_path() -> str | None:
    configured = (settings.drawing_preview_converter_path or "").strip().strip('"')
    if configured:
        return configured
    for command_name in ("dwg2pdf", "dwg2pdf.bat", "dxf2pdf", "dxf2pdf.bat"):
        found = shutil.which(command_name)
        if found:
            return found
    candidates = WINDOWS_QCAD_CONVERTER_CANDIDATES if os.name == "nt" else MAC_QCAD_CONVERTER_CANDIDATES
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _preview_stem(drawing: ProductDrawing, dxf_path: Path) -> str:
    if drawing.file_hash:
        return drawing.file_hash[:32]
    if dxf_path.exists() and dxf_path.is_file():
        return sha256(dxf_path.read_bytes()).hexdigest()[:32]
    return sha256(str(dxf_path).encode("utf-8")).hexdigest()[:32]


def preview_pdf_path_for_drawing(drawing: ProductDrawing) -> Path:
    dxf_path = Path(drawing.dxf_file_url)
    return Path(settings.drawing_preview_dir) / f"{_preview_stem(drawing, dxf_path)}.pdf"


def _build_converter_command(converter_path: str, output_path: Path, dxf_path: Path) -> list[str]:
    args = _split_converter_args(settings.drawing_preview_converter_args)
    command = [converter_path, *args, "-o", str(output_path), str(dxf_path)]
    if os.name == "nt" and converter_path.lower().endswith((".bat", ".cmd")):
        return ["cmd", "/c", *command]
    return command


def _mark_preview_status(
    drawing: ProductDrawing,
    status: str,
    file_path: Path | None = None,
    error: str | None = None,
) -> DrawingPreviewResult:
    drawing.preview_status = status
    drawing.preview_file_url = str(file_path) if file_path else None
    drawing.preview_error = _truncate(error) if error else None
    return DrawingPreviewResult(
        status=status,
        file_path=str(file_path) if file_path else None,
        error=drawing.preview_error,
    )


def generate_drawing_preview(drawing: ProductDrawing, force: bool = False) -> DrawingPreviewResult:
    dxf_path = Path(drawing.dxf_file_url)
    if not dxf_path.exists() or not dxf_path.is_file():
        return _mark_preview_status(drawing, "failed", error="DXF原始文件不存在，无法生成高清预览")

    output_path = preview_pdf_path_for_drawing(drawing)
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        return _mark_preview_status(drawing, "generated", file_path=output_path)

    converter_path = _resolve_converter_path()
    if not converter_path:
        return _mark_preview_status(drawing, "unconfigured", error="未配置QCAD转换命令，暂时无法生成高清PDF预览")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and force:
        output_path.unlink()

    command = _build_converter_command(converter_path, output_path, dxf_path)
    try:
        result = subprocess.run(
            command,
            cwd=dxf_path.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.drawing_preview_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _mark_preview_status(drawing, "failed", error="QCAD生成高清预览超时")
    except OSError as exc:
        return _mark_preview_status(drawing, "failed", error=f"QCAD转换命令执行失败：{exc}")

    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stderr, result.stdout) if part.strip())
        return _mark_preview_status(drawing, "failed", error=f"QCAD生成高清预览失败：{detail or result.returncode}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        return _mark_preview_status(drawing, "failed", error="QCAD执行完成，但没有生成PDF预览文件")

    return _mark_preview_status(drawing, "generated", file_path=output_path)
