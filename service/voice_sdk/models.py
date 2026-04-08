from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

from .config import VALID_EMOTIONS


class AsyncASRResult:
    """异步 ASR 任务结果占位对象，由 ASR 线程写入。"""

    def __init__(self) -> None:
        self.done:   bool      = False
        self.result: Any | None = None
        self.error:  str | None = None

    def set_result(self, result: Any) -> None:
        self.result = result
        self.done   = True

    def set_error(self, error: str) -> None:
        self.error = error
        self.done  = True


class VoiceResult(BaseModel):
    transcript:     str
    emotion:        str
    emotion_detail: str = ""
    audio_path:     str = ""   # 本地音频文件路径
    audio_url:      str = ""   # 可选网络路径


class RecordBundle(BaseModel):
    transcript:            str
    audio_path:            str
    duration:              float
    emotion:               str
    compressed_audio_file: str  = ""
    non_speech:            bool = False

    @field_validator("emotion")
    @classmethod
    def validate_emotion(cls, v: str) -> str:
        if v not in VALID_EMOTIONS:
            raise ValueError(
                f"情绪标签必须是：{', '.join(sorted(VALID_EMOTIONS))}，但得到：{v}"
            )
        return v
