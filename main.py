import os
import asyncio

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher

from bot import BotHandler
from logger import logger

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")


async def main():
    # Create a handler instance and register all handlers
    bot_handler = BotHandler()
    router = await bot_handler.register_handlers()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(router)

    asyncio.create_task(bot_handler.check_expired_messages_task(bot))

    bot_info = await bot.get_me()
    bot_handler.bot_username = bot_info.username
    logger.info(f"Starting bot: @{bot_info.username} ({bot_info.id})")
    logger.info(f"Bot link: https://t.me/{bot_info.username}")
    logger.info("Bot is running...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
