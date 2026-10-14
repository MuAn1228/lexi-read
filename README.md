# 词间 Lexi · Personalized Vocabulary Agent MVP

把一张英文图片变成可交互的阅读材料，通过用户反馈积累自己的词汇模型，再推荐少量适合当前语境的关联词。

## 快速开始

推荐 Python 3.12、Node.js 22.12+。SQLite 不需要单独安装。以下命令在本文件所在的项目目录运行。

```powershell
# 建议使用 uv 管理 Python（也可 python -m venv .venv）
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt -r requirements-ocr.txt
Copy-Item .env.example .env
cd frontend
npm ci
npm run build
cd ..
.\start.ps1
```

浏览器打开 http://127.0.0.1:8000，API 文档在 http://127.0.0.1:8000/docs。`Ctrl+C` 停止服务。启动脚本优先使用项目的 `.venv`；本次交付环境也支持复用工作目录下已安装的 `work/venv`。不依赖 PowerShell 脚本时运行：

```powershell
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Linux/macOS 对应环境路径是 `.venv/bin/python`，运行同一条 uvicorn 命令。首次 OCR 会下载 Paddle 模型，需要联网；后续使用本地缓存。安装 CPU 版本即可，无需 GPU。

## 使用流程

1. 上传 JPG/PNG 英文图片，或选择内置机器学习阅读示例。
2. 点击图片上的词框，或用单词列表快速筛选。
3. 标记“已掌握 / 不认识 / 不确定”。未标记是独立状态，不代表不确定。
4. 查看当前句子的语境解释，再主动请求最多 3 个关联词。
5. 在个人词库、学习记录中查看积累；刷新页面后数据仍保留。

键盘 `1 / 2 / 3` 可快速标记选中的词，随后移到下一个词。坐标以原图像素保存，前端按显示尺寸缩放。图片支持缩放与原文切换。

## LLM 配置

默认 `LLM_PROVIDER=offline`，无需 API Key 即可体验完整交互。离线解释仅覆盖有限的机器学习词条，推荐也来自规则与小型候选集；页面明确标明离线模式。未知词不会被编造释义。内置示例是带预标注坐标的测试阅读材料，不冒充 OCR 结果。

如需解释任意文章，在 `.env` 配置支持 Chat Completions 的服务：

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-key
LLM_TIMEOUT_SECONDS=30
```

重新启动后生效。供应商只在 LLMService 适配器中出现；密钥仅留在后端。LLM 请求会向所配服务发送当前词、句子、截取的文章上下文和词汇/历史摘要。OCR 在本地运行。超时、无效 JSON、服务异常会返回明确警告并降级为有限的离线结果。

## 架构与数据流

```text
frontend/                 React + TypeScript + Vite
backend/
  api/                    HTTP 路由与输入边界
  services/               文档、词汇、LLM 服务
  repositories/           SQLAlchemy 查询与更新
  models/                 User / Document / WordOccurrence / Vocabulary / LearningEvent
  schemas/                明确的输入输出结构
  ocr/                    PaddleOCR 3.x 适配器
  agents/                 候选生成、个性化排序与预算控制
  data/                   SQLite、图片、模型缓存（Git 忽略）
```

```text
图片 → 校验与方向归一化 → OCR 文本 / 行 / 词框 / 置信度
    → Document + WordOccurrence → Vocabulary 观察计数
用户标记 → Vocabulary 状态 + LearningEvent
选词 → 句子 + 文章 + 用户模型 → LLMService
    → 候选生成 → 过滤已掌握/当前词/重复词 → 排序 → 最多 3 个词
```

`Vocabulary` 按默认用户和保守归一化的 lemma 去重。第一版不做复杂词形还原，避免把不同词性或词义强行合并；例如 `generalization` 的 lemma 保留名词本身，不错误变成 `generalize`。`mastery_score` 预留为可空的 0–1 值，不把三态反馈伪装成学习概率。

`seen_count` 按每次成功导入的文章中实际出现次数累计，刷新/打开同一文档不增加；重新上传会视为新的阅读材料。`lookup_count` 在语境查询成功返回时累计。推荐新增词尚未出现在阅读图片中时，`seen_count=0`、`last_seen_at=null`。学习事件记录标记前状态，以及推荐词和结果来源；历史页面默认显示最近 100 条。

OCR 优先使用原生 word boxes；缺失时才按字符比例估算词框，并通过 `bbox_source=estimated` 标明。置信度来自 OCR 行识别结果，不是用户掌握概率。倾斜、多栏、模糊图片仍可能识别有误。

## 开发与验证

前后端热更新（两个终端）：

```powershell
.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# 第二个终端
cd frontend
npm run dev
```

Vite 将 `/api` 代理到 `127.0.0.1:8000`。生产构建后由 FastAPI 提供前端静态文件，无需另起前端服务器。

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
cd frontend
npm run build
```

单元/API 测试使用临时数据库与可替换 OCR，不会修改个人数据。真实 Paddle OCR 和浏览器验收结果见 `VERIFICATION.md`。

上传 `samples/reading.png` 可验证真实 OCR（19 个英文词）；也可运行 `python scripts/ocr_smoke.py`。预览图：`docs/reading-desktop.png`、`docs/reading-mobile.png`。

## 主要 API

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/health` | 服务状态和配置的 provider |
| `POST /api/documents` | multipart `file` 上传图片；`/api/documents/upload` 为别名 |
| `POST /api/documents/demo` | 幂等打开预标注示例 |
| `GET /api/documents` | 文档列表 |
| `GET /api/documents/{id}` | 原文、词框和最新词汇状态 |
| `GET /api/documents/{id}/image` | 方向归一化后的 PNG 原图 |
| `GET /api/vocabulary` | 当前用户的全部观察/标记词汇 |
| `PUT /api/vocabulary/{word}` | `{"status":"UNKNOWN","document_id":1}`；文档 ID 可省略 |
| `POST /api/words/analyze` | `{"occurrence_id":1}`：语境释义并记录查询 |
| `POST /api/words/recommend` | `{"occurrence_id":1}`：个性化关联推荐并记录事件 |
| `GET /api/events?limit=100` | 最近学习事件，最多 1000 条 |

`health` 表示服务和配置可用；OCR 采用延迟加载，因此此接口不代表模型已经下载。LLM 是否成功由每次结果的 `source` / `warning` 表明。

## MVP 边界

- 默认单用户、本机使用；没有登录、支付或后台，不应直接暴露到公网。
- 支持 JPG/PNG，单张最多 10 MB、2500 万像素；暂不支持 PDF。
- 模型首次加载较慢，同一进程串行执行 OCR，避免线程不安全。
- 个人数据保存在 `backend/data/`，整体备份此目录可保留数据库和原图。
- 词汇统计、反馈与事件是真实持久化数据；推荐模型目前是 LLM/规则，不是训练好的推荐系统。
- 可在 OCR、LLMService、候选生成和排序接口分别替换 PDF、Embedding、Reranker 等能力。当前不引入向量数据库或知识图谱基础设施。

实现参考：[FastAPI 文件上传](https://fastapi.tiangolo.com/reference/uploadfile/)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)、[Vite 代理配置](https://vite.dev/config/server-options.html#server-proxy)。
