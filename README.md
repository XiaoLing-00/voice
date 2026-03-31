# AI 模拟面试与能力提升平台

> 锐捷网络企业命题 · 开发者协作手册

---

# 注意（关于把敏感信息上传到github的解决方案）

一、如果只是“刚提交，还没 push”

最简单：

git reset --soft HEAD~1

含义：
    
撤回最近一次 commit

代码还在本地

你可以删掉 API key 再重新 commit

然后重新提交：

git add .
git commit -m "remove api key"

二、如果 已经 push 到 GitHub

方法 1（简单粗暴，适合最近一次提交）
git reset --soft HEAD~1
git push --force

解释：

回退本地提交

强制覆盖远程历史

---

## 目录


---

## 1. 项目总览

### 1.1 赛题背景

本项目为「面试与能力提升软件」竞赛作品。

核心场景：
- 学生通过模拟面试，练习技术岗位面试题
- AI 面试官从题库抽题 → 学生回答 → 多维度评分 → 个性化提升建议
- 老师上传课程资料后，AI 可基于课程内容出题，实现「课程答辩式面试」

### 1.2 架构一览


### 1.3 目录结构

---

## 2. 快速启动

### 2.1 环境要求

| 依赖 | 版本 | 备注 |
|------|------|------|
| Python | 3.11+ | 低版本缺少 `match/case`，不兼容 |

> ⚠️ `torch` 建议单独先装：
> ```bash
> pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
> pip install -r requirements.txt
> ```

### 2.2 安装步骤

1. 克隆仓库
2. 复制 `.env.example` 为 `.env`，填入密钥（见 2.3）
3. 安装依赖

### 2.3 .env 配置

> ⚠️ **所有 Key 均不要提交 Git**，`.gitignore` 已包含 `.env`

![相关示例](./.env_example)
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
- 最低要求：`DASHSCOPE_API_KEY`，HTTP 模式启动（知识库 ID 可为空，对应工具自动跳过）
- 加上百炼三件套：切换官方 SDK 模式，更稳定
- `TECH_KB_ID` / `DS_COURSE_KB_ID` 缺失时，对应工具跳过加载，不会崩溃

---

## 3. 核心调用链


---

*如有问题，群里 @ 队长或直接提 Issue。Good luck 大家! 🚀*
