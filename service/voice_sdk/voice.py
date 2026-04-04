import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import queue
import wave
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import requests
import sounddevice as sd
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError, field_validator

load_dotenv()

RECORD_CONFIG = {
    "samplerate": 16000,
    "channels": 1,
    "dtype": "int16",
    "format": "wav",
}

OUTPUT_AUDIO_DIR = os.path.abspath(os.path.join(os.getcwd(), "output_audio"))
RECORDINGS_DIR = os.path.abspath(os.path.join(os.getcwd(), "recordings"))

ALIYUN_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
ALIYUN_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

VALID_EMOTIONS = {"自信", "紧张", "迟疑", "流畅", "混乱"}


class AsyncASRResult:
    done: bool
    result: Any | None
    error: str | None

    def __init__(self) -> None:
        self.done = False
        self.result = None
        self.error = None

    def set_result(self, result: Any) -> None:
        self.result = result
        self.done = True

    def set_error(self, error: str) -> None:
        self.error = error
        self.done = True


class VoiceResult(BaseModel):
    transcript: str
    emotion: str
    emotion_detail: str = ""
    audio_path: str = ""  # 本地音频文件路径
    audio_url: str = ""   # 可选网络路径


class RecordBundle(BaseModel):
    transcript: str
    audio_path: str
    duration: float
    emotion: str
    compressed_audio_file: str = ""
    non_speech: bool = False

    @field_validator("emotion")
    def validate_emotion(cls, v):
        if v not in VALID_EMOTIONS:
            raise ValueError(f"情绪标签必须是：{', '.join(sorted(VALID_EMOTIONS))}, 但得到：{v}")
        return v


# voice.py 中 VoiceRecorder 类的改动部分

import pyaudio  # 新增，替代 sounddevice

