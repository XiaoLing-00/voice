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
        self.device_id = device_id  # 允许手动指定设备ID

        self._frames = []
        self._recording = False
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()

    def _select_audio_device(self):
        """自动选择合适的音频输入设备，过滤虚拟设备"""
        try:
            devices = sd.query_devices()
            
            # 如果手动指定了设备ID，直接使用
            if self.device_id is not None:
                if 0 <= self.device_id < len(devices):
                    device_info = devices[self.device_id]
                    if device_info.get('max_input_channels', 0) > 0:
                        print(f"[DEBUG] 使用手动指定设备: {device_info.get('name', 'Unknown')} (ID: {self.device_id})")
                        return self.device_id, device_info
                print(f"[DEBUG] 手动指定的设备ID {self.device_id} 无效")
            
            # 自动选择设备：优先级关键词
            priority_keywords = ['Microphone', '麦克风', 'Realtek', 'USB Audio', 'Audio', '声卡']
            virtual_keywords = ['Virtual', 'ToDesk', 'Remote', 'Bluetooth', '虚拟']
            
            candidates = []
            
            for i, device in enumerate(devices):
                name = device.get('name', '').lower()
                max_input_channels = device.get('max_input_channels', 0)
                
                # 跳过无输入声道的设备
                if max_input_channels <= 0:
                    continue
                
                # 跳过明显是虚拟设备的
                is_virtual = any(vk.lower() in name for vk in virtual_keywords)
                if is_virtual:
                    print(f"[DEBUG] 跳过虚拟设备: {device.get('name', 'Unknown')} (ID: {i})")
                    continue
                
                # 计算优先级分数
                priority_score = 0
                for keyword in priority_keywords:
                    if keyword.lower() in name:
                        priority_score += 1
                
                candidates.append((i, device, priority_score))
            
            if not candidates:
                raise RuntimeError("未找到可用的音频输入设备")
            
            # 按优先级排序，选择最高分的设备
            candidates.sort(key=lambda x: x[2], reverse=True)
            selected_id, selected_device, score = candidates[0]
            
            print(f"[DEBUG] 自动选择设备: {selected_device.get('name', 'Unknown')} (ID: {selected_id}, 优先级: {score})")
            print(f"[DEBUG] 设备信息: 声道={selected_device.get('max_input_channels', 0)}, 采样率={RECORD_CONFIG['samplerate']}")
            
            return selected_id, selected_device
            
        except Exception as e:
            print(f"[DEBUG] 设备选择失败: {e}")
            raise RuntimeError(f"无法选择音频设备: {e}")

    def record(self, duration: int = 60) -> tuple[str, float]:
        """录制语音最长60秒，可在外部调用 stop() 立即结束。

        返回: (音频路径, 录音时长秒数)
        """
        if duration <= 0 or duration > 60:
            raise ValueError("录音时长必须在1-60秒之间")

        # 选择合适的音频设备
        selected_device_id, selected_device = self._select_audio_device()
        device_channels = selected_device.get('max_input_channels', 1)
        
        # 动态调整声道配置
        actual_channels = min(device_channels, 2)  # 最多使用2声道
        print(f"[DEBUG] 使用声道数: {actual_channels} (设备支持: {device_channels})")

        self._frames = []
        self._stop_event.clear()
        self._cancel_event.clear()
        
        start = time.time()  # 在最外层记录开始时间
        # 文件名包含设备ID，方便调试
        out_wav = os.path.join(self.temp_dir, f"rec_device{selected_device_id}_{uuid.uuid4()}.wav")

        self._recording = True
        frames_captured = 0
        max_amplitude = 0.0  # 跟踪最大振幅
        silent_start = None  # 静音起始时间

        def callback(indata, frames, time_info, status):
            nonlocal frames_captured, max_amplitude, silent_start
            
            # 记录状态，但不中止（某些状态是正常的）
            if status and not status == sd.CallbackFlags.none:
                pass

            if self._cancel_event.is_set():
                raise sd.CallbackAbort
            if self._stop_event.is_set():
                raise sd.CallbackStop

            # 确保捕获正确数据
            if indata is not None and len(indata) > 0:
                self._frames.append(indata.copy())
                frames_captured += len(indata)
                
                # 计算当前块的最大振幅
                current_amplitude = np.max(np.abs(indata.astype('float64')))
                max_amplitude = max(max_amplitude, current_amplitude)

                # VAD: 持续2秒静音自动stop
                if current_amplitude < 500:  # 0-32767 量化空间，阈值可调
                    if silent_start is None:
                        silent_start = time.time()
                    elif time.time() - silent_start >= 2.0:
                        print("[DEBUG] 识别到2秒静音，自动停止录音")
                        self._stop_event.set()
                        raise sd.CallbackStop
                else:
                    silent_start = None

        try:
            stream = sd.InputStream(
                device=selected_device_id,  # 明确指定设备ID
                samplerate=RECORD_CONFIG["samplerate"],
                channels=actual_channels,  # 动态声道数
                dtype=RECORD_CONFIG["dtype"],
                callback=callback,
                blocksize=0,  # 使用默认块大小
            )
            with stream:
                print(f"[DEBUG] 开始录音，最大时长: {duration}秒")
                # 更高频率的检查（每 50ms 检查一次停止信号）
                while time.time() - start < duration:
                    if self._cancel_event.is_set():
                        print("[DEBUG] 检测到取消信号，立即关闭流")
                        stream.stop()
                        stream.close()
                        break
                    if self._stop_event.is_set():
                        print("[DEBUG] 检测到停止信号，立即关闭流")
                        stream.stop()
                        stream.close()
                        break
                    time.sleep(0.05)  # 更短的睡眠 50ms = 更快的响应
                
                actual_wall_clock = time.time() - start
                print(f"[DEBUG] 从开始到停止: {actual_wall_clock:.3f}秒")
                
                # 确保流正确关闭
                if not stream.active:
                    print("[DEBUG] 流已自动停止")
                elif not self._cancel_event.is_set() and not self._stop_event.is_set():
                    self._stop_event.set()
                    stream.stop()
                    
        except sd.CallbackAbort:
            self._recording = False
            raise RuntimeError("录音已取消")
        except Exception as e:
            self._recording = False
            raise RuntimeError("麦克风录制失败，请检查设备是否连接：" + str(e))

        self._recording = False
        actual_duration = time.time() - start
        print(f"[DEBUG] 录音完成 - 总耗时: {actual_duration:.3f}秒, 捕获帧数: {frames_captured}")

        if self._cancel_event.is_set():
            raise RuntimeError("录音已取消")

        if not self._frames or frames_captured == 0:
            raise RuntimeError(f"未获取到录音数据（捕获帧数：{frames_captured}），请重试")

        # 检查最大振幅，如果接近0说明麦克风无输入或使用虚拟设备
        if max_amplitude < 0.001:  # 非常小的阈值
            raise RuntimeError(f"麦克风无输入信号（最大振幅：{max_amplitude:.6f}），当前使用的是虚拟设备或麦克风静音")

        audio_data = np.concatenate(self._frames, axis=0)
        # 尽早释放分块缓冲，避免在线程回收阶段集中释放导致卡顿
        self._frames = []

        # 检查数据样本数
        if len(audio_data) < RECORD_CONFIG["samplerate"] * 0.3:  # 最少 0.3 秒
            raise RuntimeError("录音时长过短，请重新录入（至少 0.3 秒）")

        # 自动检测录音音量
        rms = np.sqrt(np.mean(audio_data.astype('float64') ** 2))
        if rms < 0.01:
            raise RuntimeError("录音音量太低，请靠近麦克风重试")

        # 确保目录存在
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # 计算实际录制时长（基于采集的音频样本数，而非wall-clock time）
        actual_samples = len(audio_data)
        actual_duration_sec = actual_samples / RECORD_CONFIG["samplerate"]
        print(f"[DEBUG] 音频数据: {actual_samples} 样本 = {actual_duration_sec:.3f}秒")
        
        # 强制类型转换并保存（只保存实际录制的时长）
        with wave.open(out_wav, "wb") as wf:
            wf.setnchannels(actual_channels)  # 使用实际声道数
            wf.setsampwidth(2)
            wf.setframerate(RECORD_CONFIG["samplerate"])
            # 确保数据是 int16 类型
            audio_int16 = audio_data.astype(np.int16)
            wf.writeframes(audio_int16.tobytes())

        # 将临时文件移动到 output_audio 作为待发送语音条
        dest_wav = os.path.join(self.output_dir, f"voice_{uuid.uuid4()}.wav")
        shutil.move(out_wav, dest_wav)

        print(f"[DEBUG] 音频文件已保存并迁移: {dest_wav} ({os.path.getsize(dest_wav)} bytes)")
        self.temp_file = dest_wav
        return self.temp_file, actual_duration_sec

    def compress_audio(self, wav_path: str, target_format: str = 'mp3', bitrate: str = '64k') -> str:
        """将已保存的 wav 音频压缩为 mp3/aac，并返回新文件路径。"""
        from pydub import AudioSegment

        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"待压缩音频文件不存在：{wav_path}")

        if target_format not in ['mp3', 'aac']:
            raise ValueError('仅支持 mp3/aac 两种压缩格式')

        out_path = os.path.splitext(wav_path)[0] + f'.{target_format}'
        audio = AudioSegment.from_wav(wav_path)

        # 使用高压缩比以生成微信风格语音条
        audio.export(out_path, format=target_format, bitrate=bitrate)
        return out_path

    def _try_convert_wav_to_mp3(self, wav_path: str, mp3_path: str) -> bool:
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_wav(wav_path)
            audio.export(mp3_path, format="mp3")
            return True
        except Exception:
            # 尝试本地 ffmpeg
            if shutil.which("ffmpeg"):
                try:
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            wav_path,
                            "-codec:a",
                            "libmp3lame",
                            "-qscale:a",
                            "2",
                            mp3_path,
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True
                except Exception:
                    return False
    def stop(self):
        if self._recording:
            self._stop_event.set()

    def cancel(self):
        if self._recording:
            self._cancel_event.set()

    def clean_temp(self):
        """清理临时音频文件，异常不中断程序流程"""
        # 仅清理 temp_audio 下的旧临时文件；output_audio/recordings 中的“待发送语音条”不自动删除
        self.temp_file = None

        # 清理临时目录中的旧文件（超过50个或超过1小时未修改的文件）
        try:
            if os.path.exists(self.temp_dir):
                files = [
                    os.path.join(self.temp_dir, f)
                    for f in os.listdir(self.temp_dir)
                    if f.startswith("rec_") and f.endswith(".wav")
                ]
                
                # 如果文件过多，删除最旧的文件
                if len(files) > 50:
                    files_with_time = [
                        (f, os.path.getmtime(f)) for f in files
                    ]
                    files_with_time.sort(key=lambda x: x[1])
                    # 保留最新的50个文件
                    for f, _ in files_with_time[:-50]:
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                
                # 删除超过1小时未修改的文件
                current_time = time.time()
                for f in files:
                    try:
                        if current_time - os.path.getmtime(f) > 3600:  # 1小时
                            os.remove(f)
                    except OSError:
                        pass
        except Exception:
            # 目录清理失败不影响程序
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
