import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Optional

from config import OUTPUT_DIR
from downloader import download_video
from merger import cleanup_files, merge_video_subtitles
from translator import transcribe_video

logger = logging.getLogger("pipeline")


class PipelineError(Exception):
    pass


class DownloadPipelineError(PipelineError):
    pass


async def process_video(
    url: str,
    translate_to_ru: bool,
    merge: bool,
    progress_coro: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Path:
    async def _noop(_message: str) -> None:
        pass

    progress = progress_coro if progress_coro is not None else _noop

    try:
        await progress("⬇️ Скачивание видео...")
        video_path, title_or_error = await download_video(url, progress)
        if video_path is None:
            raise DownloadPipelineError(title_or_error or "Не удалось скачать видео")
        srt_path = await transcribe_video(
            video_path,
            source_lang="en",
            progress_coro=progress,
            translate_to_ru=translate_to_ru,
        )
        if merge:
            final_path = await merge_video_subtitles(
                video_path,
                srt_path,
                translate_to_ru=translate_to_ru,
                progress_coro=progress,
            )
            cleanup_files(video_path, srt_path)
        else:
            final_path = OUTPUT_DIR / srt_path.name
            shutil.move(str(srt_path), str(final_path))
            cleanup_files(video_path)
        await progress("✅ Обработка завершена")
        return final_path
    except DownloadPipelineError:
        raise
    except Exception as exc:
        logger.exception("Ошибка обработки %s", url)
        raise PipelineError(str(exc)[:500]) from exc