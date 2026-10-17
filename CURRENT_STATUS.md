# Current Status

公网部署准备（2026-10-17）：已增加 GitHub Pages 前端配置、公开访客身份和全链路数据隔离、持久化 AI/OCR 尝试限额、Docker 与云端部署配置。两个独立浏览器验证了词库/文档隔离、认证原图和刷新恢复，前端 8 项测试及构建通过。GitHub Actions 后端 213 项测试与 Linux Docker 真实 OCR 检查均通过。代码已推送 MuAn1228/lexi-read，Pages 已启用 workflow 发布。

后端选择已改为阿里云中国站（支付宝），部署配置见 deploy/vps/README.md，原 Render 方案保留为可选。仍需实际服务器公网 IP、SSH 公钥绑定及 HTTPS 域名；完成后配置 Pages 的 VITE_API_ORIGIN 并发布。当前未声称公网网站已上线。

交付更新（2026-10-17）：TODO.md 中本轮 P0–P3 已完成。已补齐稳定词元归并、兼容迁移、逐词历史、规则掌握度、SQL 学习概览、明确的 Agent 学习输入、推荐反馈与个人词汇关系。当前后端回归为 178 项通过，前次生产构建及关键浏览器流程通过；正式数据库已备份升级，双击启动入口可用。当前仍是本机单用户 MVP。

Agnes 3.0 Flash 已接入本机配置：使用官方 `agnes-3.0-flash` 模型与 `https://apihub.agnes-ai.com/v1` 地址，按文档省略未列出的 `response_format` 参数，保留严格 JSON 校验和异常降级。真实连接检查中释义和推荐均为 `source=llm`，排序后 3 个关联词；检查仅使用合成阅读内容、不写学习数据。应用已重启，首页和健康接口通过。密钥仅保存于 Git 忽略的 `.env`；仓库模板仍默认离线。详细配置与验证见 README.md、VERIFICATION.md。

以下保留最初接手时的审计基线，便于追溯改动原因。

检查日期：2026-10-15。以下是接手时（`da7734a`）的基线，不按文件名推断；后续进度见 TODO.md。回归基线：33 tests passed。

| 功能 | 状态 | 实际情况 |
| --- | --- | --- |
| 图片上传 | ✅ 已完整实现 | 校验格式、内容、大小、像素；失败清理 |
| 图片预览 | ✅ 已完整实现 | 原图、缩放、OCR 文本切换 |
| OCR | ✅ 已完整实现 | PaddleOCR CPU、本地模型缓存、503 错误处理 |
| 单词级 bounding box | ✅ 已完整实现 | 原生坐标，缺失时明确标记 estimated |
| 原图单词交互 | ✅ 已完整实现 | 百分比缩放、选中与状态颜色 |
| 单词列表 | ✅ 已完整实现 | lemma 去重、搜索、状态筛选、键盘标记 |
| 已掌握 / 不认识 / 不确定标记 | ✅ 已完整实现 | PUT 后提交数据库，并同步同 lemma 出现位置 |
| SQLite 持久化 | ✅ 已完整实现 | 用户、文档、出现位置、词库、事件均持久化 |
| 用户词库 | 🟡 部分实现 | 缺 first_seen_at 和完整逐词学习历史 |
| lemma 词形还原 | 🟡 部分实现 | 固定小表，generalized 等未归并 |
| 单词出现次数统计 | ✅ 已完整实现 | 成功导入时按实际出现次数加总，刷新不加 |
| 当前句子提取 | ✅ 已完整实现 | 标点切分，支持跨 OCR 行；复杂排版仍有限制 |
| 上下文释义 | ✅ 已完整实现 | 词、句子、文章一起送模型；有限离线 fallback |
| VocabularyAgent | 🟡 部分实现 | 生成与排序分离；缺完整当前词历史与反馈 |
| 根据上下文解释 | ✅ 已完整实现 | LLMService 接收 sentence/article，不罗列词典 |
| 根据用户词库推荐关联词 | ✅ 已完整实现 | 读取词库状态、计数、最近事件 |
| Association Budget | ✅ 已完整实现 | schema 和排序均限制最多 3 |
| Top-K | ✅ 已完整实现 | 按真实评分排序并截取 3 |
| 排除已完全掌握词 | ✅ 已完整实现 | KNOWN 或 mastery>=0.85 过滤 |
| 推荐原因 | ✅ 已完整实现 | 模型/离线原因加个人状态提示 |
| 结构化输出 | ✅ 已完整实现 | Pydantic 校验长度、范围和 JSON，异常降级 |
| KNOWN | ✅ 已完整实现 | 用户标记状态 |
| UNKNOWN | ✅ 已完整实现 | 用户标记状态 |
| UNCERTAIN | ✅ 已完整实现 | 与未评估 null 区分 |
| mastery_score | 🟡 部分实现 | 可空字段存在，但 mark 总是重置为空 |
| seen_count | ✅ 已完整实现 | 按 lemma 累计出现次数 |
| lookup_count | ⚠️ 存在明显问题 | 自动显示释义也累计，不能代表主动查询 |
| last_seen_at | ✅ 已完整实现 | 导入时更新，非读请求更新 |
| LearningEvent | 🟡 部分实现 | 缺独立 lemma，原词与归一化词未分开 |
| MARK_KNOWN | ✅ 已完整实现 | 与状态更新同事务，保留前状态 |
| MARK_UNKNOWN | ✅ 已完整实现 | 同上 |
| MARK_UNCERTAIN | ✅ 已完整实现 | 同上 |
| LOOKUP | ⚠️ 存在明显问题 | 页面重挂载会造成无意查询事件 |
| VIEW_RELATED | ⚠️ 存在明显问题 | 生成整组推荐就写“查看”，无逐项查看证据 |
| 历史查询 | 🟡 部分实现 | 全局最近 100 条，没有逐词历史接口 |
| 高频生词 | ❌ 尚未实现 | 有底层计数，无聚合与页面 |
| 重复遇到的生词 | 🟡 部分实现 | 词库显示 seen_count，无专门分组/排序 |
| 学习统计 | 🟡 部分实现 | 前端四个状态计数，无 7 天趋势和低掌握排行 |
| 单词关联 | 🟡 部分实现 | 推荐是暂态列表，无关系表和反馈 |
| Personal Vocabulary Profile | 🟡 部分实现 | 原始词库送 Agent，无独立个人摘要 |

调用链证据：

- `api/routes.py → services/documents.import_image → ocr/OCRService.recognize → save_document → VocabularyService.observe`。
- `PUT vocabulary → VocabularyService.mark → Repository.ensure/event → commit`。
- `POST words/analyze → LLMService.analyze_word → VocabularyService.lookup`。
- `POST words/recommend → VocabularyAgent.recommend → LLMService.recommend_related_words → rank → event`。
- `App → ContextPanel/WordList/LibraryViews` 都使用真实 `/api` 请求；无假成功数据。

未发现已复现的启动、OCR、接口不匹配或页面崩溃故障。迁移风险：原 initialize_database 只有 create_all，不能直接给 ORM 新增字段而忽略现有数据库。后续采用兼容迁移与备份。

审计基线时真实 LLM 密钥未配置；后续 Agnes 真实联调结果见本文件开头及 VERIFICATION.md。
