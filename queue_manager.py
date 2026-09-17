import asyncio
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from config import DB_PATH
from pipeline import DownloadPipelineError, process_video

logger = logging.getLogger("queue_manager")

MAX_ERROR_LEN = 500


@dataclass
class Job:
    id: int
    url: str
    user_id: int
    translate_to_ru: bool
    merge: bool
    status: str
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    notify_coro: Optional[Callable[["Job"], Awaitable[None]]] = None
    progress_coro: Optional[Callable[[str], Awaitable[None]]] = None


class QueueManager:
    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._running: Optional[Job] = None
        self._default_notify: Optional[Callable[["Job"], Awaitable[None]]] = None
        self._default_progress: Optional[Callable[[str], Awaitable[None]]] = None
        self._init_db()

    def _init_db(self):
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        translate_to_ru INTEGER NOT NULL DEFAULT 0,
                        merge INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,
                        result TEXT,
                        error TEXT,
                        created_at TEXT
                    )
                    """
                )

    @property
    def running_job(self) -> Optional[Job]:
        return self._running

    def set_default_notifiers(self, notify, progress) -> None:
        self._default_notify = notify
        self._default_progress = progress

    @staticmethod
    def _ts() -> str:
        return datetime.now().isoformat(sep=" ", timespec="seconds")

    def submit(
        self,
        url: str,
        user_id: int,
        translate_to_ru: bool,
        merge: bool,
        notify_coro: Callable[["Job"], Awaitable[None]],
        progress_coro: Callable[[str], Awaitable[None]],
    ) -> Job:
        created_at = self._ts()
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO jobs (url, user_id, translate_to_ru, merge, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (url, user_id, int(bool(translate_to_ru)), int(bool(merge)), "queued", created_at),
                )
                job_id = cur.lastrowid
        job = Job(
            id=job_id,
            url=url,
            user_id=user_id,
            translate_to_ru=bool(translate_to_ru),
            merge=bool(merge),
            status="queued",
            created_at=created_at,
            notify_coro=notify_coro or self._default_notify,
            progress_coro=progress_coro or self._default_progress,
        )
        self._wake_worker()
        return job

    def cancel_user_jobs(self, user_id: int) -> int:
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE jobs SET status = ?, error = ? WHERE user_id = ? AND status = ?",
                    ("cancelled", "Отменено пользователем", user_id, "queued"),
                )
                return cur.rowcount

    def _wake_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    def _fetch_queued(self) -> Optional[Job]:
        with self._lock:
            with self._conn:
                row = self._conn.execute(
                    "SELECT id, url, user_id, translate_to_ru, merge, status, result, error, created_at"
                    " FROM jobs WHERE status = ? ORDER BY id ASC LIMIT 1",
                    ("queued",),
                ).fetchone()
        if row is None:
            return None
        return Job(
            id=row[0],
            url=row[1],
            user_id=row[2],
            translate_to_ru=bool(row[3]),
            merge=bool(row[4]),
            status=row[5],
            result=row[6],
            error=row[7],
            created_at=row[8],
            notify_coro=self._default_notify,
            progress_coro=self._default_progress,
        )

    def _mark_status(self, job_id: int, status: str, result: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE jobs SET status = ?, result = ?, error = ? WHERE id = ?",
                    (status, result, error, job_id),
                )

    async def _process_job(self, job: Job) -> str:
        result = await process_video(
            url=job.url,
            translate_to_ru=job.translate_to_ru,
            merge=job.merge,
            progress_coro=job.progress_coro,
        )
        return str(result)

    async def _worker_loop(self) -> None:
        try:
            while True:
                job = self._fetch_queued()
                if job is None:
                    break
                self._running = job
                self._mark_status(job.id, "processing")
                try:
                    result = await self._process_job(job)
                    self._mark_status(job.id, "done", result=str(result), error=None)
                    job.result = str(result)
                    job.status = "done"
                    if job.notify_coro is not None:
                        try:
                            await job.notify_coro(job)
                        except Exception:
                            logger.exception("Ошибка при отправке уведомления о готовности")
                except DownloadPipelineError as exc:
                    msg = str(exc)[:MAX_ERROR_LEN]
                    self._mark_status(job.id, "failed", result=None, error=msg)
                    logger.error("Задача %s не выполнена (скачивание): %s", job.id, msg)
                except Exception as exc:
                    msg = (str(exc)[:MAX_ERROR_LEN]) or "Неизвестная ошибка"
                    self._mark_status(job.id, "failed", result=None, error=msg)
                    logger.exception("Задача %s упала", job.id)
                    if job.progress_coro is not None:
                        try:
                            await job.progress_coro(f"❌ Ошибка: {msg}")
                        except Exception:
                            logger.exception("Ошибка отправки сообщения об ошибке")
                finally:
                    self._running = None
        finally:
            self._worker_task = None