class VoiceRecorder:
    """音频录制类，支持最长 60 秒、取消、立即发送（中断）"""

    def __init__(self, device_id: int | None = None) -> None:
        self.temp_dir = os.path.abspath(os.path.join(os.getcwd(), "temp_audio"))
        os.makedirs(self.temp_dir, exist_ok=True)

        self.output_dir = OUTPUT_AUDIO_DIR
        os.makedirs(self.output_dir, exist_ok=True)

        self.recordings_dir = RECORDINGS_DIR
        os.makedirs(self.recordings_dir, exist_ok=True)

        self.temp_file = None
        self.device_id = device_id  # 允许手动指定设备ID，None 则用系统默认输入

        self._frames = []
        self._recording = False
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()

        # 用 pyaudio 替代 sounddevice
        self._pa = pyaudio.PyAudio()

    @staticmethod
    def _close_audio_stream(stream: Any | None) -> None:
        if stream is None:
            return
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    @staticmethod
    def _normalized_audio_metrics(audio_data: np.ndarray) -> tuple[float, float]:
        if audio_data.size == 0:
            return 0.0, 0.0

        normalized = audio_data.astype(np.float32) / 32768.0
        peak = float(np.max(np.abs(normalized)))
        rms = float(np.sqrt(np.mean(np.square(normalized))))
        return peak, rms

    def record(self, duration: int = 60) -> tuple[str, float]:
        """录制语音最长60秒，可在外部调用 stop() 立即结束。

        返回: (音频路径, 录音时长秒数)
        """
        if duration <= 0 or duration > 60:
            raise ValueError("录音时长必须在1-60秒之间")

        chunk = 1024
        samplerate = RECORD_CONFIG["samplerate"]
        # pyaudio 固定单声道，与 RECORD_CONFIG 一致
        channels = 1

        self._frames = []
        self._stop_event.clear()
        self._cancel_event.clear()

        out_wav = os.path.join(self.temp_dir, f"rec_{uuid.uuid4()}.wav")

        self._recording = True
        silent_start = None
        start = time.time()
        stream: Any | None = None

        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=samplerate,
                input=True,
                input_device_index=self.device_id,  # None = 系统默认输入设备
                frames_per_buffer=chunk,
            )
        except Exception as e:
            self._recording = False
            raise RuntimeError("麦克风录制失败，请检查设备是否连接：" + str(e))

        print(f"[DEBUG] 开始录音，最大时长: {duration}秒")
        try:
            while time.time() - start < duration:
                if self._cancel_event.is_set():
                    print("[DEBUG] 检测到取消信号，立即停止")
                    break
                if self._stop_event.is_set():
                    print("[DEBUG] 检测到停止信号，立即停止")
                    break

                data = stream.read(chunk, exception_on_overflow=False)
                self._frames.append(data)

                # 振幅检测（用于 VAD 静音判断）
                audio_chunk = np.frombuffer(data, dtype=np.int16)
                current_peak, _ = self._normalized_audio_metrics(audio_chunk)

                # VAD：持续 2 秒静音自动停止
                silence_threshold = 500.0 / 32768.0
                if current_peak < silence_threshold:
                    if silent_start is None:
                        silent_start = time.time()
                    elif time.time() - silent_start >= 2.0:
                        print("[DEBUG] 识别到2秒静音，自动停止录音")
                        break
                else:
                    silent_start = None

        except Exception as e:
            self._recording = False
            self._close_audio_stream(stream)
            raise RuntimeError("录音过程中发生异常：" + str(e))
        finally:
            self._close_audio_stream(stream)

        actual_duration = time.time() - start
        self._recording = False
        print(f"[DEBUG] 录音完成 - 总耗时: {actual_duration:.3f}秒, 捕获块数: {len(self._frames)}")

        if self._cancel_event.is_set():
            self._frames = []
            raise RuntimeError("录音已取消")

        if not self._frames:
            raise RuntimeError("未获取到录音数据，请重试")

        # 拼接所有音频块
        audio_data = np.frombuffer(b"".join(self._frames), dtype=np.int16)
        self._frames = []
        peak, rms = self._normalized_audio_metrics(audio_data)

        # 最短时长校验
        if len(audio_data) < samplerate * 0.3:
            raise RuntimeError("录音时长过短，请重新录入（至少 0.3 秒）")

        # 振幅校验
        if peak < 500.0 / 32768.0:
            raise RuntimeError(
                f"麦克风无输入信号（最大振幅：{peak:.6f}），"
                "请检查麦克风是否静音或未正确连接"
            )

        # 音量校验
        if rms < 0.003:
            raise RuntimeError("录音音量太低，请靠近麦克风重试")

        # 写入 WAV 文件（单声道，与原格式一致）
        actual_duration_sec = len(audio_data) / samplerate
        print(f"[DEBUG] 音频数据: {len(audio_data)} 样本 = {actual_duration_sec:.3f}秒")

        os.makedirs(self.temp_dir, exist_ok=True)
        with wave.open(out_wav, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(samplerate)
            wf.writeframes(audio_data.tobytes())

        # 移动到 output_audio 目录
        dest_wav = os.path.join(self.output_dir, f"voice_{uuid.uuid4()}.wav")
        shutil.move(out_wav, dest_wav)

        print(f"[DEBUG] 音频文件已保存: {dest_wav} ({os.path.getsize(dest_wav)} bytes)")
        self.temp_file = dest_wav
        return self.temp_file, actual_duration_sec

    def compress_audio(
        self,
        audio_path: str,
        target_format: str = "mp3",
        bitrate: str = "64k",
    ) -> str:
        """将录制得到的 WAV 压缩为目标格式，默认输出 MP3。"""
        if not audio_path:
            raise ValueError("audio_path 不能为空")
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在：{audio_path}")

        try:
            from pydub import AudioSegment
        except Exception as exc:
            raise RuntimeError(
                "缺少音频压缩依赖，请安装 pydub 并确保 ffmpeg 可用：pip install pydub"
            ) from exc

        output_format = target_format.lstrip(".").lower().strip()
        if not output_format:
            raise ValueError("target_format 不能为空")

        try:
            audio_segment = AudioSegment.from_file(audio_path)
        except Exception as exc:
            raise RuntimeError(f"读取待压缩音频失败：{exc}") from exc

        output_path = os.path.join(self.output_dir, f"voice_{uuid.uuid4()}.{output_format}")
        export_kwargs: dict[str, Any] = {"format": output_format}
        if output_format == "mp3":
            export_kwargs["bitrate"] = bitrate

        try:
            audio_segment.export(output_path, **export_kwargs)
        except Exception as exc:
            raise RuntimeError(f"音频压缩失败，请检查 ffmpeg 是否可用：{exc}") from exc

        return output_path

    def stop(self):
        if self._recording:
            self._stop_event.set()

    def cancel(self):
        if self._recording:
            self._cancel_event.set()

    def clean_temp(self):
        """清理临时音频文件"""
        self.temp_file = None
        try:
            if os.path.exists(self.temp_dir):
                files = [
                    os.path.join(self.temp_dir, f)
                    for f in os.listdir(self.temp_dir)
                    if f.startswith("rec_") and f.endswith(".wav")
                ]
                if len(files) > 50:
                    files_with_time = [(f, os.path.getmtime(f)) for f in files]
                    files_with_time.sort(key=lambda x: x[1])
                    for f, _ in files_with_time[:-50]:
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                current_time = time.time()
                for f in files:
                    try:
                        if current_time - os.path.getmtime(f) > 3600:
                            os.remove(f)
                    except OSError:
                        pass
        except Exception:
            pass

    def __del__(self):
        """析构时释放 pyaudio 资源"""
        try:
            self._pa.terminate()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._pa.terminate()
        except Exception:
            pass


class StreamingAudioPlayer:
    """将流式音频 chunk 放入后台线程顺序播放。"""

    def __init__(self, default_sample_rate: int = 24000, default_channels: int = 1):
        # TTS 播放统一输出为 24kHz / 单声道 / 16-bit PCM，避免频繁重建设备流。
        self.default_sample_rate = default_sample_rate or 24000
        self.default_channels = default_channels or 1
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._closed = threading.Event()
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.default_channels,
            rate=self.default_sample_rate,
            output=True,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, audio_chunk: bytes) -> None:
        if self._closed.is_set():
            return
        if audio_chunk:
            self._queue.put(audio_chunk)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put(None)

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            while True:
                audio_chunk = self._queue.get()
                if audio_chunk is None:
                    break
                try:
                    pcm_bytes = self._decode_chunk(audio_chunk)
                    if not pcm_bytes:
                        continue
                    self._stream.write(pcm_bytes)
                except Exception as exc:
                    print(f"[TTS Player] chunk play failed: {exc}")
        finally:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            try:
                self._pa.terminate()
            except Exception:
                pass

    def _decode_chunk(self, audio_chunk: bytes) -> bytes:
        """优先按原生 PCM 播放；若遇到 WAV 数据则提取帧后再播放。"""
        if not audio_chunk:
            return b""

        # 兼容回退 URL 可能返回的 WAV 文件；流式 chunk 通常是原生 PCM。
        if audio_chunk.startswith(b"RIFF"):
            try:
                with wave.open(io.BytesIO(audio_chunk), "rb") as wf:
                    return wf.readframes(wf.getnframes())
            except Exception:
                return b""

        return audio_chunk

    @staticmethod
    def _sample_width_to_format(sample_width: int):
        if sample_width == 1:
            return pyaudio.paInt8
        if sample_width == 2:
            return pyaudio.paInt16
        if sample_width == 4:
            return pyaudio.paInt32
        return pyaudio.paInt16

