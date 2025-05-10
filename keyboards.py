from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


async def query_btn(url: str):
    return InlineKeyboardButton(text="𖦹⋆｡˚⋆ฺ link", url=url)


async def auto_btn(uuid: str):
    return InlineKeyboardButton(
        text="･ﾟ༝✧* auto *･༓☾", callback_data=f"download:{uuid}:auto"
    )


async def audio_btn(uuid: str):
    return InlineKeyboardButton(
        text="‧₊˚♪ audio 𝄞₊˚⊹", callback_data=f"download:{uuid}:audio"
    )


async def start_bot_btn(bot_username: str):
    return InlineKeyboardButton(
        text="ﾟ+..｡*ﾟ+ /start", url=f"https://t.me/{bot_username}?start=from_inline"
    )


async def get_download_keyboard(uuid: str, query: str):
    """Function to create article keyboard with additional URL button"""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [await auto_btn(uuid), await audio_btn(uuid)],
            [await query_btn(query)],
        ]
    )


async def get_query_keyboard(url: str):
    """Function to create article keyboard with additional URL button"""

    return InlineKeyboardMarkup(inline_keyboard=[[await query_btn(url)]])


async def get_unopened_dms_keyboard(uuid: str, url: str, bot_username: str):
    """Function to create keyboard for inline results with message bot button"""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [await auto_btn(uuid), await audio_btn(uuid)],
            [await query_btn(url)],
            [await start_bot_btn(bot_username)],
        ]
    )


async def get_open_bot_keyboard(bot_username: str):
    """Create keyboard with an 'Open Bot' button"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Open Bot", url=f"https://t.me/{bot_username}"
                )
            ]
        ]
    )


async def get_processing_keyboard(url: str):
    """Create a keyboard with a processing message"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="processing ⋆˚｡⋆୨୧˚", callback_data="processing"
                ),
            ],
            [
                await query_btn(url),
            ],
        ]
    )
