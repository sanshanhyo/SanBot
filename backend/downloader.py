from __future__ import annotations

import logging
import re
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

logger = logging.getLogger(__name__)

ALBUM_ID_RE = re.compile(r"^\d{1,12}$")
ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
COOKIE_LOG_RE = re.compile(
    r"(?i)(Cookie['\"]?\s*:\s*['\"]?)([^'\"\]}]+)|"
    r"(cookies['\"]?\s*:\s*['\"]?)([^'\"\]}]+)|"
    r"(AVS['\"]?\s*:\s*['\"]?)([^'\"\]}]+)"
)


class DownloaderError(Exception):
    """Base error with a message that is safe to show to QQ users."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class AlbumNotFoundError(DownloaderError):
    pass


class PdfGenerationError(DownloaderError):
    pass


class DownloadError(DownloaderError):
    pass


class PreviewError(DownloaderError):
    pass


def sanitize_filename(name: str, fallback: str = "output.pdf", max_length: int = 180) -> str:
    cleaned = ILLEGAL_FILENAME_CHARS_RE.sub("_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > max_length:
        stem = Path(cleaned).stem[: max_length - 4].strip(" .")
        suffix = Path(cleaned).suffix or ".pdf"
        cleaned = f"{stem}{suffix}"
    return cleaned


def _looks_like_missing_album(exc: Exception) -> bool:
    text = str(exc).lower()
    needles = ("404", "not found", "不存在", "无法找到", "不存在该", "album not")
    return any(needle in text for needle in needles)


def _download_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "403" in text or "ip地区禁止访问" in text or "爬虫被识别" in text:
        return "JM 请求被拒绝：IP 地区禁止访问或被识别为爬虫，请检查网络代理或 Cookie"
    if "tls connect error" in text or "openssl" in text:
        return "JM 网络连接失败：TLS 握手失败，请检查网络或代理"
    if "timeout" in text or "timed out" in text:
        return "JM 网络连接超时，请稍后重试"
    return "下载失败，请稍后重试"


def _redact_sensitive_log(text: str) -> str:
    return COOKIE_LOG_RE.sub(lambda m: f"{m.group(1) or m.group(3) or m.group(5)}<redacted>", text)


def _log_captured_jmcomic_output(stdout: StringIO, stderr: StringIO) -> None:
    captured = "\n".join(part for part in (stdout.getvalue(), stderr.getvalue()) if part.strip())
    if captured:
        logger.debug("JMComic output:\n%s", _redact_sensitive_log(captured))


def _ensure_child_path(child: Path, parent: Path) -> Path:
    resolved_child = child.resolve()
    resolved_parent = parent.resolve()
    if not resolved_child.is_relative_to(resolved_parent):
        raise PdfGenerationError("PDF 生成失败：输出路径异常")
    return resolved_child


def _finalize_single_pdf(album_id: str, output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    pdfs = [path for path in output_dir.rglob("*.pdf") if path.is_file()]

    if not pdfs:
        raise PdfGenerationError("PDF 生成失败：未找到输出文件")

    non_empty_pdfs = [path for path in pdfs if path.stat().st_size > 0]
    if len(non_empty_pdfs) != len(pdfs):
        raise PdfGenerationError("PDF 生成失败：输出文件为空")

    if len(non_empty_pdfs) != 1:
        raise PdfGenerationError("PDF 生成失败：输出文件数量异常")

    pdf_path = _ensure_child_path(non_empty_pdfs[0], output_dir)
    prefix = f"[JM{album_id}]"
    current_name = sanitize_filename(pdf_path.name, fallback=f"{prefix}.pdf")

    if f"JM{album_id}" not in current_name.upper():
        title = re.sub(rf"^(?:JM)?{re.escape(album_id)}[\s_\-]*", "", pdf_path.stem, flags=re.I)
        title = sanitize_filename(title, fallback="album", max_length=120)
        current_name = f"{prefix}{title}.pdf"

    final_name = sanitize_filename(current_name, fallback=f"{prefix}.pdf")
    if f"JM{album_id}" not in final_name.upper():
        final_name = f"{prefix}{final_name}"

    final_path = _ensure_child_path(output_dir / final_name, output_dir)
    if pdf_path != final_path:
        if final_path.exists():
            final_path.unlink()
        pdf_path.replace(final_path)

    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise PdfGenerationError("PDF 生成失败：最终文件无效")

    return final_path


def _set_job_download_dir(option: object, images_dir: Path) -> None:
    try:
        option.dir_rule.base_dir = str(images_dir)
    except Exception:
        logger.warning("Could not override jmcomic dir_rule.base_dir; using option file value.")


def download_album_pdf(album_id: str, option_path: str | Path, job_dir: str | Path) -> Path:
    if not ALBUM_ID_RE.fullmatch(album_id):
        raise DownloadError("编号格式错误：只允许 1 到 12 位数字")

    option_file = Path(option_path).expanduser().resolve()
    if not option_file.is_file():
        raise DownloadError("JMComic 配置文件不存在，请检查 JMCOMIC_OPTION_PATH")

    job_path = Path(job_dir).resolve()
    images_dir = job_path / "images"
    output_dir = job_path / "pdf"
    images_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from jmcomic import Feature, create_option_by_file, download_album
    except ImportError as exc:
        raise DownloadError("未安装 jmcomic，请先安装项目依赖") from exc

    try:
        option = create_option_by_file(str(option_file))
        _set_job_download_dir(option, images_dir)
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            download_album(
                album_id,
                option,
                extra=Feature.export_pdf(
                    pdf_dir=str(output_dir),
                    filename_rule="Aid_Atitle",
                    delete_original_file=True,
                ),
            )
        _log_captured_jmcomic_output(stdout, stderr)
    except DownloaderError:
        raise
    except Exception as exc:
        if "stdout" in locals() and "stderr" in locals():
            _log_captured_jmcomic_output(stdout, stderr)
        if _looks_like_missing_album(exc):
            raise AlbumNotFoundError("JM 内容不存在或不可访问") from exc
        raise DownloadError(_download_error_message(exc)) from exc

    return _finalize_single_pdf(album_id, output_dir)


def estimate_download_seconds(page_count: int | None) -> int | None:
    if not page_count or page_count <= 0:
        return None
    return max(60, int(page_count * 2.5))


def format_estimated_time(seconds: int | None) -> str:
    if seconds is None:
        return "预计时间未知，取决于页数和网络"
    minutes = max(1, round(seconds / 60))
    high_minutes = max(minutes + 1, round(minutes * 1.5))
    if minutes == high_minutes:
        return f"预计约 {minutes} 分钟"
    return f"预计约 {minutes}-{high_minutes} 分钟"


def fetch_album_preview(album_id: str, option_path: str | Path) -> dict:
    if not ALBUM_ID_RE.fullmatch(album_id):
        raise PreviewError("编号格式错误：只允许 1 到 12 位数字")

    option_file = Path(option_path).expanduser().resolve()
    if not option_file.is_file():
        raise PreviewError("JMComic 配置文件不存在，请检查 JMCOMIC_OPTION_PATH")

    try:
        from jmcomic import JmcomicText, create_option_by_file
    except ImportError as exc:
        raise PreviewError("未安装 jmcomic，请先安装项目依赖") from exc

    stdout = StringIO()
    stderr = StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            option = create_option_by_file(str(option_file))
            album = option.new_jm_client().get_album_detail(album_id)
        _log_captured_jmcomic_output(stdout, stderr)
    except DownloaderError:
        raise
    except Exception as exc:
        _log_captured_jmcomic_output(stdout, stderr)
        if _looks_like_missing_album(exc):
            raise PreviewError("JM 内容不存在或不可访问") from exc
        raise PreviewError(_download_error_message(exc)) from exc

    page_count = getattr(album, "page_count", None)
    try:
        page_count = int(page_count) if page_count is not None else None
    except (TypeError, ValueError):
        page_count = None

    estimated_seconds = estimate_download_seconds(page_count)
    return {
        "album_id": str(album_id),
        "title": str(getattr(album, "title", None) or getattr(album, "name", None) or f"JM{album_id}"),
        "cover_url": JmcomicText.get_album_cover_url(album_id),
        "page_count": page_count,
        "estimated_seconds": estimated_seconds,
        "estimated_text": format_estimated_time(estimated_seconds),
    }
