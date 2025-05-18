from typing import List, Literal, Optional
from urllib.parse import urlparse

from aiogram.types import InputMedia
from pydantic import BaseModel, model_validator


class InlineQueryInfo(BaseModel):
    query: str
    inline: bool
    time_ns: int
    from_user_id: int


class PickerItem(BaseModel):
    type: Literal["gif", "video", "photo"]
    url: str
    thumb: Optional[str] = None


class MediaResponse(BaseModel):
    """
    Model for the media response from the API
    https://github.com/imputnet/cobalt/blob/main/docs/api.md
    """

    status: Literal["error", "tunnel", "picker", "redirect"] = "error"
    picker: Optional[List[PickerItem]] = None

    url: Optional[str] = None
    filename: Optional[str] = None

    audio: Optional[str] = None
    audioFilename: Optional[str] = None

    error: Optional[dict] = None

    type: Literal["gif", "video", "photo", "audio", "file"] = "file"

    @model_validator(mode="after")
    def set_type_and_filename(self):
        """
        1. Always set the type based on file extension in URL
        2. If URL exists but filename doesn't, extract filename from URL
        """
        if self.url:
            path = urlparse(self.url).path.lower()
            if not self.filename:
                path_segments = [s for s in path.split("/") if s]
                if path_segments:
                    last_segment = path_segments[-1]
                    if "." in last_segment:
                        self.filename = last_segment
                    else:
                        self.filename = "file"
                else:
                    self.filename = "file"

            path_lower = self.filename.lower() or self.audioFilename.lower()

            video_extensions = [".mp4", ".mov", ".avi", ".webm", ".mkv", ".flv"]
            image_extensions = [
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".gif",
                ".bmp",
                ".tiff",
            ]
            audio_extensions = [".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac"]
            if any(ext in path_lower for ext in video_extensions):
                self.type = "video"
                if not self.filename:
                    ext = next(ext for ext in video_extensions if ext in path)
                    self.filename = f"video{ext}"

            elif any(ext in path_lower for ext in image_extensions):
                if ".gif" in path_lower:
                    self.type = "gif"
                else:
                    self.type = "photo"
                if not self.filename:
                    ext = next(ext for ext in image_extensions if ext in path)
                    self.filename = f"image{ext}"

            elif any(ext in path_lower for ext in audio_extensions):
                self.type = "audio"
                if not self.filename:
                    ext = next(ext for ext in audio_extensions if ext in path)
                    self.filename = f"audio{ext}"

            else:
                self.type = "file"
                if not self.filename:
                    self.filename = "file"

        return self


class ParsedMediaResponse(BaseModel):
    """
    Model for the parsed media response, ready to be sent to Telegram
    """

    # Media items to be sent to Telegram
    media_items: List[InputMedia] = []

    # Error information
    success: bool = True  # True if at least one item succeeded
    error_message: Optional[str] = None
    error_count: int = 0
    total_count: int = 0

    @property
    def success_count(self) -> int:
        """Number of successfully processed items"""
        return len(self.media_items)

    @property
    def has_errors(self) -> bool:
        """Whether there were any errors during processing"""
        return self.error_count > 0

    @property
    def all_failed(self) -> bool:
        """Whether all items failed to process"""
        return self.total_count > 0 and self.success_count == 0
