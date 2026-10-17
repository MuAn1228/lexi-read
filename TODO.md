# Incremental Learning Loop

依据 CURRENT_STATUS.md 的真实调用链检查制定；每项完成测试后单独提交，不重建项目。

## P0：稳定与数据真实性

- [x] 原有 33 项测试基线通过，保留启动/OCR/状态同步/LLM 降级。
- [x] 主动点击才记录 LOOKUP；推荐生成不再伪装成 VIEW_RELATED。
- [x] 新增字段必须兼容迁移，迁移前 SQLite 在线备份，旧计数与事件保留。

## P1：个人学习闭环

- [x] 英文屈折词形归并与旧词库合并；保留派生词和概念名词区别。
- [x] first_seen_at，LearningEvent 原词/lemma/details；补齐旧记录。
- [x] 单词历史 API 与阅读侧栏：首次/最近、计数、上次标记、事件。
- [x] MasteryService：事件驱动规则、边界截断、记录前后值，不称为科学记忆模型。
- [x] SQL 学习统计：高频生词、重复查询、新增生词、长期未掌握、7 天数据、最低掌握度。
- [x] Dashboard 与 Personal Vocabulary Profile。

## P2：个性化 Agent 与反馈

- [x] 明确传入词/lemma/句子/文章、known/unknown/uncertain、当前词历史、最近事件。
- [x] 解释 importance、推荐 relation/priority；保持结构验证与 fallback。
- [x] 推荐曝光、逐项查看、认识/不认识、有用/没用反馈；可追溯到推荐项。
- [x] 掌握度与反馈参与排序；保留 Top-K<=3 和已掌握过滤。

## P3：个人词汇关系

- [x] SQLite VocabularyRelation 表，类型、来源、权重与更新时间。
- [x] 用户查看/反馈后记录或调整关系，幂等处理重试。
- [x] 查看个人关联；统计推荐展示/查看/加入生词/有用的真实口径。

## 每阶段验收

- [ ] 新旧测试、构建、关键浏览器流程。
- [ ] 在现有数据库升级启动，更新 README、验证记录并保持入口可用。

暂不实现：图数据库、微服务、支付、社交或复杂记忆模型。


