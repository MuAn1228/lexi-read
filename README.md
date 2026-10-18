# 词间 Lexi · Personalized Vocabulary Agent MVP

把英文图片、PDF 或粘贴的文章变成可交互的阅读材料，通过用户反馈积累自己的词汇模型，再推荐少量适合当前语境的关联词。

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

1. 点击“添加阅读材料”，上传 JPG/PNG 图片、导入 PDF、粘贴英文文章，或打开内置机器学习阅读示例。
2. 点击图片词框或文章中的单词，也可用单词列表快速筛选。
3. 标记“已掌握 / 不认识 / 不确定”。未标记是独立状态，不代表不确定。
4. 主动查看语境解释，再请求最多 3 个关联词。展开关联后可反馈认识、不认识、有用或没用；需要读懂原句时点击“翻译与解析本句”。
5. 从阅读页的今日任务进入“生词复习”，先回想，再按需显示原文提示或回到原文，选择“已掌握 / 不确定 / 不认识”保存自评并安排下次复习。
6. 在“文章库”搜索标题或正文，按标签、材料类型筛选，整理标题和标签，或把材料移到回收站。
7. 在“我的词库”按词或状态筛选，点击“导出当前筛选 CSV”；也可查看学习概览、我的关联和学习足迹。
8. 在“数据管理”下载完整备份，或选择备份预览内容、确认后恢复本机学习数据。

键盘 `1 / 2 / 3` 可快速标记选中的词，随后移到下一个词。坐标以原图像素保存，前端按显示尺寸缩放。图片支持缩放与原文切换。

粘贴文章支持可选标题和最多 20,000 个字符的正文，保留段落、空格和标点；未填标题时取首个非空行。导入不运行 OCR，也不调用模型。点击正文中的词后，可以使用现有语境释义、三种标记和间隔复习；重复出现的词分别保留原句与位置，共享词库状态。复习时“回到原文”也能定位到文字文章里的原词。

PDF 导入支持可以选中文字的英文文件，最大 10 MB、100 页、提取正文最多 200,000 个 Unicode 字符。阅读页按原文件页码显示提取正文，保留空白页编号，可切换页码、点击单词、保存精确书签；复习回原文会跳到对应页的原词。提取正文不保留 PDF 的原始版面，原文件可在阅读页下载。扫描件、纯图片和加密 PDF 暂不支持。导入不调用 OCR 或 AI；失败保留已选文件和请求编号，重试不会重复文章或词频。

选中单词后，在侧栏点击“翻译与解析本句”，使用当前原句和文章语境生成中文翻译、句子结构、分句说明及语法要点。仅点击才调用模型，支持最长 4,000 个 Unicode 字符的句子；同一篇文章同一句的成功结果会缓存，选中句中其他词时可复用。离线、超时或返回格式不可靠时明确提示不可用并允许重试，不编造译文。此功能不增加查词次数、学习事件或改变复习排期；模型解析可辅助阅读，仍需结合原文判断。

导入失败会保留当前输入，重试相同内容不会重复创建文章或累计词频；成功后主动再次导入则视为新的阅读材料。关闭导入弹窗仍保留当前草稿，刷新网页不会保存尚未导入的草稿。文字文章与图片都保存在本机 SQLite 数据库中，重开软件后可以继续阅读。

文章库支持标题/全文搜索、标签与类型筛选，以及按导入时间或标题排序。每篇最多 10 个标签，每个标签最多 20 字；重命名和整理标签不会重新导入文章或增加词频。修改后的标题会同步到阅读材料选择器。

阅读时可把当前选中的单词保存为书签，也可回到或清除书签。再次打开文章优先定位到保存位置；从复习跳回原文时优先定位到该张复习卡对应的词。书签只用于定位，不代表已经读完或掌握文章中的单词，也不影响复习排期。

移到回收站后，文章从正常列表、原文入口和复习原句选择中移除；词库、累计出现次数、学习记录和复习排期保留。若同一词还有其他文章语境，复习可使用其他原句，否则仍可无原文自评。回收站可以恢复文章及其标签、书签和词位置；当前不提供永久清空，原图和全文继续保留。删除与恢复不重复累计词频。

复习每组最多 10 个当前已到期的词，包括到期的已掌握词；未来到期的词暂不进入新的一组。今日任务显示当前待复习、今日已复习及稍后到期情况。今日已复习按电脑当地日期统计明确自评过的不同词数，同词当天多次自评只算一个词。未标记的词不进入队列。

自评后的安排采用简单固定规则，与已有的规则掌握度分开保存：