class STTClient:
    """阿里云百炼ASR API调用客户端，集成情绪分析"""

    def __init__(self):
        if not ALIYUN_API_KEY:
            raise RuntimeError("缺少环境变量：DASHSCOPE_API_KEY")

        self.headers = {
            "Authorization": f"Bearer {ALIYUN_API_KEY}",
            "Content-Type": "application/json",
        }

    def _call_asr_api(self, audio_path: str) -> dict:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在：{audio_path}")

        print(f"[DEBUG] 开始准备音频数据: {audio_path}")
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        # 转换为 Data URL 格式
        print(f"[DEBUG] 正在编码音频数据 ({len(audio_bytes)} bytes)...")
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        data_uri = f"data:audio/wav;base64,{audio_b64}"

        payload = {
            "model": "qwen3-asr-flash",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"audio": data_uri}
                        ]
                    }
                ]
            },
            "parameters": {
                "result_format": "message"
            }
        }

        print(f"[DEBUG] 正在上传音频数据到 API...")
        upload_start = time.time()
        resp = requests.post(ALIYUN_API_URL, headers=self.headers, json=payload, timeout=30)
        upload_end = time.time()
        print(f"[DEBUG] 上传完成，耗时: {upload_end - upload_start:.2f}秒，正在等待推理结果...")
        
        resp.raise_for_status()

        try:
            raw = resp.json()
            inference_end = time.time()
            print(f"[DEBUG] 推理完成，总耗时: {inference_end - upload_start:.2f}秒")
        except json.JSONDecodeError as e:
            raise RuntimeError("语音识别 API 返回结果解析失败：" + str(e))

        return raw

    def analyze(self, audio_path: str) -> VoiceResult:
        raw = self._call_asr_api(audio_path)

        try:
            # 防御性编程：检查返回结构
            output = raw.get("output", {})
            choices = output.get("choices", [])

            if not choices:
                return VoiceResult(
                    transcript="[未检测到语音内容]",
                    emotion="流畅",
                    emotion_detail="API 返回 choices 为空列表，可能录音质量问题或静音",
                    audio_path=audio_path,
                )

            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", [])

            if not content:
                return VoiceResult(
                    transcript="[未检测到语音内容]",
                    emotion="流畅",
                    emotion_detail="API 返回 content 为空列表，可能未识别到有效语音",
                    audio_path=audio_path,
                )

            transcript = content[0].get("text", "").strip()
            if not transcript:
                return VoiceResult(
                    transcript="[未检测到语音内容]",
                    emotion="流畅",
                    emotion_detail="API 返回的文本内容为空",
                    audio_path=audio_path,
                )

            annotations = message.get("annotations", [])
            audio_info = next((item for item in annotations if item.get("type") == "audio_info"), {})
            raw_emotion = audio_info.get("emotion", "neutral")

            # 映射逻辑：将模型标签映射到 VALID_EMOTIONS
            emotion_mapping = {
                "neutral": "流畅",
                "happy": "自信",
                "sad": "迟疑",
                "fearful": "紧张",
                "angry": "混乱",
                "surprised": "自信",
                "disgusted": "混乱",
            }
            final_emotion = emotion_mapping.get(raw_emotion, "流畅")

            return VoiceResult(
                transcript=transcript,
                emotion=final_emotion,
                emotion_detail=f"原始情绪: {raw_emotion}",
                audio_path=audio_path,
            )

        except (KeyError, IndexError) as e:
            raise RuntimeError(f"解析返回结果失败: {str(e)}, 原始响应: {raw}")


