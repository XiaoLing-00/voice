#!/usr/bin/env python
"""
音频设备选择测试脚本
验证 VoiceRecorder 的设备选择逻辑
"""

import os
import sys

# 添加当前路径到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("音频设备选择测试")
print("=" * 60)

try:
    from service.voice import VoiceRecorder
    import sounddevice as sd

    print("\n[1] 列出所有音频设备:")
    devices = sd.query_devices()
    for i, device in enumerate(devices):
        name = device.get('name', 'Unknown')
        input_channels = device.get('max_input_channels', 0)
        output_channels = device.get('max_output_channels', 0)
        print(f"  ID {i}: {name} (输入:{input_channels}, 输出:{output_channels})")

    print("\n[2] 测试自动设备选择:")
    recorder = VoiceRecorder()
    try:
        selected_id, selected_device = recorder._select_audio_device()
        print(f"  ✓ 选择成功: {selected_device.get('name', 'Unknown')} (ID: {selected_id})")
    except Exception as e:
        print(f"  ✗ 选择失败: {e}")

    print("\n[3] 测试手动指定设备:")
    # 测试手动指定设备ID（如果存在的话）
    if len(devices) > 0:
        test_id = 0  # 测试第一个设备
        recorder_manual = VoiceRecorder(device_id=test_id)
        try:
            selected_id, selected_device = recorder_manual._select_audio_device()
            print(f"  ✓ 手动指定成功: {selected_device.get('name', 'Unknown')} (ID: {selected_id})")
        except Exception as e:
            print(f"  ✗ 手动指定失败: {e}")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)

except ImportError as e:
    print(f"导入失败：{e}")
    sys.exit(1)