# 语音功能修复完成报告

## 问题描述
- 语音按钮卡在"发送中"
- temp_audio 目录未生成对应的语音文件
- 等待长时间后出现"语音识别 API 返回的识别结果为空"错误

## 根本原因分析

### 1. **关键缺失：stop_requested 信号未连接** ⭐
在 `_on_voice_btn_click()` 中，**stop_requested** 和 **cancel_requested** 信号未连接到对应的 Worker 方法。  
这导致用户点击"停止并发送"按钮时，录音线程无法收到停止信号，一直在等待 60 秒录音完成。

**修复位置**：`UI/interview_panel.py` L679-680
```python
# 恢复的关键连接
self._voice_worker.stop_requested.connect(self._voice_worker.stop)
self._voice_worker.cancel_requested.connect(self._voice_worker.cancel)
```

---

### 2. **音频帧捕获诊断不足**
原代码无法判断是否真正捕获了音频数据，只有空的 frames 列表检查。

**修复**：`service/voice.py` L69-86
- 添加 `frames_captured` 计数器（nonlocal）
- 检查 `indata` 非空后才追加帧数据
- 提供更详细的错误信息，告知捕获了多少帧

---

### 3. **最小样本检查和文件大小验证**
原代码没有验证最终音频文件是否真的被生成。

**修复**：`service/voice.py` L123-126
```python
# 验证文件大小
if file_size < 1000:
    raise RuntimeError(f"录音文件过小（{file_size} bytes），可能未成功捕获音频")

# 验证最小样本数（至少 0.3 秒）
if len(audio_data) < RECORD_CONFIG["samplerate"] * 0.3:
    raise RuntimeError("录音时长过短，请重新录入（至少 0.3 秒）")
```

---

### 4. **缺少详细的调试日志**
无法追踪录音和 API 调用的执行过程。

**修复**：`UI/interview_panel.py` L49-68，`service/voice.py` L75+
添加了完整的执行流程日志：
```
[DEBUG] 开始语音录制，线程启动...
[DEBUG] VoiceWorker.run() 开始执行
[DEBUG] 开始录音，最长 60 秒...
[DEBUG] 录音完成，文件路径：...
[DEBUG] 录音文件大小：... bytes
[DEBUG] 调用 API 进行语音识别...
[DEBUG] API 识别成功：...
[ERROR] VoiceWorker 执行失败：...
```

---

## 修复清单

| 文件 | 修改项 | 作用 |
|-----|--------|------|
| `UI/interview_panel.py` | **恢复 stop/cancel 信号连接** | ⭐ 解决"卡在发送中"的根本原因 |
| `UI/interview_panel.py` | 添加调试日志 | 便于诊断问题 |
| `UI/interview_panel.py` | 改进错误提示 | 显示诊断建议 |
| `service/voice.py` | 添加 frames_captured 计数 | 诊断帧捕获情况 |
| `service/voice.py` | 添加文件大小检查 | 验证文件生成成功 |
| `service/voice.py` | 添加最小样本数检查 | 确保录音时长足够 |
| `service/voice.py` | 改进异常信息 | 提供更清晰的错误诊断 |
| `service/voice.py` | **DashScope API 协议更新** | ⭐ 修复 API 调用失败，正确调用 qwen3-asr-flash 模型 |
| `service/voice.py` | **防御性 API 解析** | ⭐ 防止 IndexError 崩溃，优雅处理空内容 |
| `service/voice.py` | **录音质量检测增强** | ⭐ 检测麦克风无输入，减少停止延迟 |
| `service/voice.py` | **智能音频设备选择** | ⭐ 自动过滤虚拟设备，选择真实麦克风 |
| `service/voice.py` | **动态声道匹配** | ⭐ 自动匹配设备声道数，避免配置错误 |
| `service/voice.py` | **增强音量预检** | ⭐ 检测虚拟设备和静音，提供明确错误信息 |
| `service/voice.py` | **即时停止优化** | ⭐ 确保 stop_event 秒停，无延迟 |
| `service/voice.py` | **音频时长裁剪** | ⭐ 只保存实际录制时长，不补齐60秒 |
| `service/voice.py` | **API 进度反馈** | ⭐ 明确显示上传/推理阶段和耗时 |