def record_and_stt(duration: int = 8, device_id: int | None = None) -> dict[str, Any]:
    recorder = VoiceRecorder(device_id=device_id)
    try:
        audio_path, duration_sec = recorder.record(duration)
        try:
            compressed_audio_path = recorder.compress_audio(
                audio_path,
                target_format="mp3",
                bitrate="64k",
            )
        except Exception as exc:
            print(f"[WARN] 音频压缩失败，回退使用原始 WAV：{exc}")
            compressed_audio_path = audio_path

        async_result = AsyncASRResult()

        def _asr_task() -> None:
            try:
                client = STTClient()
                asr_result = client.analyze(audio_path)

                non_speech = asr_result.transcript.strip() == "" or asr_result.transcript.startswith("[未检测到语音内容]")
                bundle = RecordBundle(
                    transcript="" if non_speech else asr_result.transcript,
                    audio_path=audio_path,
                    duration=duration_sec,
                    emotion=asr_result.emotion,
                    compressed_audio_file=compressed_audio_path,
                    non_speech=non_speech,
                )

                async_result.set_result(bundle)
            except Exception as _e:
                async_result.set_error(str(_e))

        thread = threading.Thread(target=_asr_task, daemon=True)
        thread.start()

        return {
            "status": "recorded",
            "audio_file": audio_path,
            "compressed_audio_file": compressed_audio_path,
            "duration": duration_sec,
            "asr_thread": thread,
            "asr_result": async_result,
            "message": "录音成功，ASR 正在异步处理中"
        }

    except Exception as e:
        raise RuntimeError("语音处理失败：" + str(e))
    finally:
        # 暂时禁用自动清理，方便调试和手动检查文件
        # recorder.clean_temp()
        recorder.close()


