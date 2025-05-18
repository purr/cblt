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
from models import MediaResponse, ParsedMediaResponse


async def check_url_has_content(url: str) -> bool:
    """
    Check if a URL has at least 1 bit of content without downloading the entire file.

    Args:
        url: The URL to check

    Returns:
        bool: True if the URL has content, False otherwise
    """
    try:
        # Make a HEAD request to get headers without downloading content
        response = requests.head(url, timeout=5)

        # Check if the server supports HEAD requests
        if response.status_code < 400:
            # Check Content-Length header
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 0:
                logger.info(
                    f"URL has content: {url} (Content-Length: {content_length})"
                )
                return True

        # If HEAD request fails or doesn't have Content-Length, try a GET with Range header
        response = requests.get(
            url,
            headers={"Range": "bytes=0-0"},  # Request just the first byte
            timeout=5,
        )

        if response.status_code // 100 in (2, 3):  # Check if it starts with 2 or 3
            if len(response.content) > 0:
                logger.info(f"URL has content: {url}")
                return True
        logger.warning(f"URL appears to be empty: {url}")
        return False
    except Exception as e:
        logger.warning(f"Error checking URL content: {e}")
        # If we can't check, assume it has content
        return True


async def parse_media_response(response: MediaResponse) -> ParsedMediaResponse:
    """Parse the media response and return a structured ParsedMediaResponse

    Returns a ParsedMediaResponse object with media items and error information.
    """
    result = ParsedMediaResponse()

    if response.status == "error":
        logger.error(f"Media fetcher error: {response.error}")
        result.success = False
        if isinstance(response.error, dict):
            error_code = response.error.get("code", "unknown")
            error_context = response.error.get("context", {})

            error_str = f"{error_code}"
            if error_context:
                context_str = ", ".join(f"{k}: {v}" for k, v in error_context.items())
                error_str += f"\nContext: {context_str}"

            result.error_message = error_str
        else:
            result.error_message = str(response.error)
        return result

    try:
        if response.status == "picker" and response.picker:
            result.total_count = len(response.picker)

            for index, item in enumerate(response.picker):
                try:
                    # Check if URL has content
                    if not await check_url_has_content(item.url):
                        result.error_count += 1
                        logger.warning(f"Picker item {index + 1} has empty content")
                        continue

                    input_file = URLInputFile(item.url)
                    thumbnail = None
                    if item.thumb:
                        try:
                            if await check_url_has_content(item.thumb):
                                thumbnail = URLInputFile(item.thumb)
                            else:
                                logger.warning(
                                    f"Thumbnail for item {index + 1} is empty, skipping thumbnail"
                                )
                        except Exception as thumb_error:
                            logger.warning(f"Failed to create thumbnail: {thumb_error}")

                    if item.type == "photo":
                        media = InputMediaPhoto(media=input_file, thumbnail=thumbnail)
                        result.media_items.append(media)
                    elif item.type == "video":
                        media = InputMediaVideo(media=input_file, thumbnail=thumbnail)
                        result.media_items.append(media)
                    elif item.type == "gif":
                        media = InputMediaAnimation(
                            media=input_file, thumbnail=thumbnail
                        )
                        result.media_items.append(media)
                except Exception as item_error:
                    result.error_count += 1
                    logger.warning(
                        f"Failed to process picker item {index + 1}: {item_error}"
                    )

            # Update success status
            result.success = len(result.media_items) > 0
            if not result.success:
                result.error_message = "Failed to process all media items"

            logger.info(
                f"Created media group with {result.success_count} items, {result.error_count} errors out of {result.total_count} total"
            )
            return result

        if response.status in ["tunnel", "redirect"]:
            result.total_count = 1

            try:
                if response.audio:
                    # Check if audio URL has content
                    if not await check_url_has_content(response.audio):
                        result.error_count = 1
                        result.success = False
                        result.error_message = (
                            "Failed to download, audio appears to be empty"
                        )
                        return result

                    input_file = URLInputFile(
                        response.audio,
                        filename=response.audioFilename or "audio.mp3",
                    )
                    media = InputMediaAudio(media=input_file)
                    result.media_items.append(media)
                    return result

                if response.url:
                    # Check if URL has content
                    if not await check_url_has_content(response.url):
                        result.error_count = 1
                        result.success = False
                        result.error_message = (
                            "Failed to download, file appears to be empty"
                        )
                        return result

                    filename = response.filename or "file"
                    input_file = URLInputFile(response.url, filename=filename)

                    # Use the type field from MediaResponse instead of checking filename extensions
                    if response.type == "video":
                        logger.info("Creating video object based on type")
                        media = InputMediaVideo(media=input_file)
                        result.media_items.append(media)
                    elif response.type == "photo":
                        logger.info("Creating photo object based on type")
                        media = InputMediaPhoto(media=input_file)
                        result.media_items.append(media)
                    elif response.type == "gif":
                        logger.info("Creating gif object based on type")
                        media = InputMediaAnimation(media=input_file)
                        result.media_items.append(media)
                    elif response.type == "audio":
                        logger.info("Creating audio object based on type")
                        media = InputMediaAudio(media=input_file)
                        result.media_items.append(media)
                    else:
                        logger.info("Creating document object based on type")
                        media = InputMediaDocument(
                            media=input_file, disable_content_type_detection=False
                        )
                        result.media_items.append(media)

                    return result

            except Exception as url_error:
                logger.error(f"Error creating media from URL: {url_error}")
                result.error_count = 1
                result.success = False
                result.error_message = "Failed to process media URL"
                return result

        logger.warning(f"Unhandled response type: {response.status}")
        result.success = False
        result.error_message = "Unable to process this media type"
        return result
    except Exception as e:
        logger.error(f"Error in parse_media_response: {e}")
        result.success = False
        result.error_message = "Error processing media"
        return result


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