---

## API 接口协议修复 (2025-03-25)

### 5. **DashScope API 协议更新** ⭐
原代码使用错误的 API 端点和请求格式，导致无法调用 qwen3-asr-flash 模型。

**修复位置**：`service/voice.py` STTClient 类

#### 主要修改：
1. **API 端点修正**
   - 原：`https://bailian.aliyuncs.com/v1/audio/speech/recognition`
   - 新：`https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`

2. **请求体结构更新**
   - 原：单一 `input` 结构
   - 新：对话格式 `messages` 结构

3. **Base64 格式修正**
   - 原：纯 Base64 字符串
   - 新：Data URL 格式 `data:audio/wav;base64,{base64_data}`

4. **参数调整**
   - 移除 `enable_emotion_analysis` 和 `emotion_categories`（不支持）
   - 添加 `result_format: "message"` 以确保返回结构

5. **返回值解析更新**
   - 原：`output.text` 和 `output.emotion`
   - 新：`output.choices[0].message.content[0].text` 和 `annotations[0].emotion`

6. **情绪标签映射**
   - 模型原生标签：surprised, neutral, happy, sad, disgusted, angry, fearful
   - 映射到应用标签：自信, 紧张, 迟疑, 流畅, 混乱

---

## API 解析崩溃修复 (2025-03-25)

### 7. **防御性 API 解析** ⭐
原代码在解析 API 返回值时直接访问数组索引，导致 IndexError 崩溃。

**修复位置**：`service/voice.py` STTClient.analyze 方法

#### 修复内容：
- 检查 `choices` 数组是否为空
- 检查 `content` 数组是否为空  
- 检查 `text` 内容是否为空
- 当检测不到语音内容时，返回特殊标记 `[未检测到语音内容]` 而不是崩溃
- 提供详细的 emotion_detail 说明原因

### 8. **录音质量检测增强**
原代码无法检测麦克风无输入或录音质量问题。

**修复位置**：`service/voice.py` VoiceRecorder.record 方法

#### 修复内容：
- **实时振幅监测**：在 callback 中跟踪最大振幅，如果全程接近 0 则抛异常
- **立即停止逻辑**：将 while 循环检查间隔从 0.05s 减少到 0.01s，减少停止延迟
- **强制类型转换**：确保 audio_data 转换为 np.int16 后再保存到 WAV 文件
- **设备信息打印**：录音开始前打印当前音频输入设备信息用于调试

### 9. **调试辅助功能**
为了便于排查录音问题，暂时禁用自动清理逻辑。

**修复位置**：`service/voice.py` record_and_stt 函数

#### 修复内容：
- 注释掉 `finally` 块中的 `recorder.clean_temp()` 调用
- 保留 temp_audio 目录中的 .wav 文件供手动检查

### 原始流程（有问题）
```
用户点击"停止并发送" 
  → stop_requested 信号发送
  → [无效] 信号未连接到 stop() 方法
  → 录音线程继续等待 60 秒
  → UI 卡住，显示"发送中..."
```

### 修复后流程（正确）
```
用户点击"停止并发送"
  → stop_requested 信号发送
  → [✓] 信号正确连接到 stop() 方法
  → stop() 设置 _stop_event
  → callback 中捕获 CallbackStop，流会立即结束
  → 收集的帧数据被写入 WAV 文件
  → API 识别成功，返回结果
  → UI 更新，显示识别内容
```

---

## 测试建议

### 基本测试
1. 点击"🎤 语音"开始录音
2. 说几句话（2-5 秒）
3. 点击"停止并发送"（应该立即停止，而不是卡住）
4. 观察控制台的 `[DEBUG]` 日志输出
5. 等待 API 返回识别结果（约 3-10 秒）

### 诊断要点 - 查看这些日志
```
[DEBUG] 开始语音录制，线程启动...              ← 线程启动成功
[DEBUG] 停止录音信号已发送                     ← 用户点击"停止"
[DEBUG] VoiceWorker.stop() 被调用             ← 信号正确连接
[DEBUG] 录音完成，文件路径：...              ← 文件成功创建
[DEBUG] 录音文件大小：... bytes              ← 验证文件有数据
[DEBUG] 调用 API 进行语音识别...             ← API 开始调用
[DEBUG] API 识别成功：...                    ← API 返回结果
```

