import os

from dotenv import load_dotenv

load_dotenv()

# ── 录音参数 ──────────────────────────────────────────────────────────────────
RECORD_CONFIG = {
    "samplerate": 16000,
    "channels": 1,
    "dtype": "int16",
    "format": "wav",
}

# ── 目录 ──────────────────────────────────────────────────────────────────────
OUTPUT_AUDIO_DIR = os.path.abspath(os.path.join(os.getcwd(), "output_audio"))
RECORDINGS_DIR   = os.path.abspath(os.path.join(os.getcwd(), "recordings"))

# ── 阿里云 / DashScope ────────────────────────────────────────────────────────
ALIYUN_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# STT
ALIYUN_STT_API_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
    "multimodal-generation/generation"
)
ALIYUN_STT_MODEL = "qwen3-asr-flash"

# TTS
DEFAULT_TTS_API_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_TTS_MODEL        = "qwen3-tts-instruct-flash"
DEFAULT_TTS_VOICE        = "Elias"

# ── 情绪标签 ──────────────────────────────────────────────────────────────────
VALID_EMOTIONS: frozenset[str] = frozenset({"自信", "紧张", "迟疑", "流畅", "混乱"})
