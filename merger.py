import asyncio
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

import ffmpeg

import config
from config import OUTPUT_DIR

logger = logging.getLogger("merger")


class MergerError(RuntimeError):
    pass


_SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF,"
    "OutlineColour=&H000000,BackColour=&H80000000,Bold=0,"
    "Outline=1.5,Shadow=0.5,MarginV=30,Alignment=2"
)


async def merge_video_subtitles(
    video_path: Path,
    srt_path: Path,
    translate_to_ru: bool = False,
    progress_coro: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Path:
    if progress_coro is not None:
        await progress_coro("⏳ Склейка видео с субтитрами...")
    out_path = OUTPUT_DIR / f"{video_path.stem}_subtitled.mp4"

    def _run_burn() -> None:
        safe_srt = srt_path.with_name(f"subs_{uuid.uuid4().hex}.srt")
        shutil.copyfile(srt_path, safe_srt)
        try:
            vf = f"subtitles={safe_srt.name}:force_style='{_SUBTITLE_STYLE}'"
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vf", vf,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "19",
                "-c:a", "copy",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(out_path),
            ]
            proc = subprocess.run(
                cmd,
                cwd=str(safe_srt.parent),
                capture_output=True,
                text=True,
                errors="replace",
            )
            if proc.returncode != 0:
                detail = (proc.stderr or "").strip().splitlines()
                raise MergerError(detail[-1][:300] if detail else "Ошибка ffmpeg")
        finally:
            safe_srt.unlink(missing_ok=True)

    def _run_mux() -> None:
        try:
            language = "rus" if translate_to_ru else "eng"
            video = ffmpeg.input(str(video_path))
            subs = ffmpeg.input(str(srt_path))
            stream = ffmpeg.output(
                video,
                subs,
                str(out_path),
                **{
                    "c:v": "copy",
                    "c:a": "copy",
                    "c:s": "mov_text",
                    "metadata:s:s:0": f"language={language}",
                },
            )
            ffmpeg.run(stream, overwrite_output=True, quiet=True, capture_stderr=True)
        except ffmpeg.Error as exc:
            detail = exc.stderr.decode("utf-8", "ignore").strip().splitlines()
            raise MergerError(detail[-1][:300] if detail else "Ошибка ffmpeg") from exc

    def _run() -> None:
        if config.BURN_SUBTITLES:
            _run_burn()
        else:
            _run_mux()

    await asyncio.to_thread(_run)
    if progress_coro is not None:
        await progress_coro("✅ Склейка завершена")
    return out_path


def cleanup_files(*paths: Path) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.exception("Не удалось удалить временный файл %s", path)