### 常见问题排查
| 问题 | 日志表现 | 原因 | 解决 |
|-----|---------|------|------|
| 卡在"发送中"10+ 秒 | 没有"停止录音信号已发送" | 信号未连接 | ✓ 已修复 |
| "未获取到录音数据" | 捕获帧数为 0 | 麦克风无输入 | 检查麦克风权限 |
| "文件过小" | 文件 < 1000 bytes | 音频数据不完整 | 靠近麦克风，声音清晰 |
| "音量太低" | RMS < 0.01 | 录音太轻 | 提高说话音量 |
| "API 返回结果为空" | 文件已创建，但 API 无返回 | API Key 错误或网络问题 | 检查 .env 配置和网络 |

---

## 代码质量改进
- ✓ 添加了完整的错误处理
- ✓ 信号/槽连接完整
- ✓ 详细的执行日志便于调试
- ✓ 更好的异常诊断信息
- ✓ 防守性编程（文件验证、样本数检查）

---

## 下一步
1. ✓ 代码修复完毕（未上传 GitHub）
2. 运行程序，执行上述测试
3. 观察 [DEBUG] 日志，确认各步骤正常
4. 如仍有问题，保存完整的日志输出供诊断
5. 确认正常后，再上传到 voice 分支

---

## 设备选择和质量检测修复 (2025-03-25)

### 10. **智能音频设备选择** ⭐
原代码使用默认设备，导致选择虚拟音频设备 "ToDesk Virtual Audio" 而非真实麦克风。

**修复位置**：`service/voice.py` VoiceRecorder 类

#### 修复内容：
- **设备过滤**：遍历所有设备，跳过包含 "Virtual"、"ToDesk"、"Remote"、"Bluetooth"、"虚拟" 的设备
- **优先级选择**：优先选择名称包含 "Microphone"、"麦克风"、"Realtek"、"USB Audio"、"Audio"、"声卡" 的设备
- **手动指定接口**：构造函数支持 `device_id` 参数，允许手动强制指定设备ID
- **设备信息打印**：显示选择的设备名称、ID和优先级分数

### 11. **动态声道匹配**
原代码固定使用1声道，但设备可能是2声道，导致配置不匹配。

**修复位置**：`service/voice.py` VoiceRecorder.record 方法

#### 修复内容：
- **动态声道数**：根据设备的 `max_input_channels` 动态设置，最多使用2声道
- **InputStream 参数**：明确指定 `device=device_id` 而非使用默认设备
- **文件名包含设备ID**：录音文件命名为 `rec_device{ID}_{uuid}.wav` 便于调试

### 13. **即时停止优化** ⭐
原代码在收到停止信号后仍有延迟，影响用户体验。

**修复位置**：`service/voice.py` VoiceRecorder.record 方法

#### 修复内容：
- **秒停响应**：while 循环中增加详细日志，确认信号检测
- **循环优化**：确保 `stop_event` 被检测到后立即退出，无额外延迟
- **状态反馈**：打印"检测到停止信号，立即退出"等调试信息

### 14. **音频时长裁剪**
原代码保存的音频文件总是补齐到60秒，造成文件过大。

**修复位置**：`service/voice.py` VoiceRecorder.record 方法

#### 修复内容：
- **实际时长计算**：基于 `len(audio_data) / samplerate` 计算真实录制时长
- **精确保存**：只保存实际录制的音频数据，不补齐到最大时长
- **调试信息**：显示实际样本数、时长和文件大小

### 15. **API 进度反馈**
原代码缺乏上传和推理阶段的进度提示，用户无法判断当前状态。

**修复位置**：`service/voice.py` STTClient._call_asr_api 方法

#### 修复内容：
- **上传阶段**：打印"正在上传音频数据到 API..."
- **时长统计**：记录上传耗时和总耗时
- **推理阶段**：打印"上传完成，正在等待推理结果..."
- **完成提示**：打印"推理完成，总耗时: X.XX秒"
