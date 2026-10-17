# 词间 Lexi · Personalized Vocabulary Agent MVP

把一张英文图片变成可交互的阅读材料，通过用户反馈积累自己的词汇模型，再推荐少量适合当前语境的关联词。

## 快速开始

**在本次交付的电脑上，直接双击项目文件夹中的 `打开词间.cmd`。** 它会启动后台服务并打开浏览器；重复双击会复用现有服务。关闭浏览器不会停止服务，需要退出时双击 `关闭词间.cmd`。本地网页只有在服务运行时才能打开，不能直接双击 `frontend/index.html` 使用。

启动失败时窗口会保留错误，日志在 `backend/data/logs/`。下方安装步骤适用于新环境。

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

本机 OCR 在 CPU 上运行，`OCR_CPU_THREADS` 可在 `.env` 中设置为 1–8，模板默认 4；本次交付电脑已设为 2，供阅读与日常软件同时使用。修改后关闭并重新打开词间生效。模型仅在首次识别时加载；大图片和首次加载期间仍可能短暂占用较多 CPU 与内存。

公网部署使用 **GitHub Pages 前端 + 云端后端**。Linux 服务器见 [Docker Compose 部署](deploy/vps/README.md)，Render 见 [部署说明](docs/DEPLOYMENT.md)。当前先完善本地版，公网发布尚未完成。

## 使用流程

1. 上传 JPG/PNG 英文图片，或选择内置机器学习阅读示例。
2. 点击图片上的词框，或用单词列表快速筛选。
3. 标记“已掌握 / 不认识 / 不确定”。未标记是独立状态，不代表不确定。
4. 主动查看语境解释，再请求最多 3 个关联词。展开关联后可反馈认识、不认识、有用或没用。
5. 进入“生词复习”，先回想，再按需显示原文提示或回到原文，选择“已掌握 / 不确定 / 不认识”保存自评。
6. 在“我的词库”按词或状态筛选，点击“导出当前筛选 CSV”；也可查看学习概览、我的关联和学习足迹。

键盘 `1 / 2 / 3` 可快速标记选中的词，随后移到下一个词。坐标以原图像素保存，前端按显示尺寸缩放。图片支持缩放与原文切换。

复习每组最多 10 个当前“不认识”或“不确定”的词，优先尚未复习、较久未复习的词，同等条件下参考规则掌握度和出现次数。未标记和已掌握的词不进入队列。显示提示、跳过或切回原文均不记录自评；确认自评后才保存词库、规则分数与学习足迹。无原文的关联词仍可自评。复习组在切换导航时保留；空队列中添加生词后点击“重新检查词库”。仍不熟悉的词可能在下一组再次出现；当前不提供按日期安排的间隔复习。

CSV 只包含当前筛选结果的词形、词元、状态、规则掌握度、出现与累计查询次数、首次及最近出现时间；空分数保留为空，时间使用 UTC。文件可用 Excel 打开。它不是整库备份，不包含原图、全文和完整学习历史；完整备份需保留 `backend/data/`。

## LLM 配置

默认 `LLM_PROVIDER=offline`，无需 API Key 即可体验完整交互。离线解释仅覆盖有限的机器学习词条，推荐也来自规则与小型候选集；页面明确标明离线模式。未知词不会被编造释义。内置示例是带预标注坐标的测试阅读材料，不冒充 OCR 结果。

如需解释任意文章，在 `.env` 配置支持 Chat Completions 的服务：

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-key
LLM_TIMEOUT_SECONDS=30
LLM_JSON_MODE=true
LLM_MAX_TOKENS=4096
```

### Agnes 3.0 Flash

使用 [Agnes 3.0 Flash 官方文档](https://www.agnes-ai.com/zh-Hans/docs/agnes-30-flash)确认的模型 ID 和接口，在本机 `.env` 填写：

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://apihub.agnes-ai.com/v1
LLM_MODEL=agnes-3.0-flash
LLM_API_KEY=你的Agnes密钥
LLM_TIMEOUT_SECONDS=60
LLM_JSON_MODE=false
LLM_MAX_TOKENS=4096
```

