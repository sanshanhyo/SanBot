# SanBot 常见问题

这份文档用于排查 SanBot 一键安装、NapCat 登录、群命令、JM 下载、JAV 查询、Telegram 转发和文件上传问题。

开始排查前，先运行：

```bash
sanbot status
sanbot doctor
```

常用日志命令：

```bash
sanbot logs bot
sanbot logs backend
sanbot logs napcat
```

日志会持续输出，按 `Ctrl+C` 只会退出日志查看，不会停止机器人。

> 向别人求助时可以提供报错码和必要日志，但必须删除服务器密码、Cookie、Token、Session String、手机号和其他登录信息。

---

## 安装与服务器

### SanBot 支持哪些系统？

一键安装器面向 Linux 服务器，推荐 Ubuntu 22.04、Ubuntu 24.04 或较新的 Debian，并支持 `x86_64` 和 `arm64`。

Windows 适合本地开发测试，不适合直接运行当前的一键安装脚本。

### 最低需要多大的服务器？

建议至少：

- 2 核 CPU
- 2GB 内存
- 10GB 可用磁盘
- 稳定的国际网络

下载速度通常更受数据源、线路质量和服务器带宽影响。增加线程不能突破服务器带宽，也可能触发限流。

### 为什么不建议同时运行很多下载任务？

每个任务都可能同时保存大量图片、生成 PDF 并上传群文件。并发过高会增加内存、磁盘和网络压力，也更容易被数据源限流。

小型服务器建议保持：

```text
MAX_CONCURRENT_JOBS=1
JM_DOWNLOAD_IMAGE_THREADS=8
JM_DOWNLOAD_PHOTO_THREADS=2
```

### 一行安装命令是否可以先检查再执行？

