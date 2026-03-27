#!/usr/bin/env python
"""
录音停止和时长裁剪测试脚本
验证 VoiceRecorder 的即时停止和音频裁剪功能
"""

import os
import sys
import time
import threading

# 添加当前路径到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("录音停止和时长裁剪测试")
print("=" * 60)

try:
    from service.voice import VoiceRecorder

    print("\n[1] 创建录音器实例:")
    recorder = VoiceRecorder()
    print("  ✓ VoiceRecorder 创建成功")

    print("\n[2] 测试设备选择:")
    try:
        device_id, device = recorder._select_audio_device()
        print(f"  ✓ 选择设备: {device.get('name', 'Unknown')} (ID: {device_id})")
    except Exception as e:
        print(f"  ✗ 设备选择失败: {e}")
        sys.exit(1)

    print("\n[3] 测试录音停止功能:")
    print("  启动录音线程 (5秒后自动停止)...")

    stop_called = False

    def auto_stop():
        nonlocal stop_called
        time.sleep(2)  # 2秒后停止
        print("  [模拟] 用户点击停止按钮")
        recorder.stop()
        stop_called = True

    # 启动自动停止线程
    stop_thread = threading.Thread(target=auto_stop, daemon=True)
    stop_thread.start()

    try:
        start_time = time.time()
        audio_path = recorder.record(duration=10)  # 设置10秒最大时长
        end_time = time.time()

        actual_duration = end_time - start_time
        print(".2f"        print(f"  ✓ 录音文件: {audio_path}")
        print(f"  ✓ 文件大小: {os.path.getsize(audio_path)} bytes")
        print(f"  ✓ 停止信号是否调用: {stop_called}")

        # 验证文件是否只包含实际录制时长
        import wave
        with wave.open(audio_path, 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            channels = wf.getnchannels()
            file_duration = frames / float(rate)
            print(".2f"            print(f"  ✓ 声道数: {channels}")

    except Exception as e:
        print(f"  ✗ 录音测试失败: {e}")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)
    print("\n预期结果:")
    print("- 录音时长应接近2秒 (而不是10秒)")
    print("- 控制台应显示停止信号检测日志")
    print("- 文件大小应与实际时长成正比")

except ImportError as e:
    print(f"导入失败：{e}")
    sys.exit(1)