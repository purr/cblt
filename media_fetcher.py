import asyncio
import traceback
from functools import lru_cache
from threading import Lock

import httpx
import requests
from aiogram.types import (
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    URLInputFile,
)
from yarl import URL

from logger import logger
from models import MediaResponse, ParsedMediaResponse

# Cache successful URL content checks to avoid redundant requests
_url_content_lock = Lock()


async def fix_url(url, status):
    #     if status == "redirect":
    #         for _ in range(3):
    #             payload = {"url": url, "alias": "", "password": "", "max-clicks": ""}
    #             response = httpx.post(
    #                 "https://spoo.me/", json=payload, follow_redirects=True
    #             )
    #             if response.is_redirect:
    #                 return response.url.replace("result/", "")

    return URL(url, encoded=True)


@lru_cache(maxsize=100)
def _sync_url_has_content(url: str) -> bool:
    """Synchronous helper to check if a URL has content, with caching for positive results only."""
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True)
        if resp.status_code == 200 and int(resp.headers.get("Content-Length", 0)) > 22:
            return True
        return False
    except Exception:
        return False


async def check_url_has_content(url: str) -> bool:
    """
    Check if a URL has at least 1 bit of content without downloading the entire file.
    Uses async HTTP requests and caching for faster performance.
    """
    # Use the synchronous cache in a thread-safe way
    try:
        # Use a lock to avoid race conditions in the cache
        with _url_content_lock:
            cached = _sync_url_has_content(url)
        if cached:
            logger.info(f"URL found in cache: {url}")
            return True
    except Exception:
        pass

    # If not cached, check asynchronously and update cache if positive
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.head(url, timeout=5)
            if (
                resp.status_code == 200
                and int(resp.headers.get("Content-Length", 0)) > 22
            ):
                # Store positive result in cache
                with _url_content_lock:
                    _sync_url_has_content(url)
                return True
            return False
    except Exception as e:
        logger.error(f"Error checking URL content: {e}")
        return False


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

            check_tasks = []
            for item in response.picker:
                check_tasks.append(check_url_has_content(item.url))
                if item.thumb:
                    check_tasks.append(check_url_has_content(item.thumb))
                else:
                    check_tasks.append(asyncio.sleep(0))

            check_results = await asyncio.gather(*check_tasks)

            for index, item in enumerate(response.picker):
                try:
                    url_has_content = check_results[index * 2]
                    thumb_has_content = (
                        check_results[index * 2 + 1] if item.thumb else False
                    )

                    if not url_has_content:
                        result.error_count += 1
                        logger.warning(f"Picker item {index + 1} has empty content")
                        continue

                    input_file = URLInputFile(await fix_url(item.url, response.status))
                    thumbnail = None
                    if item.thumb and thumb_has_content:
                        try:
                            thumbnail = URLInputFile(
                                await fix_url(item.thumb, response.status)
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
                    if response.status != "tunnel":
                        if not await check_url_has_content(response.audio):
                            result.error_count = 1
                            result.success = False
                        result.error_message = (
                            "Failed to download, audio appears to be empty"
                        )
                        return result

                    input_file = URLInputFile(
                        await fix_url(response.audio, response.status),
                        filename=response.audioFilename or "audio.mp3",
                    )
                    media = InputMediaAudio(media=input_file)
                    result.media_items.append(media)
                    return result

                if response.url:
                    if response.status != "tunnel":
                        if not await check_url_has_content(response.url):
                            result.error_count = 1
                            result.success = False
                            result.error_message = (
                                "Failed to download, file appears to be empty"
                            )
                            return result

                    filename = response.filename or "file"
                    input_file = URLInputFile(
                        await fix_url(response.url, response.status),
                        filename=filename,
                    )

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
                traceback.print_exc()
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
            "https://cobalt-7.kwiatekmiki.com/api/json",
            "https://cobalt.255x.ru",
            "https://co.eepy.today",
            "https://co.otomir23.me",
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
            "alwaysProxy": True,
        }

        error_text = None
        if audio:
            payload["downloadMode"] = "audio"

        for x in range(2):
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
                    try:
                        error_text = response.json().get(
                            "error", {"code": "unknown", "message": "Unknown error"}
                        )
                    except Exception:
                        pass

                except Exception as e:
                    traceback.print_exc()
                    logger.error(f"Error fetching from {api}: {str(e)}")

            logger.error("All APIs failed")

        return MediaResponse(status="error", error=error_text)
