# Компилирующий файл бота и вызывающий обработчики
import asyncio
import telebot
from telebot.async_telebot import AsyncTeleBot, ExceptionHandler
from django.conf import settings


API_TOKEN = settings.BOT_TOKEN
WEBHOOK_URL = f'{settings.WEBHOOK_BASE_URL}/webhook/'  # Укажите полный URL вашего вебхука


# Инициализация асинхронного бота
if settings.DEBUG:
    import logging #дебаг режим
    logger = telebot.logger
    telebot.logger.setLevel(logging.DEBUG)  # Outputs debug messages to console.
    class MyExceptionHandler(ExceptionHandler):
        async def handle(self, exception):
            logger.error(exception)
    bot = AsyncTeleBot(settings.BOT_TOKEN, exception_handler=MyExceptionHandler())
else:
    bot = AsyncTeleBot(settings.BOT_TOKEN)


#! Подключаем наш register_handlers.py
from .register_handlers import register_handlers
register_handlers(bot)


#! Проверка и установка вебхука
async def ensure_webhook():
    if not settings.WEBHOOK_BASE_URL:
        print("BASE_URL не задан — установка вебхука пропущена.")
        return False
    webhook_info = await bot.get_webhook_info(5)
    # Проверяем, установлен ли вебхук на нужный URL
    if webhook_info.url == WEBHOOK_URL:
        print(f"\nВебхук уже установлен на {WEBHOOK_URL}, ничего не делаем.\n")
    else:
        # Если вебхук отличается или не установлен, удаляем его и создаём новый
        print(f"\n -- Текущий вебхук: {webhook_info.url}. Ожидаемый: {WEBHOOK_URL}. Переустанавливаем...")
        await bot.remove_webhook()
        await asyncio.sleep(1)  # Небольшая пауза для надежности
        await bot.set_webhook(url=WEBHOOK_URL)
        print(f"✅ Новый вебхук установлен на {await bot.get_webhook_info()}\n")
    # Закрытие сессии
    await bot.close_session()
    return True
