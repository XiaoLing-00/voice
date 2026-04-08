"""语音识别（STT）客户端及便捷函数。

对外暴露：
- STTClient.analyze(audio_path) → VoiceResult
- record_and_stt(duration, device_id) → dict   （录音 + 异步 ASR）
- transcribe(audio_path) → VoiceResult          （直接 ASR，无录音）
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any

import requests

from ..audio.recorder import VoiceRecorder
from ..config import ALIYUN_API_KEY, ALIYUN_STT_API_URL, ALIYUN_STT_MODEL
from ..models import AsyncASRResult, RecordBundle, VoiceResult

# 阿里云返回情绪标签 → 内部情绪标签
_EMOTION_MAP: dict[str, str] = {
    "neutral":   "流畅",
    "happy":     "自信",
    "sad":       "迟疑",
    "fearful":   "紧张",
    "angry":     "混乱",
    "surprised": "自信",
    "disgusted": "混乱",
}


class STTClient:
    """阿里云百炼 ASR API 客户端，集成情绪分析。"""

    def __init__(self) -> None:
        if not ALIYUN_API_KEY:
            raise RuntimeError("缺少环境变量：DASHSCOPE_API_KEY")
        self._headers = {
            "Authorization": f"Bearer {ALIYUN_API_KEY}",
            "Content-Type": "application/json",
        }

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _call_api(self, audio_path: str) -> dict:
        import os
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在：{audio_path}")

        print(f"[DEBUG] 开始准备音频数据: {audio_path}")
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        print(f"[DEBUG] 正在编码音频数据 ({len(audio_bytes)} bytes)...")
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        data_uri  = f"data:audio/wav;base64,{audio_b64}"

        payload = {
            "model": ALIYUN_STT_MODEL,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"audio": data_uri}],
                    }
                ]
            },
            "parameters": {"result_format": "message"},
        }

        print("[DEBUG] 正在上传音频数据到 API...")
        t0   = time.time()
        resp = requests.post(ALIYUN_STT_API_URL, headers=self._headers, json=payload, timeout=30)
        print(f"[DEBUG] 上传完成，耗时: {time.time() - t0:.2f}秒，等待推理结果...")
        resp.raise_for_status()

        try:
            raw = resp.json()
            print(f"[DEBUG] 推理完成，总耗时: {time.time() - t0:.2f}秒")
        except json.JSONDecodeError as e:
            raise RuntimeError("语音识别 API 返回结果解析失败：" + str(e))

        return raw

    @staticmethod
    def _parse_response(raw: dict, audio_path: str) -> VoiceResult:
        output  = raw.get("output", {})
        choices = output.get("choices", [])

        _empty = VoiceResult(
            transcript    = "[未检测到语音内容]",
            emotion       = "流畅",
            emotion_detail= "API 返回为空或未识别到有效语音",
            audio_path    = audio_path,
        )

        if not choices:
            _empty.emotion_detail = "API 返回 choices 为空列表"
            return _empty

        message      = choices[0].get("message", {})
        content_list = message.get("content", [])

        if not content_list:
            _empty.emotion_detail = "API 返回 content 为空列表"
            return _empty

        transcript = content_list[0].get("text", "").strip()
        if not transcript:
            _empty.emotion_detail = "API 返回的文本内容为空"
            return _empty

        annotations = message.get("annotations", [])
        audio_info  = next(
            (item for item in annotations if item.get("type") == "audio_info"), {}
        )
        raw_emotion  = audio_info.get("emotion", "neutral")
        final_emotion = _EMOTION_MAP.get(raw_emotion, "流畅")

        return VoiceResult(
            transcript    = transcript,
            emotion       = final_emotion,
            emotion_detail= f"原始情绪: {raw_emotion}",
            audio_path    = audio_path,
        )

    # ── 公开接口 ────────────────────────────────────────────────────────────

    def analyze(self, audio_path: str) -> VoiceResult:
        """对指定音频文件进行 ASR + 情绪分析，返回 VoiceResult。"""
        raw = self._call_api(audio_path)
        try:
            return self._parse_response(raw, audio_path)
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"解析返回结果失败: {e}, 原始响应: {raw}")


# ── 便捷函数 ──────────────────────────────────────────────────────────────────

def record_and_stt(
    duration: int = 8,
    device_id: int | None = None,
) -> dict[str, Any]:
    """录音并异步启动 ASR，立即返回录音元数据与异步结果句柄。

    Returns:
        dict with keys:
            status, audio_file, compressed_audio_file, duration,
            asr_thread, asr_result, message
    """
    recorder = VoiceRecorder(device_id=device_id)
    try:
        audio_path, duration_sec = recorder.record(duration)

        try:
            compressed_audio_path = recorder.compress_audio(audio_path, target_format="mp3", bitrate="64k")
        except Exception as exc:
            print(f"[WARN] 音频压缩失败，回退使用原始 WAV：{exc}")
            compressed_audio_path = audio_path

        async_result = AsyncASRResult()

        def _asr_task() -> None:
            try:
                client     = STTClient()
                asr_result = client.analyze(audio_path)
                non_speech = (
                    not asr_result.transcript.strip()
                    or asr_result.transcript.startswith("[未检测到语音内容]")
                )
                bundle = RecordBundle(
                    transcript           = "" if non_speech else asr_result.transcript,
                    audio_path           = audio_path,
                    duration             = duration_sec,
                    emotion              = asr_result.emotion,
                    compressed_audio_file= compressed_audio_path,
                    non_speech           = non_speech,
                )
                async_result.set_result(bundle)
            except Exception as e:
                async_result.set_error(str(e))

        thread = threading.Thread(target=_asr_task, daemon=True)
        thread.start()

        return {
            "status":                "recorded",
            "audio_file":            audio_path,
            "compressed_audio_file": compressed_audio_path,
            "duration":              duration_sec,
            "asr_thread":            thread,
            "asr_result":            async_result,
            "message":               "录音成功，ASR 正在异步处理中",
        }
    except Exception as e:
        raise RuntimeError("语音处理失败：" + str(e))
    finally:
        recorder.close()


def transcribe(audio_path: str) -> VoiceResult:
    """直接对现有音频文件进行 ASR，不录音。"""
    return STTClient().analyze(audio_path)
