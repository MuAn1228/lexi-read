# 阿里云 / VPS 后端部署

当前选择是阿里云中国站，使用支付宝付款；建议中国香港地域、Ubuntu 22.04/24.04、至少 2 核 / 2 GB 内存的轻量应用服务器，具体套餐与金额以下单页为准。可从[阿里云轻量应用服务器](https://www.aliyun.com/product/swas)创建。服务器尚未实际购买或连接，当前没有可用的公网后端。

本目录也可用于其他兼容的 Linux 云服务器。前端继续由 GitHub Pages 托管，服务器运行一个 FastAPI 进程和 Caddy HTTPS 代理。阿里云支持[导入 SSH 公钥并绑定实例](https://help.aliyun.com/zh/simple-application-server/user-guide/manage-key-pairs-linux)，控制台绑定的密钥用于 root 用户。

## 准备条件

- x86_64 / amd64 云服务器，Ubuntu 22.04 或 24.04，至少 2 GB 内存。PaddleOCR 首次加载和镜像构建可能需要更多内存、磁盘与等待时间；2 GB 不是已实测的容量保证。
- 安装 [Docker Engine 与 Compose 插件](https://docs.docker.com/engine/install/ubuntu/)，确认 `docker compose version` 可运行，并按需配置 Docker 的开机启动。
- 一个自己控制的 API 域名。先把它的 DNS A 记录指向服务器公网 IPv4；若添加 AAAA 记录，IPv6 也必须能到达此服务器。
- 云安全组和主机防火墙允许公网 TCP 80、443。SSH 管理端口仅允许自己的管理来源。Compose 只发布 80、443；后端 10000 和 Caddy 管理接口 2019 无需开放。
- 使用本机 SSH 客户端连接。SSH 私钥和 Agnes 密钥只在自己的机器或服务器中使用，不发送到聊天、不写入仓库。

域名 DNS 和 80、443 连通后，[Caddy 会自动申请和续期 HTTPS 证书](https://caddyserver.com/docs/automatic-https)。没有可用域名时，先完成域名准备再启动本模板。

## 配置和启动

把仓库代码克隆到服务器，在仓库目录执行：

```bash
cd deploy/vps
umask 077
cp .env.example .env
cp backend.env.example backend.env
chmod 600 .env backend.env
nano .env
nano backend.env
```

在 `.env` 中填写 `API_DOMAIN=你的实际API域名`，只写主机名，不带 `https://`、路径或端口。在 `backend.env` 中填写自己的 `LLM_API_KEY`；Agnes 3.0 Flash 的其他参数已预设。密钥若包含 `$` 或 `#`，使用单引号包裹完整值，避免 Compose 的插值或注释解析。编辑后先检查自己填写的值，不要把配置内容复制到聊天。

```bash
docker compose config --quiet
docker compose build api
docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose up -d
docker compose ps
curl --fail --show-error "https://$(sed -n 's/^API_DOMAIN=//p' .env)/api/health"
```

健康响应应包含 `"status":"ok"` 和 `"auth_mode":"guest"`。上面的健康检查只验证 API 与公开访客模式；还需在网页上传测试图片，确认实际 OCR 和 Agnes 释义可用。首次 OCR 会下载模型并存入数据卷，耗时与服务器网络有关。

Compose 文件将 `APP_MODE=public`、数据目录和端口固定，并设置 `CORS_ORIGINS=https://muan1228.github.io`；源地址不含 `/lexi-read/` 路径。如需更换前端域名，请修改 `compose.yaml` 中的 CORS 后重建容器。后端通过 `serve_public.py` 以一个 worker 启动，不要横向扩容此 SQLite 部署。

## 连接 GitHub Pages

在 GitHub 仓库的 **Settings → Secrets and variables → Actions → Variables**，把仓库变量 `VITE_API_ORIGIN` 设为 `https://你的实际API域名`，不含 `/api` 或其他路径。密钥保留在服务器的 `backend.env` 中。

在 **Actions → GitHub Pages → Run workflow** 运行发布流程。流程会先检查后端健康状态和公开访客模式。成功后打开 `https://muan1228.github.io/lexi-read/`，再用两个浏览器检查文档和词库隔离。

## 更新、数据和排查

在服务器上更新仓库代码后，从 `deploy/vps` 执行 `docker compose up -d --build`。容器设置 `restart: unless-stopped`，Docker 服务运行时会重启异常退出的进程；健康状态为 unhealthy 本身不会触发 Docker 自动重启。

`lexi-data` 命名卷挂载在 `/var/data/lexi`，保存 SQLite、上传原图、OCR 模型和缓存。`caddy-data` 保存证书与私钥，`caddy-config` 保存 Caddy 配置。普通 `docker compose down` 保留这些卷；`down --volumes` 和卷清理会删除数据，不用于日常更新。备份 SQLite 和上传文件时先停止 API，再使用自己可信的备份流程复制数据卷并加密保存在服务器外。

```bash
docker compose logs --tail=100 api caddy
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

日志可能含请求信息，分享前自行删除私密内容。Caddy 管理接口仅监听它自己容器的 `localhost:2019`，无需对外发布。修改 Caddyfile 后用上面的 validate 和 reload；修改 `.env` 或 `backend.env` 后执行 `docker compose up -d` 使环境变量生效。排查 TLS 时先检查 DNS、云安全组、主机防火墙和 80、443 是否被其他服务占用。

参考：[Caddy 官方 Docker 镜像](https://hub.docker.com/_/caddy)、[Caddyfile 环境变量](https://caddyserver.com/docs/caddyfile-tutorial#environment-variables)、[Caddy 管理接口](https://caddyserver.com/docs/caddyfile/options#admin)、[Compose 环境变量](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)、[Compose 服务启动顺序](https://docs.docker.com/compose/how-tos/startup-order/)。
