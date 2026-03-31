import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave

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
    def __init__(self):
        self.done = False
        self.result = None
        self.error = None

    def set_result(self, result):
        self.result = result
        self.done = True

    def set_error(self, error):
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

    def __init__(self, device_id=None):
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
        max_amplitude = 0.0
        silent_start = None
        start = time.time()

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
                current_amplitude = float(np.max(np.abs(audio_chunk)))
                max_amplitude = max(max_amplitude, current_amplitude)

                # VAD：持续 2 秒静音自动停止
                if current_amplitude < 500:
                    if silent_start is None:
                        silent_start = time.time()
                    elif time.time() - silent_start >= 2.0:
                        print("[DEBUG] 识别到2秒静音，自动停止录音")
                        break
                else:
                    silent_start = None

        except Exception as e:
            self._recording = False
            stream.stop_stream()
            stream.close()
            raise RuntimeError("录音过程中发生异常：" + str(e))
        finally:
            stream.stop_stream()
            stream.close()

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

        # 最短时长校验
        if len(audio_data) < samplerate * 0.3:
            raise RuntimeError("录音时长过短，请重新录入（至少 0.3 秒）")

        # 振幅校验
        if max_amplitude < 0.001:
            raise RuntimeError(
                f"麦克风无输入信号（最大振幅：{max_amplitude:.6f}），"
                "请检查麦克风是否静音或未正确连接"
            )

        # 音量校验
        rms = float(np.sqrt(np.mean(audio_data.astype("float64") ** 2)))
        if rms < 0.01:
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


def record_and_stt(duration: int = 8, device_id=None):
    recorder = VoiceRecorder(device_id=device_id)
    try:
        audio_path, duration_sec = recorder.record(duration)
        compressed_audio_path = recorder.compress_audio(audio_path, target_format='mp3', bitrate='64k')

        async_result = AsyncASRResult()

        def _asr_task():
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
        pass


def transcribe(mp3_path: str) -> VoiceResult:
    client = STTClient()
    return client.analyze(mp3_path)
