"""音频录制与压缩。

VoiceRecorder 只负责：
- 通过 pyaudio 录制单声道 WAV
- 可选压缩为 MP3 等格式
- 管理自己的 PyAudio 实例生命周期
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
import wave
from typing import Any

import numpy as np
import pyaudio

from ..config import OUTPUT_AUDIO_DIR, RECORD_CONFIG, RECORDINGS_DIR


class VoiceRecorder:
    """音频录制类，支持最长 60 秒、取消、立即发送（中断）。"""

    def __init__(self, device_id: int | None = None) -> None:
        self.temp_dir = os.path.abspath(os.path.join(os.getcwd(), "temp_audio"))
        os.makedirs(self.temp_dir, exist_ok=True)

        self.output_dir = OUTPUT_AUDIO_DIR
        os.makedirs(self.output_dir, exist_ok=True)

        self.recordings_dir = RECORDINGS_DIR
        os.makedirs(self.recordings_dir, exist_ok=True)

        self.temp_file:  str | None = None
        self.device_id:  int | None = device_id

        self._frames:       list[bytes] = []
        self._recording:    bool        = False
        self._stop_event   = threading.Event()
        self._cancel_event = threading.Event()

        self._pa = pyaudio.PyAudio()

    # ── 内部工具 ────────────────────────────────────────────────────────────

    @staticmethod
    def _close_stream(stream: Any | None) -> None:
        if stream is None:
            return
        for method in (stream.stop_stream, stream.close):
            try:
                method()
            except Exception:
                pass

    @staticmethod
    def _audio_metrics(audio_data: np.ndarray) -> tuple[float, float]:
        """返回 (peak, rms)，均为 [-1, 1] 归一化范围。"""
        if audio_data.size == 0:
            return 0.0, 0.0
        normalized = audio_data.astype(np.float32) / 32768.0
        peak = float(np.max(np.abs(normalized)))
        rms  = float(np.sqrt(np.mean(np.square(normalized))))
        return peak, rms

    # ── 公开接口 ────────────────────────────────────────────────────────────

    def record(self, duration: int = 60) -> tuple[str, float]:
        """录制语音，最长 60 秒，外部可调用 stop() 提前结束。

        Returns:
            (音频文件路径, 实际录音秒数)
        """
        if not (1 <= duration <= 60):
            raise ValueError("录音时长必须在 1-60 秒之间")

        chunk      = 1024
        samplerate = RECORD_CONFIG["samplerate"]
        channels   = 1

        self._frames = []
        self._stop_event.clear()
        self._cancel_event.clear()
        self._recording = True

        silent_start: float | None = None
        start  = time.time()
        stream: Any | None = None

        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=samplerate,
                input=True,
                input_device_index=self.device_id,
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

                chunk_arr = np.frombuffer(data, dtype=np.int16)
                peak, _   = self._audio_metrics(chunk_arr)

                silence_threshold = 500.0 / 32768.0
                if peak < silence_threshold:
                    if silent_start is None:
                        silent_start = time.time()
                    elif time.time() - silent_start >= 2.0:
                        print("[DEBUG] 识别到2秒静音，自动停止录音")
                        break
                else:
                    silent_start = None

        except Exception as e:
            self._recording = False
            self._close_stream(stream)
            raise RuntimeError("录音过程中发生异常：" + str(e))
        finally:
            self._close_stream(stream)

        actual_duration = time.time() - start
        self._recording  = False
        print(f"[DEBUG] 录音完成 - 耗时: {actual_duration:.3f}秒, 块数: {len(self._frames)}")

        if self._cancel_event.is_set():
            self._frames = []
            raise RuntimeError("录音已取消")

        if not self._frames:
            raise RuntimeError("未获取到录音数据，请重试")

        audio_data   = np.frombuffer(b"".join(self._frames), dtype=np.int16)
        self._frames = []
        peak, rms    = self._audio_metrics(audio_data)

        if len(audio_data) < samplerate * 0.3:
            raise RuntimeError("录音时长过短，请重新录入（至少 0.3 秒）")
        if peak < 500.0 / 32768.0:
            raise RuntimeError(
                f"麦克风无输入信号（最大振幅：{peak:.6f}），"
                "请检查麦克风是否静音或未正确连接"
            )
        if rms < 0.003:
            raise RuntimeError("录音音量太低，请靠近麦克风重试")

        actual_duration_sec = len(audio_data) / samplerate
        print(f"[DEBUG] 音频数据: {len(audio_data)} 样本 = {actual_duration_sec:.3f}秒")

        out_wav = os.path.join(self.temp_dir, f"rec_{uuid.uuid4()}.wav")
        os.makedirs(self.temp_dir, exist_ok=True)
        with wave.open(out_wav, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(samplerate)
            wf.writeframes(audio_data.tobytes())

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
        """将 WAV 压缩为目标格式，默认输出 MP3。"""
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

        fmt = target_format.lstrip(".").lower().strip()
        if not fmt:
            raise ValueError("target_format 不能为空")

        try:
            segment = AudioSegment.from_file(audio_path)
        except Exception as exc:
            raise RuntimeError(f"读取待压缩音频失败：{exc}") from exc

        output_path    = os.path.join(self.output_dir, f"voice_{uuid.uuid4()}.{fmt}")
        export_kwargs: dict[str, Any] = {"format": fmt}
        if fmt == "mp3":
            export_kwargs["bitrate"] = bitrate

        try:
            segment.export(output_path, **export_kwargs)
        except Exception as exc:
            raise RuntimeError(f"音频压缩失败，请检查 ffmpeg 是否可用：{exc}") from exc

        return output_path

    # ── 控制接口 ────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """提前结束录音（保留已录数据）。"""
        if self._recording:
            self._stop_event.set()

    def cancel(self) -> None:
        """取消录音（丢弃已录数据）。"""
        if self._recording:
            self._cancel_event.set()

    def clean_temp(self) -> None:
        """清理临时录音文件（超出 50 个或超过 1 小时的旧文件）。"""
        self.temp_file = None
        try:
            if not os.path.exists(self.temp_dir):
                return
            files = [
                os.path.join(self.temp_dir, f)
                for f in os.listdir(self.temp_dir)
                if f.startswith("rec_") and f.endswith(".wav")
            ]
            if len(files) > 50:
                files.sort(key=os.path.getmtime)
                for f in files[:-50]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
            now = time.time()
            for f in files:
                try:
                    if now - os.path.getmtime(f) > 3600:
                        os.remove(f)
                except OSError:
                    pass
        except Exception:
            pass

    # ── 资源管理 ────────────────────────────────────────────────────────────

    def close(self) -> None:
        try:
            self._pa.terminate()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()
