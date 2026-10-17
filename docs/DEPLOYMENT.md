# GitHub Pages + Render 部署

仓库：[MuAn1228/lexi-read](https://github.com/MuAn1228/lexi-read)。目标网页地址为 `https://muan1228.github.io/lexi-read/`；只有成功运行 Pages 部署后该地址才可使用。

GitHub Pages 负责静态网页，Render 运行 FastAPI、PaddleOCR、Agnes 调用与 SQLite。[GitHub Pages 是静态托管](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)，无法运行 Python 后端。

## 1. 部署后端

使用 [Render Blueprint](https://dashboard.render.com/select-repo?type=blueprint) 选择此仓库。也可使用 [从此仓库创建 Blueprint](https://render.com/deploy?repo=https://github.com/MuAn1228/lexi-read)。Render 会读取根目录 `render.yaml`，导入前核对资源和报价：

- 一台 `1c-2g`（1 CPU / 2 GB 内存）的 Web Service，Singapore 区域。
- 一块 5 GB 持久化磁盘，挂载于 `/var/data/lexi`，用于数据库、图片和模型。
- 服务和磁盘为付费资源，实际金额以 [Render 价格页面](https://render.com/pricing)及创建页面为准；不是免费套餐。

Render 的[持久化磁盘说明](https://render.com/docs/disks)要求使用付费服务。没有磁盘时，SQLite 和图片会随重启或部署丢失。模板仅使用一个实例和一个 Python worker，符合磁盘和进程内 OCR 并发限制。

首次创建时，把 Agnes 密钥填入 Render 提示的 **LLM_API_KEY**，其他参数已在模板中准备好。不要把密钥填入 GitHub 的 `VITE_` 变量，也不要上传本机 `.env`、数据库或阅读图片。

模板设置 `APP_MODE=public` 与 `CORS_ORIGINS=https://muan1228.github.io`。如果更换前端域名，请更新后端 CORS 为网页的源地址（不含 `/lexi-read/` 路径）。

部署完成后复制 Render 实际分配的 HTTPS 地址，例如 `https://实际服务名.onrender.com`。访问其 `/api/health`，应看到 `status=ok`、`auth_mode=guest`。健康检查不代表首次 OCR 模型已下载完成；首次上传可能较慢。

## 2. 发布 GitHub Pages

1. 仓库 **Settings → Pages → Source** 选择 **GitHub Actions**。
2. **Settings → Secrets and variables → Actions → Variables** 新建仓库变量 `VITE_API_ORIGIN`，值为上一步的 Render HTTPS 源地址，不带 `/api`。
3. **Actions → GitHub Pages → Run workflow**，选择 `main`。
4. 部署成功后打开 `https://muan1228.github.io/lexi-read/`。

工作流会验证后端健康且开启访客隔离，再根据 Pages 元数据设置资源路径、构建并发布。缺少后端地址时仅运行前端测试和构建，不发布一个无法连接服务的网页。后续推送 `main` 的前端变更会自动部署。

## 3. 公开版行为

每个浏览器初次访问时创建访客学习空间，服务器只保存凭证的哈希。API 请求使用 Bearer 凭证；原图也通过认证后加载，不在 URL 中传递凭证。不同访客的文档、词库、历史、反馈和关联均隔离，旧本机用户 1 不对公开访客开放。

凭证保存在该网站的浏览器存储中。刷新会继续使用原空间；清理存储或换浏览器会创建新空间，当前没有跨设备恢复功能。

默认按 UTC 日期限制尝试次数：全站每日 200 次 AI 请求、50 次 OCR；每位访客每日 20 次 AI、5 次 OCR。释义和推荐分别计一次，失败尝试也占额度。每天最多创建 200 个空间，同一地址每小时最多 10 个；每人最多 30 篇材料。同时最多处理 2 个 AI 请求、1 个 OCR 请求，超出返回 429 和 `Retry-After`。这些限额在后端环境变量中调整，计数保存在 SQLite，重启不会清零。它们限制请求量，模型费用还取决于输入、输出和供应商价格。

首次上线后用两个浏览器验证：各自导入示例并标记，彼此看不到对方的材料和标记；刷新后自己的数据仍在。上传 `samples/reading.png` 验证 Linux OCR，再主动查看释义及推荐确认 `source=llm`。

## 维护

模板使用手动后端部署。修改后在 Render 选择 Deploy latest commit，并等待迁移/启动成功；Pages 的前端更新独立进行。磁盘服务更新会短暂停机。

SQLite 数据备份应使用在线备份接口（`sqlite3.Connection.backup`），避免只复制正在写入的主文件而遗漏 WAL。数据库和 images 目录均需备份；模型缓存可重新下载。当前设计适用于小规模公开 MVP，扩容到多个实例前需要更换数据和文件存储方案。