在 [Agnes 开发者平台](https://platform.agnes-ai.com/)创建 API Key；填入原始密钥即可，不要加 `Bearer` 前缀。`openai_compatible` 表示接口协议，请求实际发送给上面的 Agnes 地址。快速开始页面的示例仍用 2.5，本文按 3.0 Flash 专页配置。

Agnes 的请求字段表未列出 `response_format`，因此这里设为 `LLM_JSON_MODE=false`，通过提示词要求 JSON，并在后端继续严格验证字段、数值、原词及词元。此配置不代表已实测 Agnes 不支持 JSON 模式。每次请求最多输出 4096 tokens；截断、异常或不符合结构的返回会明确降级。可按实际延迟及输出需要调整超时和输出预算。

重新启动后生效。供应商只在 LLMService 适配器中出现；密钥仅留在后端。LLM 请求会向所配服务发送当前词、句子、截取的文章上下文和词汇/历史摘要。OCR 在本地运行。超时、无效 JSON、服务异常会返回明确警告并降级为有限的离线结果。

填写配置后，先在项目目录运行连接检查：

```powershell
# 本次交付环境；自行安装时替换为 .venv/Scripts/python.exe
../../work/venv/Scripts/python.exe scripts/check_llm.py
```

检查只使用内置测试句子，分别验证语境释义和关联词的真实模型响应，不写入词库或学习事件。离线模式、缺少配置或调用降级均判为失败；不会把离线返回误报为连接成功。检查通过后，双击“关闭词间.cmd”，再双击“打开词间.cmd”载入新配置。API Key 仅填入已被 Git 忽略的本机 `.env`。

## 架构与数据流

```text
frontend/                 React + TypeScript + Vite
backend/
  api/                    HTTP 路由与输入边界
  services/               文档、词汇、LLM 服务
  repositories/           SQLAlchemy 查询与更新
  models/                 用户、文档、出现位置、词库、事件、推荐反馈/回执与词汇关系
  schemas/                明确的输入输出结构
  ocr/                    PaddleOCR 3.x 适配器
  agents/                 候选生成、个性化排序与预算控制
  data/                   SQLite、图片、模型缓存（Git 忽略）
```

```text
图片 → 校验与方向归一化 → OCR 文本 / 行 / 词框 / 置信度
    → Document + WordOccurrence → Vocabulary 观察计数
用户标记 → Vocabulary 状态 + LearningEvent
复习自评 → ReviewReceipt + Vocabulary 状态/规则分数 + LearningEvent（同事务）
主动查询 → 原词/lemma + 句子/文章 + 三类词表 + 当前词历史/最近行为 → LLMService
    → 候选生成 → 过滤已掌握/当前词/重复词 → 排序 → 最多 3 个词
```

Agent 将语义相关、语境相关、陌生程度和学习价值合并排序。当前掌握度越低，陌生度权重越高；KNOWN 或分数至少 0.85 的候选被过滤。输出含六类关系及 0–1 优先级。语境重要度与掌握度是不同字段；离线覆盖词的 0.8 是固定参考值，未覆盖的词为空。LLM JSON 的长度、枚举、有限数值和范围均校验，异常时降级。

`Vocabulary` 按用户和 lemma 去重。使用 simplemma 和保守保护词表，将 generalized / generalizing / generalizes 归并到 generalize；派生名词 generalization 保留自身。无词性上下文的还原仍可能有歧义。事件保留用户看到的原词及 lemma。

`MasteryService` 使用可替换的简单规则：首次行为从 0.5 起算，标记已掌握 +0.15、不认识 −0.20、不确定 −0.08，主动查词 −0.03，限制在 0–1。没有学习行为的词保持空值。分数与事件同事务保存，事件包含调整前后值及规则版本；这不是科学记忆模型或掌握概率。

启动时自动执行编号数据库迁移，修改前用 SQLite 在线备份到 `backend/data/backups/`（包括 WAL 数据）。旧词形合并保留计数、历史及最近明确标记；旧的自动查询事件保留，但不用于降低掌握度。若迁移失败，事务回滚并保留备份。

复习回执使用独立的新增表，不改动已有表的列。前端为同一次自评生成 UUID；响应丢失后重试复用编号，不重复记分或新增事件。延迟重试返回当前词库状态，不覆盖后续标记；同一编号携带不同自评内容时返回 409。

`seen_count` 按每次成功导入的文章中实际出现次数累计，刷新/打开同一文档不增加；重新上传会视为新的阅读材料。只有主动点击“查看语境释义”且查询成功才增加 `lookup_count`。推荐新增词尚未出现在阅读图片中时，`seen_count=0`，首次与最近出现时间为空。单词侧栏显示上次标记和最近 20 条事件，全局足迹默认显示最近 100 条。被动读取这些记录不产生学习行为。

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
npm test
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
| `GET /api/vocabulary/{word}/history` | 归一化词形后的记录、上次标记和最近 20 条事件；只读 |
| `GET /api/review?limit=10` | 当前生词总数、复习队列、最近复习时间和最近原文位置；只读，limit 为 1–50 |
| `GET /api/dashboard` | SQL 统计、学习画像与 7 组词汇排行；只读 |
| `GET /api/relations` | 最近 200 条个人词汇关系、总数和推荐反馈统计；只读 |
| `PUT /api/vocabulary/{word}` | `{"status":"UNKNOWN","document_id":1}`；文档 ID 可省略 |
| `POST /api/words/analyze` | `{"occurrence_id":1}`：语境释义并记录查询 |
| `POST /api/words/recommend` | `{"occurrence_id":1}`：个性化关联推荐并记录事件 |
| `POST /api/recommendations/{batch_id}/feedback` | `word`、`action`、UUID `request_id`；反馈绑定已生成的词项快照 |
| `GET /api/events?limit=100` | 最近学习事件，最多 1000 条 |

`health` 表示服务和配置可用；OCR 采用延迟加载，因此此接口不代表模型已经下载。LLM 是否成功由每次结果的 `source` / `warning` 表明。

复习标记使用同一 PUT 接口，额外携带 `"origin":"review"` 和 UUID `request_id`。有真实原文时传入 `document_id`，无原文时省略；`request_id` 仅用于复习，原有阅读标记保持兼容。

推荐生成、展示和查看分别记录。卡片至少四分之一进入可见区域时记为展示，主动展开才记为查看。每次反馈绑定推荐批次；UUID 回执保证响应丢失后的重试不重复记账，也不会覆盖后续选择。反馈、词库状态、掌握度及事件同事务保存。有用/没用针对当前词与目标词这组关联调整排序，超过最近 100 条事件的反馈仍参与排序。

`VocabularyRelation` 使用 SQLite 普通关系表，按用户、源词、目标词和关系类型去重。支持 WORD_FAMILY、SYNONYM、ANTONYM、CONCEPT、COLLOCATION、DOMAIN_RELATED。生成或展示推荐不建立关系；逐词查看或明确反馈才建立关系，并与行为记录一起提交。初始权重 0.5：查看 +0.05、不认识 +0.08、认识 −0.02、有用 +0.15、没用 −0.20，限制在 0–1。它是可替换的规则强度，不是关联概率；事件编号保证重复回放不会重复调整。006 迁移根据已有的明确反馈补齐关系，不制造历史行为。

“我的关联”统计按每个批次中的词项计数，同一个词在两批推荐中算两项；展示、查看、转为不认识分别去重。有用/没用统计每个词项最近的评价，修改评价后计数随之变化。旧版没有词项快照的生成记录不进入这些指标。页面不把这些不同口径混算为接受率。

## MVP 边界

学习概览中的“生词”包含当前不认识与不确定的词。近 7 天新增生词按首次明确标记陌生的时间统计，近 7 天查询仅统计明确主动查询；近 30 天高频词按该时间段导入文章的实际词位置计数。长期未掌握表示首次陌生标记已满 30 天且当前仍陌生，不推断期间一直未掌握。累计查询保留旧版本记录。各排行最多 10 项，低掌握度排行排除尚未评估的空值。

- 默认 `APP_MODE=local` 为本机单用户；公网需使用 `APP_MODE=public` 的访客凭证与数据隔离。公开版暂不提供跨设备登录：清理站点存储会失去原学习空间的访问凭证。
- 支持 JPG/PNG，单张最多 10 MB、2500 万像素；暂不支持 PDF。
- 模型首次加载较慢，同一进程串行执行 OCR，避免线程不安全。
- 个人数据保存在 `backend/data/`，整体备份此目录可保留数据库和原图。
- 词汇统计、反馈与事件是真实持久化数据；推荐模型目前是 LLM/规则，不是训练好的推荐系统。
- 可在 OCR、LLMService、候选生成和排序接口分别替换 PDF、Embedding、Reranker 等能力。当前不引入向量数据库或知识图谱基础设施。

实现参考：[FastAPI 文件上传](https://fastapi.tiangolo.com/reference/uploadfile/)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)、[Vite 代理配置](https://vite.dev/config/server-options.html#server-proxy)。
