import asyncio
import re
import time
import traceback
import uuid
from typing import Dict, List, Tuple, Union

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineQueryResultCachedPhoto, InputMedia

from keyboards import (
    get_download_keyboard,
    get_error_keyboard,
    get_open_bot_keyboard,
    get_permission_required_keyboard,
    get_processing_keyboard,
    get_query_keyboard,
)
from logger import logger
from media_fetcher import MediaFetcher, parse_media_response
from models import InlineQueryInfo

router = Router()


class BotHandler:
    def __init__(self):
        self.bot_username = None
        self.download_image_file_id = "AgACAgQAAyEGAASdI2ANAAMCaB8gSXN6qtwdsZDHpb4clsRPOa8AAgi5MRu85P1Q8JJsMOZm9k0BAAMCAAN3AAM2BA"

        self.query_info: Dict[str, InlineQueryInfo] = {}
        self.query_timestamps = []  # List of UUIDs in chronological order (oldest first)
        self.MAX_QUERIES = 100  # Maximum number of queries to keep in memory
        self.active_messages = {}

        # Timeout settings - 10 seconds for auto-download
        self.download_timeout = 10  # seconds
        self.timeout_tasks = {}  # To track timeout tasks by query_uuid

        self.fetch = MediaFetcher().fetch

    async def add_query(self, query_uuid: str, query_info: InlineQueryInfo) -> None:
        """Add a query to the tracking system, removing oldest if at capacity"""
        # Add the new query
        self.query_info[query_uuid] = query_info
        self.query_timestamps.append(query_uuid)

        # If we're over capacity, remove the oldest
        while len(self.query_timestamps) > self.MAX_QUERIES:
            oldest_uuid = self.query_timestamps.pop(0)  # Remove and get oldest UUID
            if oldest_uuid in self.query_info:
                await self.cancel_timeout_task(oldest_uuid)
                del self.query_info[oldest_uuid]
                logger.info(
                    f"Removed oldest query {oldest_uuid} to maintain size limit"
                )

    async def remove_query(self, query_uuid: str) -> None:
        """Remove a query from the tracking system"""
        if query_uuid in self.query_info:
            del self.query_info[query_uuid]

        # Also remove from timestamps list
        if query_uuid in self.query_timestamps:
            self.query_timestamps.remove(query_uuid)

    async def check_expired_messages_task(self, bot: Bot):
        """Function to periodically log query stats and verify consistency"""
        while True:
            try:
                # Just log stats periodically
                total_entries = len(self.query_info)
                if total_entries > 0:
                    logger.debug(
                        f"Currently tracking {total_entries} queries in memory"
                    )

                # Verify consistency between query_info and query_timestamps (just in case)
                if len(self.query_info) != len(self.query_timestamps):
                    logger.warning(
                        f"Query tracking inconsistency: {len(self.query_info)} items in query_info but {len(self.query_timestamps)} in timestamps"
                    )

                    # Fix any inconsistency by rebuilding timestamps from query_info
                    for uuid_key in list(self.query_timestamps):
                        if uuid_key not in self.query_info:
                            self.query_timestamps.remove(uuid_key)

                    for uuid_key in self.query_info.keys():
                        if uuid_key not in self.query_timestamps:
                            self.query_timestamps.append(uuid_key)

            except Exception as e:
                logger.error(f"Error in query tracking monitor: {e}")

            # Check much less frequently since this is just for monitoring now
            await asyncio.sleep(60)  # Check once a minute

    async def register_handlers(self):
        """Register all handlers with the router"""

        router.inline_query.register(self.process_inline_query)

        router.message.register(self.cmd_start, Command("start"))
        router.callback_query.register(
            self.process_callback, F.data.startswith("download:")
        )

        # Add handler for permission_info button
        router.callback_query.register(
            self.process_permission_info, F.data == "permission_info"
        )

        # Add handler for try_again button
        router.callback_query.register(
            self.process_try_again_callback, F.data.startswith("try_again:")
        )

        # We need to customize this handler to pass the bot
        @router.chosen_inline_result()
        async def chosen_inline_result_handler(
            chosen_result: types.ChosenInlineResult, bot: Bot
        ):
            await self.process_chosen_inline_result(chosen_result, bot)

        # Register handler for all text messages that may contain URLs
        router.message.register(self.handle_incoming_message, F.text)

        return router

    async def process_callback(self, callback: types.CallbackQuery, bot: Bot):
        """Process callback queries from both inline queries and direct messages"""
        try:
            callback_data = callback.data.split(":")
            if len(callback_data) < 3:
                return await callback.answer("Invalid callback data")

            await callback.answer("ฅ^>⩊<^ ฅ")
            download_uuid = callback_data[1]
            download_type = callback_data[2]

            # Cancel the timeout task if it exists
            await self.cancel_timeout_task(download_uuid)
            logger.info(f"Processing user-initiated callback for {download_uuid}")

            await self.process_download_callback(
                callback, bot, download_uuid, download_type
            )

            # Remove query info after processing to avoid reuse
            if download_uuid in self.query_info:
                await self.remove_query(download_uuid)

        except Exception as e:
            logger.error(f"Error processing callback: {e}")

    async def cmd_start(self, message: types.Message, command: Command, bot: Bot):
        """Command handler for /start"""
        bot_info = await bot.get_me()
        self.bot_username = bot_info.username

        # Check if there's a parameter (from switch_pm)
        start_param = command.args

        if start_param and start_param.startswith("help"):
            return

        # Check if this is a download deep link
        if start_param and start_param.startswith("download_"):
            try:
                # Parse download_uuid and download_type from the deep link parameter
                # Format: "download_UUID_TYPE"
                parts = start_param.split("_")
                if len(parts) >= 3:
                    download_uuid = parts[1]
                    download_type = parts[2]

                    # Check if this download still exists in our tracking
                    if download_uuid in self.query_info:
                        logger.info(
                            f"Auto-triggering download from /start deep link: {download_uuid} ({download_type})"
                        )

                        # Let the user know we're handling their request
                        await message.answer(
                            "✨ I'll try to download that media for you now! ✨",
                            disable_notification=True,
                        )

                        # Create a callback query to simulate a download button press
                        callback = types.CallbackQuery(
                            id=str(uuid.uuid4()),
                            from_user=message.from_user,
                            chat_instance=str(uuid.uuid4()),
                            message=None,
                            data=f"download:{download_uuid}:{download_type}",
                        )

                        # If this is from an inline query, we need to simulate an inline message
                        if self.query_info[download_uuid].inline:
                            # For inline queries, we'll need to get the user to click the button again
                            await message.answer(
                                "🔄 Please go back to the chat where you used the bot and click the 'Try Again' button.",
                                disable_notification=True,
                            )
                        else:
                            # For direct messages, we can simulate the callback directly
                            # Process the callback (this will send the media)
                            await self.process_callback(callback, bot)
                            return
                    else:
                        logger.info(
                            f"Download from deep link failed - UUID not found: {download_uuid}"
                        )
                        # Just show the welcome message
            except Exception as e:
                logger.error(f"Error processing download deep link: {e}")
                # Fall through to normal welcome message

        text = (
            f"👋 Welcome to the bot @{bot_info.username}!\n\n"
            f"You can use this bot to download media from supported sites.\n\n"
            f"• Send a link directly to download media\n"
            f"• Use @{bot_info.username} in any chat to share media inline"
        )
        await message.answer(text)

    async def send_media_to_dm(
        self,
        user_id: int,
        result: Union[List[InputMedia], InputMedia],
        bot: Bot,
        keyboard=None,
    ) -> Tuple[bool, Union[str, None]]:
        """Send media files to the user's DM

        Args:
            user_id: The user ID to send media to
            result: The media result(s) to send
            bot: The bot instance
            keyboard: Optional keyboard to attach to media

        Returns:
            Tuple[bool, Union[str, None]]: (Success status, First media file_id if available)
        """
        try:
            first_file_id = None

            # Ensure result is a list
            media_list = result if isinstance(result, list) else [result]

            # Send media files with notifications disabled
            if len(media_list) > 1:
                # Send media group (multiple files)
                chunks_sent = 0
                for i in range(0, len(media_list), 10):
                    chunk = media_list[i : i + 10]
                    # We can't add keyboards to media groups directly
                    sent_media = await bot.send_media_group(
                        chat_id=user_id,
                        media=chunk,
                        disable_notification=True,  # Disable notifications
                    )
                    chunks_sent += 1

                    if i == 0 and sent_media and len(sent_media) > 0:
                        # Save the file_id from the first media item
                        first_msg = sent_media[0]
                        if hasattr(first_msg, "photo") and first_msg.photo:
                            first_file_id = first_msg.photo[-1].file_id
                        elif hasattr(first_msg, "video") and first_msg.video:
                            first_file_id = first_msg.video.file_id
                        elif hasattr(first_msg, "audio") and first_msg.audio:
                            first_file_id = first_msg.audio.file_id
                        elif hasattr(first_msg, "document") and first_msg.document:
                            first_file_id = first_msg.document.file_id

            elif len(media_list) == 1:
                # If there's only one item in the list, send it directly
                single_media = media_list[0]
                if hasattr(single_media, "media"):
                    if single_media.type == "photo":
                        sent_msg = await bot.send_photo(
                            chat_id=user_id,
                            photo=single_media.media,
                            reply_markup=keyboard,
                            disable_notification=True,  # Disable notifications
                        )
                        if hasattr(sent_msg, "photo") and sent_msg.photo:
                            first_file_id = sent_msg.photo[-1].file_id
                    elif single_media.type == "video":
                        sent_msg = await bot.send_video(
                            chat_id=user_id,
                            video=single_media.media,
                            reply_markup=keyboard,
                            disable_notification=True,  # Disable notifications
                        )
                        if hasattr(sent_msg, "video") and sent_msg.video:
                            first_file_id = sent_msg.video.file_id
                    elif single_media.type == "audio":
                        sent_msg = await bot.send_audio(
                            chat_id=user_id,
                            audio=single_media.media,
                            reply_markup=keyboard,
                            disable_notification=True,  # Disable notifications
                        )
                        if hasattr(sent_msg, "audio") and sent_msg.audio:
                            first_file_id = sent_msg.audio.file_id
                    else:
                        sent_msg = await bot.send_document(
                            chat_id=user_id,
                            document=single_media.media,
                            reply_markup=keyboard,
                            disable_notification=True,  # Disable notifications
                        )
                        if hasattr(sent_msg, "document") and sent_msg.document:
                            first_file_id = sent_msg.document.file_id

            logger.info(f"Successfully sent media to user {user_id}")
            return True, first_file_id

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error sending media to user {user_id}: {e}")

            # Check if it's a permission error (user blocked bot or didn't start it)
            if "Forbidden: bot was blocked by the user" in str(
                e
            ) or "bot can't initiate conversation" in str(e):
                logger.warning(f"User {user_id} has not started the bot or blocked it")
                return False, True

            return False, None

    async def process_download_callback(
        self,
        callback: types.CallbackQuery,
        bot: Bot,
        download_uuid: str,
        download_type: str,
        automatic_download: bool = False,
    ) -> bool | types.Message | None:
        """Callback query handler for both inline queries and direct messages"""

        if download_uuid not in self.query_info:
            return await callback.answer("𖦹 Expired, do a new query ₊✧⋆⭒˚｡⋆")

        user = callback.from_user
        from_user_id = self.query_info[download_uuid].from_user_id
        url = self.query_info[download_uuid].query
        is_inline = self.query_info[download_uuid].inline

        query_keyboard = await get_query_keyboard(url)
        open_bot_keyboard = await get_open_bot_keyboard(self.bot_username)

        if callback.from_user.id != from_user_id:
            return await callback.answer("𖦹 This is not your message ₊✧⋆⭒˚｡⋆")

        try:
            # Cancel the timeout task if it exists (this wasn't previously done for automatic downloads)
            if not automatic_download:
                await self.cancel_timeout_task(download_uuid)

            logger.info(
                f"Processing {'auto' if automatic_download else 'user-initiated'} callback for {download_uuid}"
            )

            # For inline queries, check if we can send DMs
            if is_inline:
                try:
                    # Test if we can send DM to the user
                    message = await bot.send_message(
                        callback.from_user.id, "(｡•̀ᴗ-)✧", disable_notification=True
                    )
                    await bot.delete_message(callback.from_user.id, message.message_id)

                except Exception as e:
                    try:
                        logger.error(f"Failed to send and delete dummy message: {e}")

                        # Cancel the timeout task for this download since it won't work
                        await self.cancel_timeout_task(download_uuid)

                        # Update the message with the permission required keyboard
                        await bot.edit_message_reply_markup(
                            inline_message_id=callback.inline_message_id,
                            reply_markup=await get_permission_required_keyboard(
                                url, self.bot_username, download_uuid
                            ),
                        )

                        # Clean up query_info since we won't proceed with download
                        if download_uuid in self.query_info:
                            await self.remove_query(download_uuid)
                            logger.debug(
                                f"Cleaned up query_info for {download_uuid} after permission error"
                            )
                        try:
                            await callback.answer(
                                "Start the bot and try again",
                                show_alert=True,
                            )
                        except Exception:
                            pass

                        return False
                    except Exception as reply_error:
                        logger.error(
                            f"Failed to send permission required message: {reply_error}"
                        )
                        return False

            processing_keyboard = await get_processing_keyboard(url)
            if is_inline:
                await bot.edit_message_reply_markup(
                    inline_message_id=callback.inline_message_id,
                    reply_markup=processing_keyboard,
                )
            else:
                await bot.edit_message_reply_markup(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    reply_markup=processing_keyboard,
                )

            # Fetch the results
            if download_type == "auto":
                audio = False
            elif download_type == "audio":
                audio = True

            response = await self.fetch(url, audio=audio)
            logger.debug(f"Response: {response}")

            result = await parse_media_response(response)

            # Clean up query_info as soon as we've fetched and processed the response
            # This ensures we don't leave entries in memory unnecessarily
            query_info_cleaned = False
            if download_uuid in self.query_info:
                await self.remove_query(download_uuid)
                query_info_cleaned = True
                logger.debug(
                    f"Cleaned up query_info for {download_uuid} after processing"
                )

            # Handle error case
            if not result.success:
                error_message = f"⚠️ Error: {result.error_message}"
                if is_inline:
                    await bot.edit_message_text(
                        inline_message_id=callback.inline_message_id,
                        text=error_message,
                        reply_markup=query_keyboard,
                    )
                else:
                    await bot.edit_message_caption(
                        chat_id=callback.message.chat.id,
                        message_id=callback.message.message_id,
                        caption=error_message,
                        reply_markup=query_keyboard,
                    )
            elif result.success:
                media_list = result.media_items

                # Check if we had partial success (some items failed)
                partial_success = result.has_errors and result.success_count > 0

                # Send media to DM regardless of whether it's inline or not
                # Pass the query keyboard to attach it to the media in DMs
                dm_sent, first_file_id = await self.send_media_to_dm(
                    user.id, media_list, bot, query_keyboard
                )

                # If we couldn't send the DM (perhaps user hasn't started the bot)
                if not dm_sent:
                    if first_file_id:
                        if is_inline:
                            # For inline messages, show a permission required message
                            await bot.edit_message_reply_markup(
                                inline_message_id=callback.inline_message_id,
                                reply_markup=await get_permission_required_keyboard(
                                    url, self.bot_username, download_uuid
                                ),
                            )
                        else:
                            # For direct messages, show a permission required message
                            await bot.edit_message_reply_markup(
                                chat_id=callback.message.chat.id,
                                message_id=callback.message.message_id,
                                reply_markup=await get_permission_required_keyboard(
                                    url, self.bot_username, download_uuid
                                ),
                            )
                        return True
                    else:
                        if is_inline:
                            await bot.edit_message_reply_markup(
                                inline_message_id=callback.inline_message_id,
                                reply_markup=await get_error_keyboard(url),
                            )
                        else:
                            await bot.edit_message_reply_markup(
                                chat_id=callback.message.chat.id,
                                message_id=callback.message.message_id,
                                reply_markup=await get_error_keyboard(url),
                            )
                        return True

                # Number of media files for the success message
                media_count = result.success_count
                is_multi_media = media_count > 1

                # Update the original message with the first media (where possible)
                await self.update_original_message(
                    callback=callback,
                    bot=bot,
                    is_inline=is_inline,
                    media_list=media_list,
                    first_file_id=first_file_id,
                    media_count=media_count,
                    is_multi_media=is_multi_media,
                    dm_sent=dm_sent,
                    query_keyboard=query_keyboard,
                    open_bot_keyboard=open_bot_keyboard,
                    partial_success=partial_success,
                    error_count=result.error_count,
                    total_count=result.total_count,
                )

            return True

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error processing callback: {e}")
            try:
                # Provide a fallback message in case of any error
                error_message = (
                    "An error occurred while processing your request. Please try again."
                )
                if is_inline:
                    await bot.edit_message_reply_markup(
                        inline_message_id=callback.inline_message_id,
                        reply_markup=await get_error_keyboard(url),
                    )
                else:
                    await bot.edit_message_reply_markup(
                        chat_id=callback.message.chat.id,
                        message_id=callback.message.message_id,
                        reply_markup=await get_error_keyboard(url),
                    )
            except Exception as fallback_error:
                logger.error(f"Failed to send fallback message: {fallback_error}")

            # Make sure to clean up query_info even on error
            if download_uuid in self.query_info and not query_info_cleaned:
                await self.remove_query(download_uuid)
                logger.debug(f"Cleaned up query_info for {download_uuid} after error")

            await callback.answer("Failed to process media. Please try again.")
            return False

        finally:
            if not automatic_download:
                logger.info(f"Callback processed: {download_type} by user {user.id}")

    async def update_original_message(
        self,
        callback: types.CallbackQuery,
        bot: Bot,
        is_inline: bool,
        media_list: list,
        first_file_id: str,
        media_count: int,
        is_multi_media: bool,
        dm_sent: bool,
        query_keyboard,
        open_bot_keyboard,
        partial_success: bool = False,
        error_count: int = 0,
        total_count: int = 0,
    ):
        """Helper function to update the original message with media or status"""
        try:
            if media_count > 0:
                # Create media object from first_file_id or media_list[0]
                media = await self.create_media_object(media_list, first_file_id)

                if is_inline:
                    # Update inline message
                    try:
                        await bot.edit_message_media(
                            inline_message_id=callback.inline_message_id,
                            media=media,
                            reply_markup=(
                                open_bot_keyboard if is_multi_media else query_keyboard
                            ),
                        )

                        # Add caption for multiple media files
                        if is_multi_media and dm_sent:
                            caption_text = (
                                f"Found {media_count} media, sent the rest via DM"
                            )
                            if partial_success:
                                caption_text += (
                                    f" ({error_count} of {total_count} failed)"
                                )

                            await bot.edit_message_caption(
                                inline_message_id=callback.inline_message_id,
                                caption=caption_text,
                                reply_markup=open_bot_keyboard,
                            )
                    except Exception as e:
                        logger.error(f"Error updating inline message media: {e}")
                        # Fallback text message for inline mode
                        success_msg = (
                            f"Found {media_count} media, sent via DM"
                            if is_multi_media
                            else "Media sent to your DM"
                        )

                        if partial_success:
                            success_msg += f" ({error_count} of {total_count} failed)"

                        await bot.edit_message_text(
                            inline_message_id=callback.inline_message_id,
                            text=(
                                success_msg
                                if dm_sent
                                else "⚠️ Failed to send media to your DM."
                            ),
                            reply_markup=(
                                open_bot_keyboard if is_multi_media else query_keyboard
                            ),
                        )
                else:
                    # Update direct message in chat
                    try:
                        await bot.delete_message(
                            chat_id=callback.message.chat.id,
                            message_id=callback.message.message_id,
                        )

                        if media_count > 1:
                            message_text = f"{media_count} media files"
                            if partial_success:
                                message_text += (
                                    f" ({error_count} of {total_count} failed)"
                                )

                            await bot.send_message(
                                chat_id=callback.message.chat.id,
                                text=message_text,
                                reply_markup=query_keyboard,
                                disable_notification=True,
                            )

                    except Exception as e:
                        logger.error(f"Error updating chat message media: {e}")

        except Exception as e:
            logger.error(f"Error in update_original_message: {e}")

    async def create_media_object(self, media_list, file_id=None):
        """Create a media object for updating messages"""
        # If we have a file_id from DM, use it; otherwise use the first media in the list
        if file_id:
            # Determine media type
            first_media = media_list[0]
            first_media_type = (
                first_media.type if hasattr(first_media, "type") else "photo"
            )

            if first_media_type == "photo":
                return types.InputMediaPhoto(media=file_id)
            elif first_media_type == "video":
                return types.InputMediaVideo(media=file_id)
            elif first_media_type == "audio":
                return types.InputMediaAudio(media=file_id)
            else:
                return types.InputMediaDocument(media=file_id)
        else:
            # Fallback to the original media object
            return media_list[0]

    async def process_inline_query(self, inline_query: types.InlineQuery, bot: Bot):
        """Inline query handler"""

        if inline_query.query == "":
            return await inline_query.answer(
                results=[],
                cache_time=1,
                is_personal=True,
                switch_pm_text="Input a link to download",
                switch_pm_parameter="help",
            )

        logger.info(
            f"Received inline query: {inline_query.query} from user {inline_query.from_user.id}"
        )

        url_pattern = r"https?://[^/\s]+/\S+"

        match = re.search(url_pattern, inline_query.query)

        if not match:
            return await inline_query.answer(
                results=[],
                cache_time=1,
                is_personal=True,
                switch_pm_text="Please provide a valid link",
                switch_pm_parameter="help",
            )

        url = match.group(0)

        query_uuid = str(uuid.uuid4())
        query_info = InlineQueryInfo(
            query=url,
            inline=True,
            time_ns=time.time_ns(),
            from_user_id=inline_query.from_user.id,
        )
        await self.add_query(query_uuid, query_info)

        photo = InlineQueryResultCachedPhoto(
            id=query_uuid,
            photo_file_id=self.download_image_file_id,
            title="download",
            description="download",
            parse_mode="Markdown",
            reply_markup=await get_download_keyboard(query_uuid, url),
        )

        logger.info(f"Sending inline query results to user {inline_query.from_user.id}")

        return await inline_query.answer(
            results=[photo],
            cache_time=1,
            is_personal=True,
            # switch_pm_text="Open bot settings",
            # switch_pm_parameter="settings",
        )

    async def process_chosen_inline_result(
        self, chosen_result: types.ChosenInlineResult, bot: Bot
    ):
        """Handle chosen inline results"""

        inline_message_id = chosen_result.inline_message_id
        result_id = chosen_result.result_id  # This should be the query_uuid

        if inline_message_id and result_id in self.query_info:
            # Create timeout task that will wait and then execute
            async def delayed_auto_download():
                await asyncio.sleep(self.download_timeout)
                # Only proceed if the query still exists (hasn't been processed yet)
                if result_id in self.query_info:
                    await self.handle_timeout(
                        query_uuid=result_id,
                        bot=bot,
                        is_inline=True,
                        inline_message_id=inline_message_id,
                    )

            # Start the task
            timeout_task = asyncio.create_task(delayed_auto_download())
            self.timeout_tasks[result_id] = timeout_task

            logger.info(
                f"Tracking inline message {inline_message_id}. Auto-download in {self.download_timeout} seconds if no interaction."
            )

    async def handle_incoming_message(self, message: types.Message, bot: Bot):
        """Handle incoming text messages with URLs

        This method processes direct messages sent to the bot that contain URLs.
        It extracts the URL, creates a query UUID, and sends the download image
        with the same buttons that would appear in inline mode.

        Args:
            message: The incoming message
            bot: The bot instance

        Returns:
            The sent message with download options, or None
        """
        # Extract URLs from the message
        url_pattern = r"https?://[^/\s]+/\S+"
        urls = re.findall(url_pattern, message.text)

        if urls:
            url = urls[0]  # Take the first URL if multiple are found
            logger.info(f"Received message with URL from {message.from_user.id}: {url}")

            # Create a unique ID for this query
            query_uuid = str(uuid.uuid4())

            # Store query info similar to inline queries
            query_info = InlineQueryInfo(
                query=url,
                inline=False,  # This is a direct message, not inline
                time_ns=time.time_ns(),
                from_user_id=message.from_user.id,
            )
            await self.add_query(query_uuid, query_info)

            # Send the same download image with buttons as used in inline queries
            sent_message = await bot.send_photo(
                chat_id=message.chat.id,
                photo=self.download_image_file_id,
                reply_markup=await get_download_keyboard(query_uuid, url),
                disable_notification=True,
            )

            # Create timeout task that will wait and then execute auto download
            async def delayed_auto_download():
                await asyncio.sleep(self.download_timeout)
                # Only proceed if the query still exists (hasn't been processed yet)
                if query_uuid in self.query_info:
                    await self.handle_timeout(
                        query_uuid=query_uuid,
                        bot=bot,
                        is_inline=False,
                        message_id=sent_message.message_id,
                        chat_id=message.chat.id,
                    )

            # Start the task
            timeout_task = asyncio.create_task(delayed_auto_download())
            self.timeout_tasks[query_uuid] = timeout_task

            logger.info(
                f"Sent download options to user {message.from_user.id}. Auto-download in {self.download_timeout} seconds if no interaction."
            )
            return sent_message
        else:
            # For messages without URLs, provide a helpful response
            logger.info(
                f"Received message without URL from {message.from_user.id}: {message.text}"
            )
            await message.answer(
                "Please send a link to download media. "
                "You can also use this bot inline in other chats by typing "
                f"@{self.bot_username} followed by a link."
            )

    async def handle_timeout(
        self,
        query_uuid: str,
        bot: Bot,
        is_inline: bool,
        message_id=None,
        chat_id=None,
        inline_message_id=None,
    ):
        """Handle timeout for download buttons - automatically trigger 'auto' download"""
        try:
            # Check if the query_uuid is still valid (not already processed)
            if query_uuid not in self.query_info:
                logger.info(f"Timeout for {query_uuid}: already processed or expired")
                return

            logger.info(f"Timeout reached for {query_uuid}, auto-triggering download")

            # Get stored information
            user_id = self.query_info[query_uuid].from_user_id

            # Create the appropriate callback query object
            if is_inline:
                # For inline messages
                logger.info(
                    f"Auto-triggering download for inline message {inline_message_id}"
                )
                callback = types.CallbackQuery(
                    id=str(uuid.uuid4()),
                    from_user=types.User(id=user_id, is_bot=False, first_name="User"),
                    chat_instance=str(uuid.uuid4()),
                    message=None,
                    data=f"download:{query_uuid}:auto",
                    inline_message_id=inline_message_id,
                )
            else:
                # For direct messages
                if not message_id or not chat_id:
                    logger.error(
                        "Missing message_id or chat_id for direct message timeout"
                    )
                    # Clean up query_info anyway to avoid memory leaks
                    await self.remove_query(query_uuid)
                    return

                logger.info(
                    f"Auto-triggering download for message {message_id} in chat {chat_id}"
                )
                # Get chat information first to ensure it exists
                try:
                    chat = await bot.get_chat(chat_id)
                    message = types.Message(
                        message_id=message_id,
                        date=int(time.time()),
                        chat=chat,
                        from_user=types.User(
                            id=user_id, is_bot=False, first_name="User"
                        ),
                    )
                    callback = types.CallbackQuery(
                        id=str(uuid.uuid4()),
                        from_user=types.User(
                            id=user_id, is_bot=False, first_name="User"
                        ),
                        chat_instance=str(uuid.uuid4()),
                        message=message,
                        data=f"download:{query_uuid}:auto",
                    )
                except Exception as chat_error:
                    logger.error(f"Failed to get chat for timeout: {chat_error}")
                    # Clean up query_info in case of error
                    await self.remove_query(query_uuid)
                    return

            # Call process_download_callback with the auto-download flag
            await self.process_download_callback(
                callback=callback,
                bot=bot,
                download_uuid=query_uuid,
                download_type="auto",
                automatic_download=True,
            )

            # Note: query_info for this download_uuid is now cleaned up by process_download_callback

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error in handle_timeout: {e}")
            # Attempt to clean up resources even on error
            await self.cancel_timeout_task(query_uuid)
            # Clean up query_info on error to prevent memory leaks
            if query_uuid in self.query_info:
                await self.remove_query(query_uuid)

    async def cancel_timeout_task(self, query_uuid: str):
        """Cancel a timeout task and remove it from tracking"""
        if query_uuid in self.timeout_tasks:
            try:
                self.timeout_tasks[query_uuid].cancel()
                logger.info(f"Cancelled timeout task for {query_uuid}")
            except Exception as e:
                logger.error(f"Error cancelling timeout task: {e}")
            finally:
                self.timeout_tasks.pop(query_uuid, None)

    async def process_permission_info(self, callback: types.CallbackQuery, bot: Bot):
        """Handler for permission_info button callbacks"""
        await callback.answer(
            "The bot needs permission to send you messages. Please start the bot by pressing the /start button.",
            show_alert=True,
        )

    async def process_try_again_callback(self, callback: types.CallbackQuery, bot: Bot):
        """Process try_again button callbacks by simulating a regular download callback"""
        try:
            # Parse the callback data
            callback_parts = callback.data.split(":")
            if len(callback_parts) < 3:
                return await callback.answer("Invalid callback format", show_alert=True)

            uuid = callback_parts[1]
            download_type = callback_parts[2]  # 'auto' or 'audio'

            # Create a mock callback that looks like a download callback
            mock_data = f"download:{uuid}:{download_type}"
            callback.data = mock_data

            # Answer the callback first
            await callback.answer(f"Trying {download_type} download again...")

            # Process it like a regular download callback
            await self.process_callback(callback, bot)

        except Exception as e:
            logger.error(f"Error in process_try_again_callback: {e}")
            await callback.answer(
                "Failed to retry download. Please try again later.", show_alert=True
            )