def transcribe(mp3_path: str) -> VoiceResult:
    client = STTClient()
    return client.analyze(mp3_path)


DEFAULT_SENTENCE_PUNCTUATIONS = frozenset({
    ".",
    "。",
    "!",
    "！",
    "?",
    "？",
    ",",
    "，",
    ";",
    "；",
    ":",
    "：",
    "\n",
})

DEFAULT_TTS_BEIJING_API_URL = "https://dashscope.aliyuncs.com/api/v1"

AudioChunkCallback = Callable[[bytes, str], None]


def iter_sentences_from_token_stream(
    token_stream: Iterable[str],
    sentence_punctuations: set[str] | frozenset[str] = DEFAULT_SENTENCE_PUNCTUATIONS,
    flush_tail: bool = True,
    max_buffer_length: int | None = None,
) -> Iterator[str]:
    """将 token 流聚合为完整句子。

    Args:
        token_stream: LLM 按 token 产出的文本流。
        sentence_punctuations: 触发分句的标点集合。
        flush_tail: 当流结束且缓冲区非空时，是否输出最后残余文本。
        max_buffer_length: 无标点时缓冲区最大长度，超过即强制切句。

    Yields:
        按标点切分后的句子（保留结尾标点）。
    """
    if not sentence_punctuations:
        raise ValueError("sentence_punctuations 不能为空")
    if max_buffer_length is not None and max_buffer_length <= 0:
        raise ValueError("max_buffer_length 必须大于 0")

    punctuation_regex = re.compile("[" + re.escape("".join(sentence_punctuations)) + "]")
    buffer = ""

    for token in token_stream:
        if token is None:
            continue

        token_text = str(token)
        if not token_text:
            continue

        buffer += token_text

        # 无标点长文本兜底：避免一直不触发分句导致无语音输出
        if max_buffer_length is not None and len(buffer) >= max_buffer_length:
            sentence = buffer[:max_buffer_length].strip()
            buffer = buffer[max_buffer_length:]
            if sentence:
                yield sentence

        while True:
            matched = punctuation_regex.search(buffer)
            if matched is None:
                break

            split_index = matched.end()
            sentence = buffer[:split_index].strip()
            buffer = buffer[split_index:]
            if sentence:
                yield sentence

    if flush_tail:
        tail = buffer.strip()
        if tail:
            yield tail


def _extract_audio_base64(payload: Any) -> str | None:
    """从 DashScope 流式事件里提取音频 base64 字段。"""
    if isinstance(payload, dict):
        if isinstance(payload.get("audio"), dict):
            audio_data = payload["audio"].get("data")
            if isinstance(audio_data, str) and audio_data:
                return audio_data

        output = payload.get("output")
        if isinstance(output, dict):
            if isinstance(output.get("audio"), dict):
                audio_data = output["audio"].get("data")
                if isinstance(audio_data, str) and audio_data:
                    return audio_data

            choices = output.get("choices")
            if isinstance(choices, list):
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    message = choice.get("message")
                    if not isinstance(message, dict):
                        continue
                    content_list = message.get("content")
                    if not isinstance(content_list, list):
                        continue
                    for content in content_list:
                        if not isinstance(content, dict):
                            continue
                        if isinstance(content.get("audio"), dict):
                            audio_data = content["audio"].get("data")
                            if isinstance(audio_data, str) and audio_data:
                                return audio_data

        for value in payload.values():
            nested = _extract_audio_base64(value)
            if nested:
                return nested

    if isinstance(payload, list):
        for item in payload:
            nested = _extract_audio_base64(item)
            if nested:
                return nested

    return None


def _extract_audio_url(payload: Any) -> str | None:
    """从 DashScope 返回里提取完整音频 URL。"""
    if isinstance(payload, dict):
        if isinstance(payload.get("audio"), dict):
            audio_url = payload["audio"].get("url")
            if isinstance(audio_url, str) and audio_url:
                return audio_url

        output = payload.get("output")
        if isinstance(output, dict) and isinstance(output.get("audio"), dict):
            audio_url = output["audio"].get("url")
            if isinstance(audio_url, str) and audio_url:
                return audio_url

        for value in payload.values():
            nested = _extract_audio_url(value)
            if nested:
                return nested

    if isinstance(payload, list):
        for item in payload:
            nested = _extract_audio_url(item)
            if nested:
                return nested

    return None


