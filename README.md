# AI 模拟面试与能力提升平台

> 锐捷网络企业命题 · 开发者协作手册

![各种架构图](./graph.md)
---

## ⚠️ 注意事项

### 代码合并

提交没有同步的分支时，使用空提交处理（即 reject 掉贡献者的代码然后合并到 master）。

### 敏感信息误提交处理

**场景一：刚提交，还没 push**

```bash
git reset --soft HEAD~1
# 删掉 API key 后重新提交
git add .
git commit -m "remove api key"
```

**场景二：已经 push 到 GitHub**

```bash
git reset --soft HEAD~1
git push --force
```

---

## 目录

1. [项目总览](#1-项目总览)
2. [快速启动](#2-快速启动)
3. [目录结构](#3-目录结构)
4. [核心模块说明](#4-核心模块说明)
5. [工具权限体系](#5-工具权限体系)
6. [知识库配置](#6-知识库配置)
7. [语音功能](#7-语音功能)
8. [数据库结构](#8-数据库结构)
9. [开发规范](#9-开发规范)

---

## 1. 项目总览

### 1.1 赛题背景

本项目为「面试与能力提升软件」竞赛作品。

核心场景：
- 学生通过模拟面试练习技术岗位面试题
- AI 面试官从题库抽题 → 学生回答（支持文字/语音）→ 多维度评分 → 个性化提升建议
- 老师上传课程资料后，AI 可基于课程内容出题，实现「课程答辩式面试」

### 1.2 技术栈

| 层级 | 技术 |
|------|------|
| UI 框架 | PySide6（Qt for Python） |
| LLM 接入 | 阿里云百炼（DashScope），兼容 OpenAI SDK |
| 知识库 RAG | 阿里云百炼 RAG SDK / HTTP API |
| 工具框架 | LangChain `@tool` |
| 数据库 | SQLite（WAL 模式） |
| 语音 | PyAudio + 阿里云 ASR（qwen3-asr-flash） |

### 1.3 架构一览

```
main.py
├── InterviewPanel      → InterviewEngine（面试流程引擎）
│   ├── MarkovDecisionEngine（难度/意图决策）
│   ├── RAGService（课程知识库检索）
│   ├── DBConversation（会话持久化）
│   ├── AnswerEvaluator（LLM 评分）
│   └── Agent（流式生成 + 工具调用）
│
├── AgentPanel          → HelperEngine（AI 学习助手）
│   └── Agent（ASSISTANT_SKILLS 工具集）
│
├── HistoryPanel        → DatabaseManager（成长曲线 + 雷达图）
└── QuizPanel           → DatabaseManager（题库浏览，服务端分页）
```

### 1.4 面试流程

```
start_session()
    └── get_first_question_stream()   ← RAG 检索开场场景
          ↓
    submit_answer_stream()
          ├── AnswerEvaluator.evaluate()   → 推送 __EVAL__:{json}
          ├── MarkovDecisionEngine.decide_next_action()
          │     ├── DEEPEN  → 追问底层原理
          │     ├── CORRECT → 引导纠错
          │     ├── CLARIFY → 换角度重问
          │     ├── NEXT    → 换新题（调整难度）
          │     └── WRAPUP  → 推送 __IS_FINISHED__
          └── Agent 流式生成下一问
                ↓
    finish_session_stream()           → 推送 __SCORE__:{float} + 报告
```

---

## 2. 快速启动

### 2.1 环境要求

| 依赖 | 版本 | 备注 |
|------|------|------|
| Python | 3.11+ | 低版本缺少 `match/case`，不兼容 |
| PyAudio | 最新 | 语音录制，需系统已安装 PortAudio |

> ⚠️ `torch` 建议单独先装（如需本地语音处理）：
> ```bash
> pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
> pip install -r requirements.txt
> ```

### 2.2 安装步骤

```bash
# 1. 克隆仓库
git clone <repo_url>
cd <project_dir>

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少填入 DASHSCOPE_API_KEY

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动
python main.py
```

### 2.3 .env 配置

> ⚠️ **所有 Key 均不要提交 Git**，`.gitignore` 已包含 `.env`

```env
# ── 核心（必填）──────────────────────────────────────────────────────────────
DASHSCOPE_API_KEY="sk-xxx"

# ── 知识库 ID（在百炼控制台创建知识库后，复制 Index ID 填入）────────────────
# 技术知识库：Java/Spring/MySQL/Redis/前端等，AI 助手使用
TECH_KB_ID="xxxxxxxxxxxxxxxxx"
# 数据结构课程知识库：课程讲义/场景素材，面试引擎出题使用
DS_COURSE_KB_ID="xxxxxxxxxxxxxxxxx"

# ── 百炼官方 SDK 模式（三件套，比 HTTP 模式更稳定，建议配置）────────────────
BAILOU_WORKSPACE_ID="xxxxxxxxxxxxxxxxxxxx"
ALIBABA_CLOUD_ACCESS_KEY_ID="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
ALIBABA_CLOUD_ACCESS_KEY_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# ── 联网搜索（可选，不填则跳过 web_search 工具）─────────────────────────────
BOCHA_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TAVILY_API_KEY="xxxxxxxxxxxxxxxxxx"
```

**配置优先级：**

| 配置 | 效果 |
|------|------|
| 仅 `DASHSCOPE_API_KEY` | HTTP 模式启动，知识库工具自动跳过 |
| + 百炼三件套 | 切换官方 SDK 模式，更稳定 |
| + `TECH_KB_ID` | AI 助手启用知识库检索 |
| + `DS_COURSE_KB_ID` | 面试引擎启用课程场景增强 |
| + `BOCHA_API_KEY` | 启用联网搜索工具 |

---

## 3. 目录结构

```
.
├── main.py                          # 入口，组装所有服务和 UI 面板
│
├── UI/
│   ├── components.py                # 统一组件库（Theme、ChatBubble、ButtonFactory 等）
│   ├── interview_panel.py           # 模拟面试主界面（支持流式 + 语音）
│   ├── agent_panel.py               # AI 知识助手面板
│   ├── history_panel.py             # 成长曲线 + 雷达图
│   └── quiz_panel.py                # 题库练习（服务端分页）
│
├── service/
│   ├── db.py                        # SQLite 单例封装（WAL 模式）
│   ├── schema.py                    # 建表 + 种子数据初始化
│   ├── evaluator.py                 # LLM 答案评分器（4 维度）
│   ├── helper_engine.py             # AI 助手引擎（HelperEngine）
│   │
│   ├── agent_core/
│   │   ├── agent_core.py            # 通用 Agent（流式 + 工具调用 + SkillSet 注入）
│   │   ├── history_manage.py        # 对话历史管理（自动裁剪）
│   │   ├── tool_calling.py          # LangChain tool → OpenAI tools 格式转换
│   │   ├── load_prompt.py           # 从文件加载 Prompt
│   │   └── prompt/
│   │       ├── interview/           # 面试系统 Prompt、报告模板
│   │       └── helper/              # 助手系统 Prompt
│   │
│   ├── interview_engine_sdk/
│   │   ├── interview_engine.py      # 面试引擎编排器（Orchestrator）
│   │   ├── db_conversation.py       # 数据库交互层（Session/Turn 管理）
│   │   ├── rag_service.py           # RAG 检索服务封装
│   │   └── static_markov_asking.py  # 马尔可夫决策引擎（难度/意图调整）
│   │
│   ├── tools/
│   │   ├── permissions.py           # ToolGroup + SkillSet 权限路由定义
│   │   ├── registry.py              # 工具注册中心（懒加载 + SkillSet 筛选）
│   │   ├── db_tools.py              # DB 类工具（历史查询、抽题、搜索等）
│   │   ├── search_tools.py          # 联网搜索工具（博查 + Wikipedia）
│   │   ├── difficulty_tools.py      # 难度调整工具
│   │   └── knowledge/
│   │       ├── KnowledgeCore.py     # 阿里云百炼 RAG 封装（支持 SDK/HTTP 双模式）
│   │       ├── create_knowledge_search_tool.py  # 技术知识库工具（助手用）
│   │       ├── create_ds_course_tool.py         # 课程知识库工具（面试引擎用）
│   │       ├── create_teaching_kb_tool.py       # 教学知识库工具
│   │       └── create_combined_kb_tool.py       # 双库混合检索工具
│   │
│   └── voice_sdk/
│       └── voice.py                 # 录音（PyAudio）+ ASR（qwen3-asr-flash）
│
├── .env.example                     # 环境变量模板
├── requirements.txt
└── README.md
```

---

## 4. 核心模块说明

### 4.1 Agent（`service/agent_core/agent_core.py`）

通用 Agent 核心，所有引擎的执行层。

```python
agent = Agent(
    db=db,
    system_prompt="你是面试官...",
    model="qwen3-omni-flash",   # 默认模型
    skill_set=INTERVIEW_SKILLS, # 自动从 registry 加载对应工具
)

# 流式输出
for chunk in agent.stream("请出一道 Java 多线程题"):
    print(chunk, end="", flush=True)

# 运行时切换
agent.set_system_prompt("新的 Prompt").set_skill_set(ASSISTANT_SKILLS)
```

**特殊 token（面试引擎专用，UI 层消费）：**

| Token | 含义 |
|-------|------|
| `__EVAL__:{json}\n` | 评分结果（4 维度 + 综合分 + 建议） |
| `__IS_FINISHED__\n` | 本轮是最后一题 |
| `__FINISHED__\n` | 面试全部结束（兜底） |
| `__SCORE__:{float}\n` | 报告总分 |
| `__ERROR__:{msg}\n` | 内部错误 |

### 4.2 MarkovDecisionEngine（`static_markov_asking.py`）

无状态决策引擎，根据评分决定下一步行动。

| 意图 | 触发条件 | 行为 |
|------|----------|------|
| `DEEPEN` | 技术分 ≥ 6 且深度分 < 7 | 追问底层原理（最多 2 次） |
| `CORRECT` | 技术分 < 5 | 引导纠错 |
| `CLARIFY` | 逻辑分 < 5 | 换角度重问 |
| `NEXT` | 其余情况 | 换新题，动态调整难度 |
| `WRAPUP` | 已达最大轮数（默认 8） | 结束面试 |

难度调整策略：综合分 ≥ 8 升级，≤ 4 降级，其余保持。

### 4.3 AnswerEvaluator（`service/evaluator.py`）

同步评分，返回 `EvalResult`。

| 维度 | 权重 | 说明 |
|------|------|------|
| 技术正确性（tech） | 35% | 答案的技术准确度 |
| 逻辑严谨性（logic） | 25% | 回答的条理性 |
| 知识深度（depth） | 25% | 对知识点理解的深度 |
| 表达清晰度（clarity） | 15% | 是否易懂 |

### 4.4 KnowledgeCore（`service/tools/knowledge/KnowledgeCore.py`）

阿里云百炼 RAG 检索封装。自动探测运行模式：

- **官方 SDK 模式**：需要 `BAILOU_WORKSPACE_ID` + AK/SK，更稳定
- **HTTP API 模式**：只需 `DASHSCOPE_API_KEY`，作为降级方案

```python
kb = KnowledgeCore(knowledge_base_id="xxx", label="技术知识库")
results = kb.retrieve("Redis 分布式锁", top_k=3)   # 返回 List[str]
ctx = kb.retrieve_as_context("Spring AOP")         # 返回可嵌入 Prompt 的字符串
```

---

## 5. 工具权限体系

### 5.1 SkillSet 定义（`service/tools/permissions.py`）

| SkillSet | 适用场景 | 包含工具组 |
|----------|----------|------------|
| `INTERVIEW_SKILLS` | 面试引擎 | COMMON + DS_COURSE |
| `READONLY_SKILLS` | 只读查询 | COMMON + QUIZ + RAG |
| `ASSISTANT_SKILLS` | AI 助手 | COMMON + QUIZ + RAG + SEARCH + HISTORY |
| `ADMIN_SKILLS` | 管理员 | 同 ASSISTANT（预留扩展） |
| `COURSE_DEFENSE_SKILLS` | 课程答辩 | COMMON + QUIZ + TEACHING_KB + COMBINED_KB |

### 5.2 工具清单

| 工具名 | 所属组 | 说明 |
|--------|--------|------|
| `get_job_position_info` | COMMON | 查询岗位信息 |
| `draw_questions_from_bank` | COMMON | 按分类/难度随机抽题 |
| `get_question_bank_stats` | COMMON | 题库统计 |
| `adjust_question_difficulty` | COMMON | 根据评分调整难度 |
| `search_question_bank` | QUIZ | 关键词搜索题目（分页） |
| `search_knowledge_base` | RAG | 技术知识库检索（助手用） |
| `search_ds_course` | DS_COURSE | 数据结构课程库（面试引擎用） |
| `search_teaching_knowledge` | TEACHING_KB | 教学知识库检索 |
| `search_combined_knowledge` | COMBINED_KB | 双库混合检索 |
| `web_search` | SEARCH | 博查联网搜索 |
| `search_wikipedia` | SEARCH | Wikipedia 查询 |
| `get_student_interview_history` | HISTORY | 历史面试记录（分页） |
| `get_student_id_by_name` | HISTORY | 按姓名查学生 ID |

### 5.3 新增工具步骤

1. 在 `service/tools/db_tools.py`（或对应文件）编写工厂函数
2. 在 `permissions.py` 的合适 `ToolGroup` 中添加工具名常量
3. 在 `registry.py` 的 `build_tools()` 中注册工厂函数
4. SkillSet 自动更新，无需手动维护

---

## 6. 知识库配置

### 6.1 两个知识库的用途区分

| 环境变量 | 用途 | 使用方 |
|----------|------|--------|
| `TECH_KB_ID` | Java/Spring/MySQL/Redis/前端等面试技术知识 | `HelperEngine`（AI 助手） |
| `DS_COURSE_KB_ID` | 数据结构课程讲义、场景素材 | `InterviewEngine`（面试引擎出题增强） |

### 6.2 缺失时的降级策略

- `TECH_KB_ID` 未配置 → `search_knowledge_base` 工具跳过，不崩溃
- `DS_COURSE_KB_ID` 未配置 → `search_ds_course` 工具跳过，面试不使用 RAG 增强
- 两者都缺失时系统仍可正常运行，只是无知识库检索能力

### 6.3 认证模式

KnowledgeCore 在初始化时自动探测：

```
有 AK/SK + workspace_id + alibabacloud SDK？
  → 官方 SDK 模式（推荐，更稳定）
  
仅有 DASHSCOPE_API_KEY？
  → HTTP API 模式（降级方案）

两者都没有？
  → 抛出 ValueError，工具注册时被捕获并跳过
```

---

## 7. 语音功能

### 7.1 录制流程

```
VoiceRecorder.record(max=60s)
    ├── PyAudio 捕获音频（16kHz，单声道，int16）
    ├── VAD：持续 2 秒静音自动停止
    ├── 用户点击"停止录音"可立即停止
    └── 保存为 WAV 到 output_audio/ 目录

→ 进入预发送状态（微信模式）
    ├── "发送语音"   → 直接提交（若有文本）或触发自动转写
    ├── "转文字"     → STTClient.analyze() → 填入文本框供编辑
    └── "取消录音"   → 删除临时文件
```

### 7.2 ASR（STTClient）

调用 `qwen3-asr-flash` 模型，Base64 编码音频作为 Data URL 发送。

情绪映射：

| 模型原始标签 | 显示情绪 |
|------------|----------|
| neutral | 流畅 |
| happy | 自信 |
| fearful | 紧张 |
| sad | 迟疑 |
| angry / disgusted | 混乱 |
| surprised | 自信 |

### 7.3 线程安全

- `VoiceWorker` 在独立 QThread 运行，通过 Signal 与 UI 通信
- `ASRWorker` 同样独立线程，转写完成后回调到主线程
- 线程生命周期：`finished/error → thread.quit() → deleteLater()`，无内存泄漏

---

## 8. 数据库结构

数据库文件：`interview.db`（SQLite，WAL 模式）

```sql
-- 岗位表
job_position (id, name, description, tech_stack JSON, created_at)

-- 题库（分类 + 难度 + 题目 + 答案）
question_bank (id, classify, level, content, answer)

-- 学生表
student (id, name, email, created_at)

-- 面试会话
interview_session (id, student_id, job_position_id, status, started_at,
                   finished_at, overall_score, report)

-- 面试轮次（每题一行）
interview_turn (id, session_id, turn_index, question_text,
                student_answer, scores JSON, audio_path, created_at)

-- RAG 知识库分块（本地备用）
knowledge_chunk (id, job_position_id, source, chunk_text, chunk_index, created_at)
```

**评分 JSON 格式（scores 字段）：**
```json
{
  "tech": 8, "logic": 7, "depth": 6, "clarity": 9,
  "overall": 7.45,
  "strengths": "...", "weaknesses": "...", "suggestion": "..."
}
```

---

## 9. 开发规范

### 9.1 UI 开发

- 所有新面板继承自 `QWidget`，使用 `UI/components.py` 中的统一组件
- 颜色全部使用 `Theme`（`T`）中的常量，禁止硬编码颜色值
- 耗时操作（LLM 调用、IO）必须放在 `QThread` 中，通过 `Signal` 与 UI 通信
- 流式输出使用 `StreamSignals`（`chunk_received / stream_done / stream_error`）

### 9.2 工具开发

```python
# 标准工厂函数模式
class MyToolInput(BaseModel):
    param: str = Field(..., description="参数说明")

def create_my_tool(db):           # 工厂函数，接受依赖注入
    @tool(args_schema=MyToolInput)
    def my_tool(param: str) -> str:
        """工具描述（LLM 会读取这段描述来决定是否调用）"""
        ...
    return my_tool
```

### 9.3 模型选择

| 场景 | 模型 | 说明 |
|------|------|------|
| 面试官对话 | `qwen3-omni-flash` | 流式生成，温度 0.7 |
| AI 助手 | `qwen3-omni-flash` | 流式生成，温度 0.1 |
| 答案评分 | `qwen-plus` | 同步调用，需要 JSON 格式输出 |
| 报告生成 | 同面试官模型 | 温度 0.3，max_tokens=2048 |

### 9.4 错误处理原则

- 知识库工具初始化失败 → `registry.py` 捕获异常，打印警告，跳过该工具（不崩溃）
- Agent 流式输出异常 → yield 错误提示字符串，由 UI 层显示
- 语音录制/ASR 异常 → 通过 `Signal.error.emit(str)` 通知 UI，弹窗展示

---

*如有问题，群里 @ 队长或直接提 Issue。Good luck 大家! 🚀*