可以。先下载安装脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/sanshanhyo/SanBot/main/scripts/install.sh -o install.sh
```

查看内容：

```bash
less install.sh
```

确认后运行：

```bash
sudo bash install.sh
```

### 安装时提示必须使用 root 或 sudo

普通用户运行：

```bash
curl -fsSL https://raw.githubusercontent.com/sanshanhyo/SanBot/main/scripts/install.sh | sudo bash
```

如果已经以 `root` 登录且系统没有 `sudo`：

```bash
curl -fsSL https://raw.githubusercontent.com/sanshanhyo/SanBot/main/scripts/install.sh | bash
```

### 安装卡在 Docker 或镜像下载

先确认服务器可以访问 Docker 与 GitHub：

```bash
curl -I https://get.docker.com
curl -I https://github.com
```

然后检查 Docker：

```bash
systemctl status docker --no-pager
docker info
docker compose version
```

如果云服务商线路访问镜像仓库很慢，请更换线路或配置可信的镜像加速服务。不要随意执行来源不明的 Docker 换源脚本。

### 重新运行安装器会不会删除数据？

不会。检测到已有安装后，选择：

```text
保留现有功能开关、白名单、Cookie 和 Token，只升级程序 [true]
```

安装器会合并新增配置，并备份旧 `.env`。日常更新直接使用：

```bash
sanbot update
```

---

## NapCat 与 QQ 登录

### NapCat WebUI 打不开或提示连接被拒绝

依次检查：

1. 安装时是否选择公网开放 WebUI。
2. 云服务器安全组是否放行 TCP 6099。
3. 系统防火墙是否允许 6099。
4. NapCat 容器是否运行。

```bash
sanbot status
sanbot logs napcat
```

查看当前 WebUI 地址：

```bash
sanbot webui
```

WebUI 登录完成后应执行 `sanbot close-webui`，不要长期公开 6099 端口。

### WebUI Token 在哪里？

一键安装完成时会直接显示带 Token 的 WebUI 地址。之后可以运行：

```bash
sanbot webui
```

不要把这个地址完整截图发到公开群，因为 URL 中包含 WebUI Token。

### QQ 扫码成功后仍显示离线

先等待十几秒，再运行：

```bash
sanbot doctor
sanbot logs napcat
```

如果日志提示登录失效、异地登录或被踢下线，需要重新扫码。QQ 登录状态由 QQ 和 NapCat 管理，SanBot 无法绕过登录验证。

### 机器人运行一段时间后突然不回复

检查三个服务：

```bash
sanbot status
```

如果服务都在运行，再查看 NapCat 是否仍在线：

```bash
sanbot doctor
sanbot logs napcat
```

常见原因是 QQ 登录失效。重新扫码后，Bot 通常会自动恢复 WebSocket 连接。

### 是否需要手动配置 OneBot 3000 和 3001？

使用当前一键安装器时不需要。脚本会自动创建：

- OneBot HTTP 服务端：3000
- OneBot WebSocket 服务端：3001

这两个端口只在 Docker 内部网络使用，不会映射到公网。

---

## 群命令与权限

### Bot 已连接 NapCat，但群里没有反应

依次确认：

1. 使用 QQ 的 `@` 选择了机器人，而不是只输入昵称。
2. 发送者不是机器人自己。
3. 群号在 `ALLOWED_GROUP_IDS` 中。
4. 对应功能的 `ENABLE_*` 开关为 `true`。
5. 对应 `*_ALLOWED_GROUP_IDS` 包含当前群。
6. `.env` 中的 `BOT_QQ_ID` 与 NapCat 登录 QQ 一致。

然后查看：

```bash
sanbot logs bot
```

### 为什么提示“未知命令”？

发送：

```text
@机器人 帮助
```

注意命令格式：

- JM 编号必须写成 `JM123456`，只发送 `123456` 不会触发。
- 番号查询使用 `JAV SSIS-123`。
- JM 搜索使用 `JM搜索 关键词`。
- AV 标题搜索使用 `AV搜索 中文标题`。
- 演员搜索使用 `演员搜索 演员名`。

### 为什么 `@机器人 帮助` 没有发图片？

新版会发送 `assets/main.png`。如果图片文件缺失、共享数据目录不可写或 NapCat 图片发送失败，会自动回退到文字帮助。

检查：

```bash
sanbot logs bot
```

Docker 部署还可以确认镜像已经更新：

```bash
sanbot update
```

### 机器人拉进任何群都能使用吗？

取决于白名单：

- `ALLOWED_GROUP_IDS` 为空：机器人加入的群都可以触发基础命令。
- `ALLOWED_GROUP_IDS` 有值：只有列出的群可以使用。
- 功能级白名单有值：该功能还要额外通过自己的群白名单。

MissAV、剧照和 Telegram 等功能建议始终配置独立白名单。

### 为什么提示 `FEATURE_NOT_ALLOWED` 或 `GROUP_NOT_ALLOWED`？

- `GROUP_NOT_ALLOWED`：当前群不在全局群白名单。
- `FEATURE_NOT_ALLOWED`：功能已关闭，或当前群不在该功能白名单。

运行 `sanbot config` 检查相应开关，保存后执行：

```bash
sanbot restart
```

---

## JMComic 下载

### 如何获取或更新 AVS Cookie？

请查看 **[一键安装教程中的 AVS 获取指南](./tutorial.md#avs-获取指南)**。

Cookie 相当于登录凭据。不要把它发到群里、截图公开或提交到 GitHub。

### 提示 `JM_NOT_FOUND`

可能原因：

- JM 编号不存在。
- 编号输入错误。
- 数据源暂时没有返回内容。
- Cookie 或当前服务器 IP 无法访问。

先在浏览器确认编号，再稍后重试。

### 提示 `JM_DOWNLOAD_FAILED`

这是一类下载失败，常见原因包括：

- JM Cookie 失效。
- 服务器 IP 被地区限制。
- 数据源限流或临时故障。
- 服务器代理不可用。
- 图片请求连续失败。

查看后端日志：

```bash
sanbot logs backend
```

不要立即把线程调得更高；高并发通常会让限流更严重。

### 提示 IP 地区禁止访问或被识别为爬虫

这通常不是 PDF 逻辑问题，而是 JM 数据源拒绝当前出口 IP。可以依次尝试：

1. 更新自己的 AVS Cookie。
2. 降低下载线程并稍后重试。
3. 检查代理是否能从容器访问。
4. 更换具有不同出口 IP 的合规服务器线路。

不要使用来历不明的公共 Cookie 或代理。

### 为什么下载到一半卡住？

SanBot 使用独立下载子进程，并配置两层超时：

- `JOB_TIMEOUT_SECONDS`：任务总超时。
- `JOB_STALL_TIMEOUT_SECONDS`：长时间没有新文件写入时判定卡住。

超时后任务会失败并释放队列，不应永久堵住后续任务。管理者也可以在群内取消任务。

查看进度与日志：

```bash
sanbot logs backend
```

### 提示 `JOB_TIMEOUT` 或 `JOB_STALLED`

- `JOB_TIMEOUT`：任务运行总时间超过限制。
- `JOB_STALLED`：指定时间内没有新的文件变化。

偶发时可以重试；频繁发生时应检查网络、Cookie、磁盘空间和下载线程，而不是直接关闭超时保护。

### 为什么超过 300 页会被拒绝？

超大漫画会显著增加内存、磁盘、转换时间和上传失败概率，因此默认：

- 超过 100 页需要二次确认。
- 超过 300 页自动拒绝。

对应配置：

```text
LARGE_ALBUM_WARNING_PAGES=100
MAX_ALBUM_PAGES=300
```

可以修改，但应先确认服务器资源充足。

### 页数探测为什么和实际页数不同？

页面数据可能包含分章、重复记录、动态加载内容或数据源异常。SanBot 会在下载前尽量校验，并以实际下载结果为准。明显异常的超大页数会被上限保护拦截。

如果同一编号稳定复现，请保留 JM 编号、时间和后端日志用于排查。

### 提示 `PDF_GENERATION_FAILED` 或 `PDF_INVALID`

先检查：

```bash
df -h
sanbot logs backend
```

常见原因：

- 图片没有完整下载。
- 图片文件损坏或格式异常。
- 磁盘空间不足。
- 下载子进程提前退出。
- PDF 工具无法读取某张图片。

不要只看群内简短提示，后端日志会保留详细原因。

### 为什么提示重复任务或任务上限？

SanBot 会限制同一用户和同一群的活跃任务，避免一个用户占满下载队列。

常见报错码：

- `DUPLICATE_ACTIVE_JOB`：同一本漫画已有进行中的任务。
- `USER_ACTIVE_JOB_LIMIT`：该用户活跃任务达到上限。
- `GROUP_ACTIVE_JOB_LIMIT`：该群活跃任务达到上限。
- `ACTIVE_JOB_LIMIT`：全局活跃任务达到上限。

等待当前任务完成，或由管理者取消异常任务。

---

## PDF 与群文件上传

### 提示 `NAPCAT_UPLOAD_FAILED`

这表示 PDF 通常已经生成，但 NapCat 或 QQ 群文件上传失败。检查：

```bash
sanbot logs bot
sanbot logs napcat
```

常见原因：

- QQ 富媒体通道临时异常。
- 群文件权限受限。
- 文件过大。
- QQ 登录状态不稳定。
- NapCat 无法读取共享文件路径。

SanBot 会自动重试；大 PDF 还会拆成多个分卷上传。

### 为什么只有某一个群永远上传失败？

如果同一个很小的测试文件在其他群可以上传，只有某群返回 `rich media transfer failed`，通常是该群的群文件权限或 QQ 富媒体通道异常，不是 PDF 下载失败。

建议群主或管理员检查群文件权限，也可以尝试让机器人获得群管理员权限。文本和图片能发送，不代表群文件上传通道一定正常。

### 为什么群文件名会变短？

QQ 对上传文件名的字节数和显示长度有限制。SanBot 会保留 JM 编号，并在必要时裁剪标题。分卷名称会把 `partXX-ofXX` 放在较前位置，方便在群文件列表中识别。

### 上传失败后 PDF 是否还在？

任务文件会暂时保存在数据目录，并由缓存清理策略定期删除。需要人工处理时应尽快查看任务目录，不建议关闭缓存清理后长期堆积文件。

---

## JAV 元数据、预告片与剧照

### 小写番号为什么也应该能查询？

SanBot 会规范化番号大小写，例如 `ssis-123` 会转换为标准格式。如果小写番号稳定失败而大写成功，请记录输入、时间和后端日志，这通常属于解析回归。

### 提示 `JAV_NOT_FOUND`

可能原因：

- 番号不存在或格式错误。
- 当前数据源没有收录。
- 多个数据源暂时都没有结果。
- 失败结果仍在短期缓存中。

确认番号后稍等一段时间再试，或检查 `JAVLIBRARY_PROVIDER_ORDER`。

### 提示 `JAV_SOURCE_BLOCKED` 或 `JAV_FETCH_TIMEOUT`

- `JAV_SOURCE_BLOCKED`：源站返回验证页、拒绝访问或阻断当前请求。
- `JAV_FETCH_TIMEOUT`：数据源在总超时时间内没有返回结果。

建议检查 Cookie、服务器网络和数据源顺序。不要无限提高重试次数，否则一条查询可能长时间占用后端。

### 如何配置 JavDB/Javlibrary Cookie？

请查看 **[一键安装教程中的 Cookie 获取指南](./tutorial.md#javdbjavlibrary-cookie-获取指南)**。

Cookie 失效后，应重新从自己的浏览器获取。不要使用他人的登录会话。

### 为什么演员搜索找不到中文译名？

演员搜索会尝试：

- 原始输入名称
- 本地演员别名文件
- 已缓存别名
- 可选的在线别名解析

可以在 `/opt/sanbot/config/actor-aliases.yml` 中补充自己的中日文或罗马字别名，然后重启 SanBot。

### 为什么没有“预告片”按钮？

检查：

- `ENABLE_JAV_TRAILER=true`
- 当前群在 `JAV_TRAILER_ALLOWED_GROUP_IDS` 中
- 当前番号的数据源确实返回预告片

部分预告片只对登录用户可见，需要有效 Cookie。

### 为什么预告片提示 m3u8、转换失败或 `FFMPEG_NOT_FOUND`？

SanBot 会先下载 HLS/m3u8 资源，再使用 `ffmpeg` 转为本地 MP4。

- `FFMPEG_NOT_FOUND`：当前运行环境没有找到 ffmpeg。
- `TRAILER_MP4_TIMEOUT`：下载或转换超时。
- `TRAILER_HLS_DOWNLOAD_FAILED`：HLS 分片请求失败。
- `TRAILER_HLS_SEGMENTS_MISSING`：播放列表中的部分分片缺失。
- `TRAILER_MP4_TOO_LARGE`：最终视频超过配置上限。
- `NAPCAT_VIDEO_UPLOAD_FAILED`：MP4 已生成，但发送到 QQ 失败。

一键 Docker 镜像已经包含 ffmpeg。先运行 `sanbot update` 和 `sanbot doctor`，再查看 Bot 日志。

### 为什么部分小剧照被跳过？

SanBot 会跳过尺寸过小的缩略图，避免把低清预览混进剧照 PDF。相关配置：

```text
JAV_STILLS_MIN_IMAGE_WIDTH=300
JAV_STILLS_MIN_IMAGE_HEIGHT=200
```

设置为 `0` 可以关闭对应尺寸检查，但 PDF 质量可能下降。

### 为什么剧照或 MissAV 入口没有显示？

这些功能受多层限制：

- 功能总开关
- 功能独立群白名单
- 群人数上限
- 当前番号是否有对应资源

MissAV 默认关闭；剧照预览默认关闭。不要仅修改群白名单而忘记开启功能开关。

---

## Telegram 转发

### Bot Token 模式和 Telethon 模式有什么区别？

- **Bot Token 模式**：配置简单，但受 Telegram Bot API 文件大小和频道权限限制。
- **Telethon 模式**：使用用户会话，能力更完整，但需要 API ID、API Hash 和 Session，账号风险也更高。

不确定时建议关闭 TG 功能。

### 提示 `TG_BOT_TOKEN_MISSING` 或 `TG_SESSION_NOT_CONFIGURED`

- Bot 模式需要 `TG_BOT_TOKEN`。
- Telethon 模式需要 API 信息和有效 Session。

运行 `sanbot config` 修改后，再执行：

```bash
sanbot restart
```

### Bot Token 模式为什么不能下载大文件？

Bot API 模式受到 Telegram 官方接口限制。一键向导会把单文件上限设置得更保守。需要更大文件时只能评估 Telethon 模式，但应同时考虑账号安全、带宽和 QQ 上传限制。

### 自动拉取没有新内容时为什么不回复？

这是预期行为。自动拉取采用静默模式，没有未转发内容时不会在群里发送“暂无内容”。手动执行 `TG最新` 时才会返回明确结果。

### 为什么同一条内容只发到了一个群？

新版会按 `群号 + 频道 + Telegram 消息 ID` 分别记录转发状态。同一频道绑定多个群时，每个群都有独立进度。若仍出现串群，请提供群号、频道和消息 ID，并检查是否已经更新到最新版。

### TG 功能会增加 QQ 封号风险吗？

频繁发送图片、视频或相似内容会增加平台风控和被举报的风险。建议：

- 默认关闭 TG。
- 仅对白名单群开放。
- 限制单次拉取数量和自动拉取频率。
- 仅转发你有权访问和传播的内容。
- 使用专门的机器人账号。

---

## 数据、缓存与更新

### 数据保存在哪里？

一键安装目录为：

```text
/opt/sanbot
```

常见内容：

- `.env`：功能开关、白名单和 Token
- `config/`：JMComic、JAV 和演员别名配置
- `data/`：SQLite 数据库、任务文件和缓存
- `napcat/`：NapCat 登录与网络配置
- `backups/`：手动或更新前备份

### SanBot 会自动清理缓存吗？

会。任务文件、预览、上传临时文件、JAV 缓存和 TG 媒体都有各自保留时间。SQLite 任务与审计记录也会按配置清理。

不要把 `CACHE_CLEANUP_INTERVAL_SECONDS` 长期设为 `0`，除非你有其他磁盘清理方案。

### 如何检查磁盘空间？

```bash
df -h
du -sh /opt/sanbot/data /opt/sanbot/backups
```

不要在不确认路径的情况下使用递归删除命令。

### 如何备份？

```bash
sanbot backup
```

备份包含 `.env`、Compose 配置、SanBot 配置、NapCat 配置和 SQLite 数据库，不包含所有已下载漫画缓存。

### 如何更新？

```bash
sanbot update
sanbot doctor
```

更新会先备份，再拉取镜像并重启。遇到重大问题时保留更新前备份和日志。

### 修改 `.env` 后为什么没有生效？

保存后需要重启服务：

```bash
sanbot restart
```

变量名必须与 **[配置说明](./env.md)** 一致，布尔值使用 `true` 或 `false`。

### 如何卸载？

```bash
sanbot uninstall
```

该命令移除容器但保留 `/opt/sanbot` 数据。确认不再需要后再手动删除目录，删除前先运行 `sanbot backup`。

---

## 常见报错码速查

| 报错码 | 含义与优先检查项 |
| --- | --- |
| `BACKEND_UNAVAILABLE` | 后端不可用；检查 `sanbot status` 和后端日志 |
| `FEATURE_NOT_ALLOWED` | 功能关闭或当前群不在功能白名单 |
| `GROUP_NOT_ALLOWED` | 当前群不在全局白名单 |
| `JM_NOT_FOUND` | JM 编号不存在或数据源没有返回内容 |
| `JM_DOWNLOAD_FAILED` | JM 下载失败；检查 Cookie、网络、限流和后端日志 |
| `JOB_TIMEOUT` | 任务超过总超时 |
| `JOB_STALLED` | 任务长时间没有新文件写入 |
| `PDF_GENERATION_FAILED` | 图片转 PDF 失败；检查图片完整性、磁盘和日志 |
| `NAPCAT_UPLOAD_FAILED` | PDF 已生成，但群文件上传失败 |
| `JAV_NOT_FOUND` | 多个 JAV 数据源都没有找到该番号 |
| `JAV_SOURCE_BLOCKED` | JAV 数据源返回验证页或拒绝请求 |
| `JAV_FETCH_TIMEOUT` | JAV 查询超过总超时 |
| `FFMPEG_NOT_FOUND` | 当前环境没有找到 ffmpeg |
| `TRAILER_MP4_TIMEOUT` | 预告片下载或转换超时 |
| `NAPCAT_VIDEO_UPLOAD_FAILED` | 预告片已处理，但发送到 QQ 失败 |
| `TG_NOT_CONFIGURED` | Telegram 模式或凭据未完整配置 |

报错码用于快速分类，不等于完整原因。最终应结合相同时间附近的 Bot、Backend 或 NapCat 日志判断。

---

## 提交问题时应提供什么？

建议提供：

- SanBot 提交版本或镜像更新时间
- 使用的部署方式
- 报错码
- 触发问题的大致时间
- 是否只在某一个群发生
- `sanbot status` 输出
- 对应服务的必要日志片段

不要提供：

- `.env` 完整内容
- AVS 或 JAV Cookie
- NapCat、Telegram 或后端 Token
- Telegram Session String
- 服务器密码或 SSH 私钥

返回 **[项目主页](../README.md)** · 查看 **[一键安装教程](./tutorial.md)** · 查看 **[配置说明](./env.md)**