def _normalize_dashscope_payload(payload: Any) -> Any:
    """将 DashScope SDK 返回的对象递归转换为基础 Python 类型。"""
    if payload is None or isinstance(payload, (str, int, float, bool)):
        return payload

    if isinstance(payload, dict):
        return {key: _normalize_dashscope_payload(value) for key, value in payload.items()}

    if isinstance(payload, list):
        return [_normalize_dashscope_payload(item) for item in payload]

    if isinstance(payload, tuple):
        return [_normalize_dashscope_payload(item) for item in payload]

    if hasattr(payload, "__dict__"):
        return {
            key: _normalize_dashscope_payload(value)
            for key, value in vars(payload).items()
            if not key.startswith("_")
        }

    return payload


def stream_tts_audio_chunks(
    sentence: str,
    *,
    api_key: str,
    model: str = "qwen3-tts-instruct-flash",
    voice: str = "Elias",
    api_base_url: str = DEFAULT_TTS_BEIJING_API_URL,
) -> Iterator[bytes]:
    """对单句文本进行 DashScope 流式 TTS，并逐块产出音频二进制数据。

    Args:
        sentence: 需要合成的完整句子。
        api_key: DashScope API Key。
        model: TTS 模型名称，默认 qwen3-tts-instruct-flash。
        voice: 朗读音色。
        api_base_url: DashScope 接口地址，默认北京节点。

    Yields:
        bytes: 每个流式音频 chunk 的二进制数据。
    """
    text = sentence.strip()
    if not text:
        return

    if not api_key:
        raise ValueError("api_key 不能为空")

    try:
        import dashscope
        from dashscope import MultiModalConversation
    except ImportError as exc:
        raise ImportError(
            "缺少 dashscope 依赖或版本过低，请先安装/升级: pip install -U dashscope"
        ) from exc

    dashscope.api_key = api_key
    # 按官方文档使用可配置的接口地址；调用方可显式切换新加坡地域。
    dashscope.base_http_api_url = api_base_url or DEFAULT_TTS_BEIJING_API_URL

    # qwen3-tts 系列不支持 Cherry，自动回退到 Elias。
    if voice == "Cherry":
        voice = "Elias"

    print(f"[TTS] sentence: {text}")
    print(f"[TTS] using endpoint: {dashscope.base_http_api_url}")
    print(f"[TTS] request variant=1 (MultiModalConversation.call) model={model} voice={voice}")

    def _is_transient_tts_error(exc: Exception) -> bool:
        text_msg = str(exc).lower()
        markers = (
            "ssl",
            "ssleoferror",
            "connection reset",
            "connection aborted",
            "eof occurred in violation of protocol",
            "temporarily unavailable",
            "timed out",
            "timeout",
        )
        return any(marker in text_msg for marker in markers)

    emitted = False
    max_attempts = 3
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        emitted_stream_chunk = False
        fallback_audio_url: str | None = None

        try:
            response_stream = MultiModalConversation.call(
                model=model,
                api_key=api_key,
                text=text,
                voice=voice,
                stream=True,
            )

            for event in response_stream:
                normalized_event = _normalize_dashscope_payload(event)

                audio_b64 = _extract_audio_base64(normalized_event)
                if audio_b64:
                    try:
                        audio_chunk = base64.b64decode(audio_b64.strip())
                    except Exception:
                        audio_chunk = b""

                    if audio_chunk:
                        emitted = True
                        emitted_stream_chunk = True
                        yield audio_chunk
                        continue

                if not emitted_stream_chunk:
                    audio_url = _extract_audio_url(normalized_event)
                    if audio_url:
                        fallback_audio_url = audio_url

            # 只有完全没有流式 chunk 时，才回退下载完整音频，避免一句话播两遍。
            if not emitted_stream_chunk and fallback_audio_url:
                try:
                    resp = requests.get(fallback_audio_url, timeout=30)
                    resp.raise_for_status()
                    audio_chunk = resp.content
                except Exception as exc:
                    print(f"[TTS] download audio url failed: {exc}")
                    audio_chunk = b""

                if audio_chunk:
                    emitted = True
                    yield audio_chunk

            if emitted:
                break

        except Exception as exc:
            last_exc = exc
            # 若已产出过音频，避免重试导致重播；保留已播内容并退出。
            if emitted:
                break

            if attempt < max_attempts and _is_transient_tts_error(exc):
                print(f"[TTS] transient network/ssl error, retry {attempt}/{max_attempts}: {exc}")
                time.sleep(0.4 * attempt)
                continue

            print(f"[TTS] variant=1 call failed: {exc}")
            raise RuntimeError("TTS 未返回可用音频 chunk，请检查模型、参数和账号权限") from exc

    if not emitted:
        if last_exc is not None:
            raise RuntimeError("TTS 未返回可用音频 chunk，请检查模型、参数和账号权限") from last_exc
        raise RuntimeError("TTS 未返回可用音频 chunk，请检查模型、参数和账号权限")

