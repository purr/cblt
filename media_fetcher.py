import traceback

import requests
from aiogram.types import (
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    URLInputFile,
)

from logger import logger
from models import MediaResponse


async def parse_media_response(response: MediaResponse):
    """Parse the media response and return Telegram media objects or error message

    Always returns a list of media items for successful responses, or a string error message for errors.
    """

    if response.status == "error":
        logger.error(f"Media fetcher error: {response.error}")
        if isinstance(response.error, dict):
            error_code = response.error.get("code", "unknown")
            error_context = response.error.get("context", {})

            error_str = f"{error_code}"
            if error_context:
                context_str = ", ".join(f"{k}: {v}" for k, v in error_context.items())
                error_str += f"\nContext: {context_str}"

            return error_str
        return str(response.error)

    try:
        if response.status == "picker" and response.picker:
            media_group = []

            for item in response.picker:
                try:
                    input_file = URLInputFile(item.url)
                    thumbnail = None
                    if item.thumb:
                        try:
                            thumbnail = URLInputFile(item.thumb)
                        except Exception as thumb_error:
                            logger.warning(f"Failed to create thumbnail: {thumb_error}")

                    if item.type == "photo":
                        media = InputMediaPhoto(media=input_file, thumbnail=thumbnail)
                        media_group.append(media)
                    elif item.type == "video":
                        media = InputMediaVideo(media=input_file, thumbnail=thumbnail)
                        media_group.append(media)
                    elif item.type == "gif":
                        media = InputMediaAnimation(
                            media=input_file, thumbnail=thumbnail
                        )
                        media_group.append(media)
                except Exception as item_error:
                    logger.warning(f"Failed to process picker item: {item_error}")

            if len(media_group) > 0:
                logger.info(f"Created media group with {len(media_group)} items")
                return media_group
            else:
                logger.warning("Picker response contained no valid media items")
                return "No valid media found"

        if response.status in ["tunnel", "redirect"]:
            try:
                if response.audio:
                    input_file = URLInputFile(
                        response.audio,
                        filename=response.audioFilename or "audio.mp3",
                    )
                    media = InputMediaAudio(media=input_file)
                    return [media]  # Return as a list with a single item

                if response.url:
                    filename = response.filename or "file"
                    input_file = URLInputFile(response.url, filename=filename)

                    # Use the type field from MediaResponse instead of checking filename extensions
                    if response.type == "video":
                        logger.info("Creating video object based on type")
                        media = InputMediaVideo(media=input_file)
                        return [media]
                    elif response.type == "photo":
                        logger.info("Creating photo object based on type")
                        media = InputMediaPhoto(media=input_file)
                        return [media]
                    elif response.type == "gif":
                        logger.info("Creating gif object based on type")
                        media = InputMediaAnimation(media=input_file)
                        return [media]
                    elif response.type == "audio":
                        logger.info("Creating audio object based on type")
                        media = InputMediaAudio(media=input_file)
                        return [media]
                    else:
                        logger.info("Creating document object based on type")
                        media = InputMediaDocument(
                            media=input_file, disable_content_type_detection=False
                        )
                        return [media]

            except Exception as url_error:
                logger.error(f"Error creating media from URL: {url_error}")
                return "Failed to process media URL"

        logger.warning(f"Unhandled response type: {response.status}")
        return "Unable to process this media type"
    except Exception as e:
        logger.error(f"Error in parse_media_response: {e}")
        return "Error processing media"


class MediaFetcher:
    """Class for fetching media information from API endpoints."""

    def __init__(self):
        self.apis = [
            "https://cobalt.255x.ru",
            "https://co.eepy.today",
            "https://cobalt-7.kwiatekmiki.com/api/json",
            "https://co.otomir23.me",
            "https://cobalt-api.kwiatekmiki.com",
        ]
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }

    async def fetch(self, url: str, audio: bool = False) -> MediaResponse:
        """
        Fetch media data from API endpoints.

        Args:
            url: The URL to fetch data from
            audio: Whether to download as audio

        Returns:
            MediaResponse object or None if all APIs fail
        """
        payload = {
            "url": url,
            "audioBitrate": "320",
            "tiktokFullAudio": True,
            "disableMetadata": False,
            "filenameStyle": "nerdy",
        }

        error_text = None
        if audio:
            payload["downloadMode"] = "audio"

        for api in self.apis:
            try:
                logger.info(f"Trying to fetch from {api}")
                response = requests.post(
                    f"{api}", headers=self.headers, json=payload, timeout=5
                )

                if response.status_code == 200:
                    data = response.json()
                    return MediaResponse.model_validate(data)

                logger.warning(
                    f"Failed to fetch from {api}: Status code {response.status_code} {response.text}"
                )
                error_text = response.json().get(
                    "error", {"code": "unknown", "message": "Unknown error"}
                )

            except Exception as e:
                traceback.print_exc()
                logger.error(f"Error fetching from {api}: {str(e)}")

        logger.error("All APIs failed")
        return MediaResponse(status="error", error=error_text)