| 自评 | 下次复习 |
| --- | --- |
| 不认识 | 10 分钟后，连续掌握次数归零 |
| 不确定 | 1 天后，连续掌握次数归零 |
| 已掌握 | 连续掌握时依次为 3、7、14、30、60 天，之后保持 60 天 |

普通阅读或关联推荐中明确标记“不认识 / 不确定”，会让该词立即待复习；标记“已掌握”从 3 天后安排回顾，同时重置连续复习次数。这些操作不计入今日复习完成数。间隔从本次明确操作时刻起算，不把分数解释为记忆概率。旧词库升级时，已有生词立即待复习，原有已掌握词从升级时起 3 天后回顾，不补造历史行为。

排期保存在 SQLite，关闭软件或关机后仍保留。下次启动会按实际时间显示到期任务，不需要为了计时让电脑一直运行。

显示提示、跳过、查看原文或查询释义不会改变下次复习时间，也不记录自评。跳过的词保留到期状态；无原文的关联词仍可自评。未完成组和待确认自评的编号保存在当前浏览器中，刷新页面或重开同一访问地址后可以继续。恢复时会核对尚未处理的词是否仍到期；上次断线而未确认的自评保留原编号重试。换浏览器、切换访问地址或清除浏览器数据不保留组内进度；已成功保存的词库、排期和学习记录仍在后端 SQLite 中。

CSV 只包含当前筛选结果的词形、词元、状态、规则掌握度、出现与累计查询次数、首次及最近出现时间；空分数保留为空，时间使用 UTC。文件可用 Excel 打开。它不是整库备份，不包含原图、全文和完整学习历史。

“数据管理”仅在本机模式显示。完整 ZIP 包含在线数据库快照、全部文章原图和 PDF，包括回收站材料、标签、书签、词库、学习记录、关联及复习排期；不包含 `.env` 密钥、日志、模型文件或可重新生成的缓存。导出不修改学习数据，可把下载的 ZIP 另存到其他磁盘。

恢复支持同一数据结构版本导出的完整备份，压缩包最大 250 MB、展开后最大 500 MB、最多 10,000 个文件。先选文件并预览文章、词汇、事件和原文件数量，勾选覆盖确认后才开始恢复；预览 30 分钟内有效。恢复会覆盖当前本机学习数据，开始前自动生成一份恢复前完整备份，成功页可下载。数据库写入失败会回滚；响应丢失后留在当前页面重试，不会再次覆盖已经恢复后的新学习记录。

恢复期间暂停其他 API 操作，等待已有操作结束。成功后会清除当前浏览器的旧阅读位置和未完成复习组，点击“重新进入阅读空间”重新载入；清理失败时按提示重试清理。其他已打开的浏览器窗口请刷新后继续学习。本机 Agnes 配置不会随恢复改变，换电脑使用需另行配置模型。恢复前备份和旧原文件保留于本机，不会被自动删除。

## LLM 配置

默认 `LLM_PROVIDER=offline`，无需 API Key 即可使用基础阅读、标词和复习。离线解释仅覆盖有限的机器学习词条，推荐也来自规则与小型候选集；页面明确标明离线模式。未知词不会被编造释义，整句翻译与解析需要配置模型。内置示例是带预标注坐标的测试阅读材料，不冒充 OCR 结果。

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
粘贴文章 → 校验 / 英文分词 / 字符位置 → Document + WordOccurrence + 导入回执
    → Vocabulary 观察计数（同一事务，不产生自评或查词事件）
用户标记 → Vocabulary 状态 + LearningEvent
复习自评 → ReviewReceipt + Vocabulary 状态/规则分数 + LearningEvent（同事务）
主动查询 → 原词/lemma + 句子/文章 + 三类词表 + 当前词历史/最近行为 → LLMService
    → 候选生成 → 过滤已掌握/当前词/重复词 → 排序 → 最多 3 个词