def stream_interview_tts_from_tokens(
    token_stream: Iterable[str],
    on_audio_chunk: AudioChunkCallback,
    *,
    api_key: str,
    model: str = "qwen3-tts-instruct-flash",
    api_base_url: str = DEFAULT_TTS_BEIJING_API_URL,
    sentence_punctuations: set[str] | frozenset[str] = DEFAULT_SENTENCE_PUNCTUATIONS,
    max_workers: int = 4,
    ordered_output: bool = False,
    max_buffer_length: int | None = 120,
    voice: str = "Elias",
) -> None:
    """将 LLM token 流转换为流式语音并回调音频 chunk。

    流程:
    1. 按标点将 token 流聚合为句子。
    2. 对每个句子调用 DashScope 流式 TTS。
    3. 每产出一个音频 chunk，调用 on_audio_chunk(chunk, sentence)。

    Args:
        token_stream: 上游文本 token 可迭代对象。
        on_audio_chunk: 用户提供的 chunk 回调，签名为 (audio_chunk, source_sentence)。
        api_key: DashScope API Key。
        model: TTS 模型名称。
        api_base_url: DashScope API 基础地址。
        sentence_punctuations: 分句标点集合。
        max_workers: 并发合成的最大线程数。
        ordered_output: 是否按句子顺序输出音频 chunk。
        max_buffer_length: 无标点时的强制切句长度。
        voice: 朗读音色。
    """
    if on_audio_chunk is None:
        raise ValueError("on_audio_chunk 不能为空")

    if max_workers <= 0:
        raise ValueError("max_workers 必须大于 0")

    errors: list[Exception] = []

    def _collect_sentence_audio(sentence: str) -> tuple[str, list[bytes]]:
        audio_chunks: list[bytes] = []
        for audio_chunk in stream_tts_audio_chunks(
            sentence=sentence,
            api_key=api_key,
            model=model,
            voice=voice,
            api_base_url=api_base_url,
        ):
            audio_chunks.append(audio_chunk)
        return sentence, audio_chunks

    def _stream_sentence_audio(sentence: str) -> None:
        try:
            for audio_chunk in stream_tts_audio_chunks(
                sentence=sentence,
                api_key=api_key,
                model=model,
                voice=voice,
                api_base_url=api_base_url,
            ):
                print("[TTS] sending chunk to player")
                on_audio_chunk(audio_chunk, sentence)
        except Exception as exc:
            print(f"TTS thread error: {exc}")
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for sentence in iter_sentences_from_token_stream(
            token_stream=token_stream,
            sentence_punctuations=sentence_punctuations,
            flush_tail=True,
            max_buffer_length=max_buffer_length,
        ):
            if ordered_output:
                futures.append(executor.submit(_collect_sentence_audio, sentence))
            else:
                futures.append(executor.submit(_stream_sentence_audio, sentence))

        if ordered_output:
            for future in futures:
                try:
                    sentence, audio_chunks = future.result()
                    for audio_chunk in audio_chunks:
                        on_audio_chunk(audio_chunk, sentence)
                except Exception as exc:
                    errors.append(exc)
        else:
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    errors.append(exc)

    if errors:
        raise RuntimeError(f"流式 TTS 执行失败：{errors[0]}")
