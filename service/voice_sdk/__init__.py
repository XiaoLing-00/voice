"""voice_sdk 公开接口。

最常用的入口：
    from voice_sdk import record_and_stt, transcribe
    from voice_sdk import stream_interview_tts_from_tokens
    from voice_sdk import VoiceRecorder, StreamingAudioPlayer
    from voice_sdk.tts import TTSClient, DashScopeTTSClient   # 换模型时从这里入手
"""

from .audio.player import StreamingAudioPlayer
from .audio.recorder import VoiceRecorder
from .models import AsyncASRResult, RecordBundle, VoiceResult
from .stt.client import STTClient, record_and_stt, transcribe
from .tts.pipeline import AudioChunkCallback,DashScopeTTSClient,stream_interview_tts_from_tokens
from .tts.sentence_splitter import DEFAULT_SENTENCE_PUNCTUATIONS,iter_sentences_from_token_stream
from .tts.client import TTSClient
__all__ = [
    # 录音
    "VoiceRecorder",
    "StreamingAudioPlayer",
    # 识别
    "STTClient",
    "record_and_stt",
    "transcribe",
    # 合成
    "TTSClient",
    "DashScopeTTSClient",
    "stream_interview_tts_from_tokens",
    "iter_sentences_from_token_stream",
    "DEFAULT_SENTENCE_PUNCTUATIONS",
    "AudioChunkCallback",
    # 数据模型
    "VoiceResult",
    "RecordBundle",
    "AsyncASRResult",
]
