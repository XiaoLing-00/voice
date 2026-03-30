import pyaudio


def list_audio_devices():
    p = pyaudio.PyAudio()
    print(f"找到 {p.get_device_count()} 个音频设备:\n")

    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        # 只打印输入设备 (麦克风)
        if info['maxInputChannels'] > 0:
            print(f"索引 {i}: {info['name']} (Channels: {info['maxInputChannels']})")

    p.terminate()


if __name__ == "__main__":
    try:
        list_audio_devices()
    except Exception as e:
        print(f"错误：{e}")
        print("提示：如果报错，可能未安装 pyaudio 或驱动异常")