```

Agent 将语义相关、语境相关、陌生程度和学习价值合并排序。当前掌握度越低，陌生度权重越高；KNOWN 或分数至少 0.85 的候选被过滤。输出含六类关系及 0–1 优先级。语境重要度与掌握度是不同字段；离线覆盖词的 0.8 是固定参考值，未覆盖的词为空。LLM JSON 的长度、枚举、有限数值和范围均校验，异常时降级。

`Vocabulary` 按用户和 lemma 去重。使用 simplemma 和保守保护词表，将 generalized / generalizing / generalizes 归并到 generalize；派生名词 generalization 保留自身。无词性上下文的还原仍可能有歧义。事件保留用户看到的原词及 lemma。

`MasteryService` 使用可替换的简单规则：首次行为从 0.5 起算，标记已掌握 +0.15、不认识 −0.20、不确定 −0.08，主动查词 −0.03，限制在 0–1。没有学习行为的词保持空值。分数与事件同事务保存，事件包含调整前后值及规则版本；这不是科学记忆模型或掌握概率。

启动时自动执行编号数据库迁移，修改前用 SQLite 在线备份到 `backend/data/backups/`（包括 WAL 数据）。旧词形合并保留计数、历史及最近明确标记；旧的自动查询事件保留，但不用于降低掌握度。若迁移失败，事务回滚并保留备份。

复习回执和复习排期使用独立表，不改动已有词库表的列；007 迁移在备份后为旧词库添加初始排期。词库、分数、排期、学习事件与回执在同一事务提交。前端在发送自评前保存 UUID 和评价内容；响应丢失后重试复用编号，不重复记分、延长间隔或新增事件。延迟重试返回当前词库状态，不覆盖后续标记；同一编号携带不同自评内容时返回 409。复习队列与今日统计的读取均不写学习行为或推迟排期。

`seen_count` 按每次成功导入的文章中实际出现次数累计，刷新/打开同一文档不增加；重新上传或主动再次导入会视为新的阅读材料。文字导入使用 UUID 回执，同一请求因网络失败而重试时只累计一次。只有主动点击“查看语境释义”且查询成功才增加 `lookup_count`。推荐新增词尚未出现在阅读材料中时，`seen_count=0`，首次与最近出现时间为空。单词侧栏显示上次标记和最近 20 条事件，全局足迹默认显示最近 100 条。被动读取这些记录不产生学习行为。

OCR 优先使用原生 word boxes；缺失时才按字符比例估算词框，并通过 `bbox_source=estimated` 标明。置信度来自 OCR 行识别结果，不是用户掌握概率。倾斜、多栏、模糊图片仍可能识别有误。

文字文章不带图片 URL、词框或 OCR 置信度，使用 Unicode 码点位置准确还原正文。008 迁移只增加可空字符位置及文字导入回执，在线备份后升级，保留旧图片、词库、历史和复习排期。中英混排内容只提取符合现有规则的英文词，不提供中文分词。

009 迁移在线备份后增加文章标签、书签和回收站时间字段，不改写旧文档、词汇或学习行为。移入回收站使用逻辑删除；已有回执和历史仍引用原文档编号。已确认的复习请求仍能安全重试，文字导入请求若指向回收站文章则提示先恢复。统计中的当前文章数与近 30 天文章词频只计算未删除材料，累计词频和学习行为统计保留历史。

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
| `POST /api/documents/text` | JSON `text`、可选 `title`、UUID `request_id`；保存文字文章，相同请求安全重试 |
| `POST /api/documents/pdf` | multipart `file`、UUID `request_id`；保存 PDF 原文件与逐页正文，相同请求安全重试 |
| `GET /api/backups/export` | 本机完整 ZIP，包含在线数据库快照与全部文章原文件 |
| `POST /api/backups/preview` | 本机 multipart `file`，校验 ZIP 后返回数量和 30 分钟有效的 `token` |
| `POST /api/backups/restore` | 本机 JSON `token`、`confirm:true`；自动安全备份后事务恢复，同 token 安全重试 |
| `GET /api/backups/safety/{filename}` | 下载系统生成的恢复前完整备份 |
| `GET /api/documents` | 默认正常文章；支持 `q`、`tag`、`source`、`deleted=true`、`sort=newest/oldest/title` |
| `PUT /api/documents/{id}` | 部分更新 `title`、`tags`、`bookmark_occurrence_id`；书签传 `null` 可清除 |
| `DELETE /api/documents/{id}` | 移到回收站，保留学习数据；重复请求不重复处理 |
| `POST /api/documents/{id}/restore` | 恢复回收站材料，保留原词位置和书签 |
| `GET /api/documents/{id}` | 原文、词位置和最新词汇状态；文字的 `text_start/end` 为 Unicode 码点位置 |
| `GET /api/documents/{id}/image` | 方向归一化后的 PNG 原图；文字文章返回 404 |
| `GET /api/documents/{id}/pdf` | 下载当前用户活跃 PDF 的原文件；回收站或其他类型返回 404 |
| `GET /api/vocabulary` | 当前用户的全部观察/标记词汇 |
| `GET /api/vocabulary/{word}/history` | 归一化词形后的记录、上次标记和最近 20 条事件；只读 |
| `GET /api/review?limit=10&tz_offset_minutes=480` | 当前到期总数、最多 10 词的队列、今日已复习、稍后到期及下一次时间；只读，limit 为 1–50 |
| `GET /api/dashboard` | SQL 统计、学习画像与 7 组词汇排行；只读 |
| `GET /api/relations` | 最近 200 条个人词汇关系、总数和推荐反馈统计；只读 |
| `PUT /api/vocabulary/{word}` | `{"status":"UNKNOWN","document_id":1}`；文档 ID 可省略 |
| `POST /api/words/analyze` | `{"occurrence_id":1}`：语境释义并记录查询 |
| `POST /api/sentences/analyze` | `{"occurrence_id":1}`：原句中文翻译、结构、分句和语法要点；主动请求，成功缓存，不写学习记录 |
| `POST /api/words/recommend` | `{"occurrence_id":1}`：个性化关联推荐并记录事件 |
| `POST /api/recommendations/{batch_id}/feedback` | `word`、`action`、UUID `request_id`；反馈绑定已生成的词项快照 |
| `GET /api/events?limit=100` | 最近学习事件，最多 1000 条 |

`health` 表示服务和配置可用；OCR 采用延迟加载，因此此接口不代表模型已经下载。LLM 是否成功由每次结果的 `source` / `warning` 表明。

复习标记使用同一 PUT 接口，额外携带 `"origin":"review"` 和 UUID `request_id`。有真实原文时传入 `document_id`，无原文时省略；`request_id` 仅用于复习，原有阅读标记保持兼容。

`GET /api/review` 的 `tz_offset_minutes` 是当前浏览器时区向东的分钟偏移，默认 480（UTC+8），范围 -720–840；所有时间戳仍以 UTC 返回。响应的 `total` 为此刻到期总数，`reviewed_today` 为当地今天去重的自评词数，`later_today` 为今天稍后到期的词数，`next_due_at` 为最早未来排期。可重复传入 `vocabulary_ids`（最多 50 个）核对保存组的剩余卡片，只过滤 `items`，不影响总数与统计。

推荐生成、展示和查看分别记录。卡片至少四分之一进入可见区域时记为展示，主动展开才记为查看。每次反馈绑定推荐批次；UUID 回执保证响应丢失后的重试不重复记账，也不会覆盖后续选择。反馈、词库状态、掌握度及事件同事务保存。有用/没用针对当前词与目标词这组关联调整排序，超过最近 100 条事件的反馈仍参与排序。

`VocabularyRelation` 使用 SQLite 普通关系表，按用户、源词、目标词和关系类型去重。支持 WORD_FAMILY、SYNONYM、ANTONYM、CONCEPT、COLLOCATION、DOMAIN_RELATED。生成或展示推荐不建立关系；逐词查看或明确反馈才建立关系，并与行为记录一起提交。初始权重 0.5：查看 +0.05、不认识 +0.08、认识 −0.02、有用 +0.15、没用 −0.20，限制在 0–1。它是可替换的规则强度，不是关联概率；事件编号保证重复回放不会重复调整。006 迁移根据已有的明确反馈补齐关系，不制造历史行为。

“我的关联”统计按每个批次中的词项计数，同一个词在两批推荐中算两项；展示、查看、转为不认识分别去重。有用/没用统计每个词项最近的评价，修改评价后计数随之变化。旧版没有词项快照的生成记录不进入这些指标。页面不把这些不同口径混算为接受率。

## MVP 边界

学习概览中的“生词”包含当前不认识与不确定的词。近 7 天新增生词按首次明确标记陌生的时间统计，近 7 天查询仅统计明确主动查询；近 30 天高频词按该时间段导入文章的实际词位置计数。长期未掌握表示首次陌生标记已满 30 天且当前仍陌生，不推断期间一直未掌握。累计查询保留旧版本记录。各排行最多 10 项，低掌握度排行排除尚未评估的空值。

- 默认 `APP_MODE=local` 为本机单用户；公网需使用 `APP_MODE=public` 的访客凭证与数据隔离。公开版暂不提供跨设备登录：清理站点存储会失去原学习空间的访问凭证。
- 支持 JPG/PNG（单张最多 10 MB、2500 万像素）、粘贴文字（最多 20,000 字）及文本型英文 PDF（10 MB、100 页、提取正文 200,000 字）；暂不支持网址抓取。
- 模型首次加载较慢，同一进程串行执行 OCR，避免线程不安全。
- 个人数据保存在 `backend/data/`；推荐从“数据管理”导出包含一致数据库快照与原文件的完整备份。
- 词汇统计、反馈与事件是真实持久化数据；推荐模型目前是 LLM/规则，不是训练好的推荐系统。
- 可在 OCR、LLMService、候选生成和排序接口分别扩展扫描 PDF、Embedding、Reranker 等能力。当前不引入向量数据库或知识图谱基础设施。

实现参考：[FastAPI 文件上传](https://fastapi.tiangolo.com/reference/uploadfile/)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)、[Vite 代理配置](https://vite.dev/config/server-options.html#server-proxy)。
