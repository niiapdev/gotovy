import asyncio
import logging
import re
from pathlib import Path

from vkbottle import Bot, BotTypes

from config import VK_TOKEN
from queue_manager import Job, QueueManager

logger = logging.getLogger("vk_bot_subtitles_ru")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

queue = QueueManager()

bot = Bot(VK_TOKEN)


async def _notify_done(job: Job) -> None:
    try:
        await bot.api.messages.send(peer_id=job.user_id, random_id=0, message=f"✅ Готово! Файл: {job.result}")
    except Exception as exc:
        logger.exception("Ошибка отправки уведомления о готовности: %s", exc)


async def _notify_progress(message: str) -> None:
    job = queue.running_job
    if job is None:
        return
    try:
        await bot.api.messages.send(peer_id=job.user_id, random_id=0, message=message)
    except Exception as exc:
        logger.exception("Ошибка отправки прогресса: %s", exc)


@bot.on.message()
async def message_handler(message: BotTypes.Message) -> None:
    text = (message.text or "").strip()
    user_id = message.peer_id

    if text == "/cancel":
        queue.cancel_user_jobs(user_id)
        await message.answer("❌ Ваши задачи в очереди отменены")
        return

    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        await message.answer("Пришлите ссылку на видео (http/https).")
        return

    queue.submit(
        url=text,
        user_id=user_id,
        translate_to_ru=True,
        merge=True,
        notify_coro=_notify_done,
        progress_coro=_notify_progress,
    )
    await message.answer("✅ Ссылка принята! Встало в очередь!")


if __name__ == "__main__":
    queue.set_default_notifiers(_notify_done, _notify_progress)
    logger.info("Бот запущен")
    asyncio.run(bot.run_polling())