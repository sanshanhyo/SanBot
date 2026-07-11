# SanBot 配置说明

SanBot 使用 `.env` 管理功能开关、白名单、连接地址、任务限制和缓存策略。JMComic Cookie 主要保存在独立的 YAML 配置中。

一键安装后的配置文件位于：

```text
/opt/sanbot/.env
/opt/sanbot/config/jmcomic-option.yml
/opt/sanbot/config/javlibrary-option.yml
/opt/sanbot/config/actor-aliases.yml
```

推荐使用管理命令编辑 `.env`：

```bash
sanbot config
```

修改完成后重启：

```bash
sanbot restart
sanbot doctor
```

> `.env`、Cookie、Token 和 Telegram Session 都是敏感信息。不要提交到 GitHub，不要把完整配置发到群里，也不要使用来历不明的配置文件。

---

## 基本语法

每行使用 `变量名=值`：

```dotenv
ENABLE_JM_DOWNLOAD=true
MAX_CONCURRENT_JOBS=1
ALLOWED_GROUP_IDS=123456789,987654321
```

### 布尔值

统一使用小写：

```text
true
false
```

### QQ 号和群号列表

多个 ID 使用英文逗号分隔：

```dotenv
BOT_MANAGER_QQ_IDS=10001,10002
ALLOWED_GROUP_IDS=123456789,987654321
```

不要加入中文逗号。空格虽然会被部分解析器忽略，但仍建议不写。

### 时间与容量

- 名称包含 `SECONDS` 的值通常以秒为单位。
- 名称包含 `TTL` 的值表示缓存保留时间。
- 名称包含 `BYTES` 的值以字节为单位。

常用容量：

```text
20MB  = 20971520
50MB  = 52428800
100MB = 104857600
```

### 空值

下面表示没有配置：

```dotenv
JAVLIBRARY_COOKIE=
```

不要写成字符串 `null`、`None` 或 `undefined`。

### 注释

以 `#` 开头的整行是注释：

```dotenv
# 这里只允许两个群使用
ALLOWED_GROUP_IDS=123456789,987654321
```

敏感值后面不要追加行尾注释，避免 Cookie 或 Token 被截断。

---

## 白名单如何生效

一条群命令通常需要依次通过：

1. **全局群白名单**：`ALLOWED_GROUP_IDS`
2. **功能开关**：对应的 `ENABLE_*`
3. **功能群白名单**：对应的 `*_ALLOWED_GROUP_IDS`
4. **特殊限制**：例如 MissAV 和剧照的群人数上限

规则如下：

- `ALLOWED_GROUP_IDS` 为空：不限制机器人所在群。
- `ALLOWED_GROUP_IDS` 非空：只有列出的群能触发机器人。
- 功能白名单为空：不在全局白名单之外增加限制。
- 功能白名单非空：当前群还必须出现在该功能白名单中。

例如：

```dotenv
ALLOWED_GROUP_IDS=111111,222222
ENABLE_JAV_TRAILER=true
JAV_TRAILER_ALLOWED_GROUP_IDS=111111
```

结果是两个群都能使用基础功能，但只有 `111111` 可以请求预告片。

高风险功能建议始终显式设置独立白名单：

- `MISSAV_ALLOWED_GROUP_IDS`
- `JAV_STILLS_ALLOWED_GROUP_IDS`
- `JAV_STILLS_PDF_ALLOWED_GROUP_IDS`
- `TG_MIRROR_ALLOWED_GROUP_IDS`
- `TG_AUTO_FETCH_GROUP_IDS`

---

## 一键部署专用变量

这些变量由 `install.sh` 生成，通常不出现在本地开发使用的 `.env.example` 中。

| 变量 | 一键安装默认值 | 说明 |
| --- | --- | --- |
| `SANBOT_IMAGE` | `ghcr.io/sanshanhyo/sanbot:latest` | SanBot Docker 镜像。固定版本时可改成对应标签。 |
| `NAPCAT_IMAGE` | `mlikiowa/napcat-docker:latest` | NapCat Docker 镜像。 |
| `NAPCAT_UID` | 安装用户 UID | NapCat 容器写入挂载目录时使用的 UID。 |
| `NAPCAT_GID` | 安装用户 GID | NapCat 容器写入挂载目录时使用的 GID。 |
| `NAPCAT_ACCOUNT` | 机器人 QQ | NapCat 尝试登录的 QQ 账号。 |
| `NAPCAT_WEBUI_BIND` | 向导选择 | `0.0.0.0` 表示允许公网访问，`127.0.0.1` 表示仅本机访问。 |
| `NAPCAT_WEBUI_PORT` | `6099` | NapCat WebUI 映射到服务器的端口。 |
| `NAPCAT_WEBUI_TOKEN` | 随机生成 | WebUI 登录 Token。不要公开。 |
| `BACKEND_HOST` | `0.0.0.0` | 后端在容器内监听的地址。 |
| `BACKEND_PORT` | `8000` | 后端映射到服务器本机的端口。 |
| `LOG_LEVEL` | `INFO` | 日志等级，常用 `DEBUG`、`INFO`、`WARNING`、`ERROR`。 |

`NAPCAT_WEBUI_TOKEN` 同时写在 `/opt/sanbot/napcat/config/webui.json`。只修改 `.env` 会造成两边不一致；需要改 Token 时应同步修改文件，或重新运行安装器保留配置升级。

扫码完成后建议执行：

```bash
sanbot close-webui
```

该命令会把 `NAPCAT_WEBUI_BIND` 改为 `127.0.0.1` 并重建 NapCat 容器。

---

## Bot 与 NapCat

| 变量 | 本地默认值 | 说明 |
| --- | --- | --- |
| `BOT_QQ_ID` | 无，必填 | NapCat 登录的机器人 QQ，用于验证结构化 `@` 是否指向机器人。 |
| `NAPCAT_WS_URL` | `ws://127.0.0.1:3001` | OneBot 11 WebSocket 服务端地址，用于接收群事件。Docker 一键安装使用 `ws://napcat:3001`。 |
| `NAPCAT_HTTP_URL` | `http://127.0.0.1:3000` | OneBot 11 HTTP 服务端地址，用于发消息和上传文件。Docker 一键安装使用 `http://napcat:3000`。 |
| `NAPCAT_ACCESS_TOKEN` | 空 | OneBot HTTP/WS 鉴权 Token。一键安装会随机生成。 |
| `NAPCAT_HTTP_TIMEOUT_SECONDS` | `60` | 普通 NapCat HTTP API 调用超时。 |
| `NAPCAT_UPLOAD_TIMEOUT_SECONDS` | `900` | 文件上传超时，大 PDF 应保留较长时间。 |
| `NAPCAT_MAX_UPLOAD_BYTES` | `104857600` | 单个上传文件上限，超过后尝试拆分 PDF。 |
| `NAPCAT_MAX_UPLOAD_FILENAME_BYTES` | `96` | QQ 群文件展示名称的最大字节数，超出会裁剪。 |
| `NAPCAT_UPLOAD_RETRIES` | `5` | 单个文件上传失败后的重试次数。 |

`NAPCAT_ACCESS_TOKEN` 必须与 NapCat 的 OneBot HTTP 和 WebSocket 配置一致。一键安装对应文件是：

```text
/opt/sanbot/napcat/config/onebot11_机器人QQ.json
```

不要只改 `.env` 中的 Token，否则 Bot 会无法连接 NapCat。

---

## 机器人展示、管理者与全局安全

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BOT_LANG` | `zh_CN` | 群内语言文件名，对应 `i18n/zh_CN.json`。 |
| `BOT_I18N_DIR` | 空 | 自定义 i18n 目录；留空使用项目内目录。一键 Docker 使用 `/app/i18n`。 |
| `BOT_DISPLAY_NAME` | `SanBot` | 机器人介绍中显示的名称。 |
| `BOT_MANAGER_NAME` | 空 | 管理者展示名称。 |
| `BOT_MANAGER_QQ` | 空 | 介绍文案中显示的单个管理者 QQ。 |
| `BOT_MANAGER_QQ_IDS` | 空 | 拥有机器人管理权限的 QQ 列表。 |
| `ALLOWED_GROUP_IDS` | 空 | 全局群白名单；空表示不限制群。 |
| `BOT_ALLOWED_GROUP_IDS` | 空 | 旧版全局群白名单，仅当 `ALLOWED_GROUP_IDS` 为空时读取。 |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `60` | Bot 检查后端健康状态的间隔；`0` 关闭。 |
| `HEALTH_NOTIFY_GROUP_IDS` | 空 | 后端异常与恢复通知群；空时使用全局白名单。 |

`BOT_MANAGER_QQ` 主要用于展示，真正的管理权限由 `BOT_MANAGER_QQ_IDS` 控制。建议两者都填写。

---

## 后端连接

| 变量 | 本地默认值 | 说明 |
| --- | --- | --- |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Bot 调用 FastAPI 后端的地址。Docker 使用 `http://backend:8000`。 |
| `BACKEND_API_TOKEN` | 空 | Bot 与后端之间的鉴权 Token。一键安装会随机生成。 |

一键安装中 Bot 和 Backend 读取同一个 `.env`，因此修改 `BACKEND_API_TOKEN` 后重启全部服务即可同步生效。

---

## JM 下载、搜索与排行榜

### 功能开关和白名单

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENABLE_JM_DOWNLOAD` | `true` | 开启 `@机器人 JM123456` 查询与下载。 |
| `JM_DOWNLOAD_ALLOWED_GROUP_IDS` | 空 | JM 下载功能群白名单。 |
| `ENABLE_JM_SEARCH` | `true` | 开启 `@机器人 JM搜索 关键词`。优先于旧变量。 |
| `ENABLE_SEARCH` | `true` | 兼容旧版本，同时控制后端搜索接口。 |
| `JM_SEARCH_ALLOWED_GROUP_IDS` | 空 | JM 搜索群白名单。 |
| `ENABLE_JM_RANKING` | `true` | 开启 JM 日榜、周榜、月榜。 |
| `JM_RANKING_ALLOWED_GROUP_IDS` | 空 | JM 排行榜群白名单。 |

### 搜索和排行榜参数

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SEARCH_TIMEOUT_SECONDS` | `20` | JM 搜索子进程超时。 |
| `SEARCH_RESULT_LIMIT` | `5` | JM 搜索返回数量，程序最大限制为 10。 |
| `SEARCH_CONFIRM_TIMEOUT_SECONDS` | `600` | 等待用户回复搜索结果序号的时间。 |
| `RANKING_TIMEOUT_SECONDS` | `20` | JM 排行榜获取超时。 |
| `RANKING_RESULT_LIMIT` | `10` | JM 与 JavDB 排行榜返回数量，程序最大限制为 20。 |

`ENABLE_JM_SEARCH` 是当前主开关。若它存在，程序不会再使用 `ENABLE_SEARCH` 作为 Bot 端开关；为了前后端一致，建议两者保持相同。

---

## JAV 元数据查询与搜索

### 总开关、子功能和白名单

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENABLE_JAVLIBRARY` | `true` | JAV 元数据总开关。关闭后番号查询、搜索和 DB 排行榜都不可用。 |
| `ENABLE_JAV_QUERY` | `true` | 开启 `@机器人 JAV SSIS-123`。 |
| `JAV_QUERY_ALLOWED_GROUP_IDS` | 空 | 番号详情查询群白名单。 |
| `ENABLE_AV_SEARCH` | `true` | 开启 AV 中文标题搜索。 |
| `AV_SEARCH_ALLOWED_GROUP_IDS` | 空 | AV 标题搜索群白名单。 |
| `ENABLE_ACTOR_SEARCH` | `true` | 开启演员搜索。 |
| `ACTOR_SEARCH_ALLOWED_GROUP_IDS` | 空 | 演员搜索群白名单。 |
| `ENABLE_DB_RANKING` | `true` | 开启 JavDB 日榜、周榜、月榜。 |
| `DB_RANKING_ALLOWED_GROUP_IDS` | 空 | JavDB 排行榜群白名单。 |

### 请求、数据源与缓存

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `JAVLIBRARY_TIMEOUT_SECONDS` | `8` | 单个数据源请求超时。 |
| `JAVLIBRARY_TOTAL_TIMEOUT_SECONDS` | `15` | 一次番号查询尝试多个源的总超时。 |
| `JAVLIBRARY_CACHE_TTL_SECONDS` | `604800` | 成功结果缓存，默认 7 天。 |
| `JAVLIBRARY_FAILURE_CACHE_TTL_SECONDS` | `60` | 普通失败缓存。 |
| `JAVLIBRARY_NOT_FOUND_CACHE_TTL_SECONDS` | `86400` | 未找到番号的缓存，默认 1 天。 |
| `JAVLIBRARY_BLOCKED_CACHE_TTL_SECONDS` | `120` | 数据源阻断请求的失败缓存。 |
| `JAVLIBRARY_TIMEOUT_CACHE_TTL_SECONDS` | `60` | 查询超时的失败缓存。 |
| `JAVLIBRARY_BASE_URL` | `https://www.javlibrary.com` | Javlibrary 基础地址。 |
| `JAVLIBRARY_LANGUAGE` | `cn` | Javlibrary 语言路径，常用 `cn` 或 `en`。 |
| `JAVLIBRARY_PROVIDER_ORDER` | `javdb,javlibrary,jav321,javbus` | 数据源尝试顺序，从左到右。 |
| `JAVDB_BASE_URL` | `https://javdb.com` | JavDB 基础地址。 |
| `JAVBUS_BASE_URL` | `https://www.javbus.com` | JavBus 基础地址。 |
| `JAV321_BASE_URL` | `https://www.jav321.com` | Jav321 基础地址。 |
| `JAVLIBRARY_FETCHER` | `curl` | 抓取模式：`curl`、`http` 或 `browser`。 |
| `JAVLIBRARY_USER_AGENT` | 空 | 自定义 User-Agent；空时使用内置浏览器值。 |
| `JAVLIBRARY_COOKIE` | 空 | JAV 数据源 Cookie，可填写标准 Cookie 请求头。 |
| `JAVLIBRARY_PROXY` | 空 | HTTP/HTTPS 代理地址，空表示直连。 |
| `JAVLIBRARY_IMPERSONATE` | `random` | curl-cffi 浏览器指纹，可填 `random` 或受支持的具体值。 |
| `JAVLIBRARY_RETRY_TIMES` | `1` | 临时网络错误重试次数。多数据源会继续尝试下一个源。 |

数据源阻断时不建议盲目提高 `JAVLIBRARY_RETRY_TIMES`。重试过多会让一次查询长期占用后端。

Docker 容器中的 `127.0.0.1` 指容器自己。若 `JAVLIBRARY_PROXY=http://127.0.0.1:7890`，宿主机代理通常无法被容器访问，需要让代理监听容器可达地址并正确配置防火墙。

### Browser 抓取模式

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `JAVLIBRARY_BROWSER_PROFILE_DIR` | `./data/javlibrary-browser` | 持久化浏览器资料目录；Docker 使用 `/app/data/javlibrary-browser`。 |
| `JAVLIBRARY_BROWSER_CHANNEL` | 空 | 本机浏览器通道，例如 `chrome`、`msedge`；空时使用 Playwright Chromium。 |
| `JAVLIBRARY_BROWSER_HEADLESS` | `false` | 是否无头运行浏览器。首次人工验证建议 `false`。 |
| `JAVLIBRARY_BROWSER_WAIT_SECONDS` | `120` | 等待人工完成验证的时间。 |

标准 Docker 镜像默认使用 `curl`，没有为交互式浏览器验证准备桌面环境。服务器部署优先使用多数据源与有效 Cookie。

### 演员别名与后续操作

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `JAV_ACTOR_ALIAS_PATH` | `./config/actor-aliases.yml` | 演员中文译名与别名文件；Docker 使用 `/app/config/actor-aliases.yml`。 |
| `JAV_ACTOR_ALIAS_ONLINE` | `true` | 是否启用在线别名解析。 |
| `JAV_ACTOR_ALIAS_TIMEOUT_SECONDS` | `4` | 在线别名解析超时。 |
| `JAV_ACTOR_ALIAS_CANDIDATE_LIMIT` | `6` | 一次演员搜索最多尝试的候选名称。 |
| `JAV_ACTION_TIMEOUT_SECONDS` | `300` | 番号查询后等待用户回复预告片、剧照等操作的时间。 |

自定义别名示例：

```yaml
aliases:
  桥本有菜:
    - 橋本ありな
    - Hashimoto Arina
```

---

## JAV 资源页与预告片

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENABLE_JAV_RESOURCE_PAGE` | `true` | 显示 JavDB 外部资源页入口。 |
| `JAV_RESOURCE_PAGE_ALLOWED_GROUP_IDS` | 空 | 资源页入口群白名单。 |
| `ENABLE_JAV_TRAILER` | `true` | 允许下载、转换并发送预告片。 |
| `JAV_TRAILER_ALLOWED_GROUP_IDS` | 空 | 预告片群白名单。 |
| `JAV_TRAILER_FFMPEG_PATH` | `ffmpeg` | ffmpeg 可执行文件路径。 |
| `JAV_TRAILER_CONVERT_TIMEOUT_SECONDS` | `180` | HLS/m3u8 下载和 MP4 转换超时。 |
| `JAV_TRAILER_MAX_BYTES` | `104857600` | 最终预告片 MP4 大小上限。 |
| `JAV_TRAILER_COOKIE` | 空 | 预告片请求 Cookie；空时复用 `JAVLIBRARY_COOKIE`。 |
| `JAV_TRAILER_IMPERSONATE` | `random` | 预告片下载使用的 curl-cffi 浏览器指纹。 |

一键 Docker 镜像已包含 ffmpeg。修改 `JAV_TRAILER_FFMPEG_PATH` 前，应先在运行环境中确认该命令确实存在。

---

## JAV 剧照与剧照 PDF

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENABLE_JAV_STILLS` | `false` | 开启剧照预览图发送。 |
| `JAV_STILLS_ALLOWED_GROUP_IDS` | 空 | 剧照预览群白名单。 |
| `JAV_STILLS_MAX_COUNT` | `3` | 一次最多发送的预览图数量，程序最大限制为 6。 |
| `JAV_STILLS_MAX_GROUP_MEMBERS` | `150` | 剧照入口允许的最大群人数；`0` 不检查。 |
| `ENABLE_JAV_STILLS_PDF` | `true` | 在剧照操作中打包并上传 PDF。 |
| `JAV_STILLS_PDF_ALLOWED_GROUP_IDS` | 空 | 剧照 PDF 群白名单。 |
| `JAV_STILLS_PDF_MAX_IMAGES` | `0` | PDF 最多打包图片数；`0` 表示不限量。 |
| `JAV_STILLS_PDF_DOWNLOAD_CONCURRENCY` | `4` | 剧照下载并发，程序最大限制为 8。 |
| `JAV_STILLS_PDF_DOWNLOAD_TIMEOUT_SECONDS` | `60` | 单张剧照下载超时。 |
| `JAV_STILLS_MAX_IMAGE_BYTES` | `8388608` | 单张剧照最大下载大小，默认 8MB。 |
| `JAV_STILLS_MIN_IMAGE_WIDTH` | `300` | PDF 最小图片宽度；低于会跳过，`0` 不检查。 |
| `JAV_STILLS_MIN_IMAGE_HEIGHT` | `200` | PDF 最小图片高度；低于会跳过，`0` 不检查。 |

`ENABLE_JAV_STILLS_PDF=true` 不会单独创造群命令；用户需要先通过已开启的剧照操作触发。建议剧照预览和 PDF 使用相同白名单。

---

## MissAV 外部播放入口

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENABLE_MISSAV_LINK` | `false` | 开启外部播放链接，默认关闭。 |
| `MISSAV_BASE_URL` | `https://missav.live` | 外部播放链接基础地址。 |
| `MISSAV_ALLOWED_GROUP_IDS` | 空 | MissAV 独立群白名单；开启时建议必填。 |
| `MISSAV_MAX_GROUP_MEMBERS` | `150` | 允许显示入口的最大群人数；`0` 不检查。 |

该功能只发送外部链接，不代理或下载影片。即使总开关开启，群不在独立白名单或群人数超过上限时，入口也会隐藏。

---

## Telegram 频道镜像

### 开关、白名单与自动拉取

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENABLE_TG_MIRROR` | `false` | Telegram 镜像总开关。 |
| `TG_MIRROR_ALLOWED_GROUP_IDS` | 空 | 手动 TG 命令群白名单。 |
| `ENABLE_TG_AUTO_FETCH` | `false` | 开启静默自动拉取。要求 TG 总开关同时开启。 |
| `TG_AUTO_FETCH_GROUP_IDS` | 空 | 固定自动拉取目标群；空时读取后端已有频道绑定。 |
| `TG_AUTO_FETCH_INTERVAL_SECONDS` | `3600` | 自动拉取间隔，最小按程序限制为 60 秒。 |
| `TG_AUTO_FETCH_LIMIT` | `5` | 每个群单次自动发送数量，程序最大限制为 10。 |

自动拉取没有新内容时不会发送提示。转发记录按群分别保存，同一频道绑定多个群时，每个群有独立进度。

### 登录模式与凭据

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TG_MIRROR_MODE` | `telethon` | `telethon` 使用用户会话，`bot` 使用 Bot Token。 |
| `TG_BOT_TOKEN` | 空 | Bot 模式的 BotFather Token。 |
| `TG_API_ID` | 空 | Telethon 模式的 Telegram API ID。 |
| `TG_API_HASH` | 空 | Telethon 模式的 API Hash。 |
| `TG_SESSION_STRING` | 空 | Telethon 字符串 Session，优先使用。 |
| `TG_SESSION_PATH` | 空 | Telethon 本地 Session 文件；Docker 一键安装使用 `/app/data/telegram.session`。 |

选择模式后只填写该模式需要的凭据。所有 Telegram 凭据都不得提交到 Git。

### 下载和缓存

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TG_MAX_FILE_BYTES` | `104857600` | 单个 TG 媒体大小上限。Bot 模式一键安装会设置为 `20971520`。 |
| `TG_FETCH_LIMIT` | `5` | 手动 `TG最新` 默认拉取数量，程序最大限制为 10。 |
| `TG_SCAN_LIMIT` | `30` | 每个频道向前扫描消息数量，程序最大限制为 100。 |
| `TG_MEDIA_CACHE_TTL_SECONDS` | `86400` | 已下载 TG 媒体缓存保留时间，默认 1 天。 |

`TG_SCAN_LIMIT` 是扫描范围，不是一定发送的数量。扫描到已发送记录、无媒体消息或超大文件时会跳过。

---

## 历史记录与管理员命令

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENABLE_HISTORY` | `true` | 开启用户任务历史查询。 |
| `HISTORY_ALLOWED_GROUP_IDS` | 空 | 任务历史查询群白名单。 |
| `ENABLE_ADMIN_COMMANDS` | `true` | 开启状态、队列、审计、取消和清理等管理员命令。 |
| `ADMIN_ALLOWED_GROUP_IDS` | 空 | 管理员命令群白名单。 |

管理员命令还会校验发送者是否为群主、群管理员或 `BOT_MANAGER_QQ_IDS` 中的机器人管理者。

---

## 任务队列、确认与超时

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_CONCURRENT_JOBS` | `1` | 后端同时执行的下载任务数。 |
| `MAX_ACTIVE_JOBS_PER_GROUP` | `3` | 每个群允许的排队中或运行中任务；`0` 不限制。 |
| `MAX_ACTIVE_JOBS_PER_USER` | `1` | 每个用户允许的活跃任务；`0` 不限制。 |
| `JOB_TIMEOUT_SECONDS` | `1800` | 单个任务总超时，默认 30 分钟。 |
| `JOB_STALL_TIMEOUT_SECONDS` | `300` | 没有新文件写入时的卡住超时；`0` 关闭。 |
| `JOB_PROGRESS_CHECK_SECONDS` | `10` | 后端检查下载进度的间隔。 |
| `PREVIEW_TIMEOUT_SECONDS` | `30` | 获取 JM 封面、标题和页数的超时。 |
| `JOB_PROGRESS_NOTIFY_SECONDS` | `300` | 群内主动进度通知最小间隔；`0` 不主动通知。 |
| `JOB_CONFIRM_TIMEOUT_SECONDS` | `600` | JM 预览后等待“下载”或“取消”的时间。 |
| `USER_COMMAND_COOLDOWN_SECONDS` | `10` | 普通用户命令冷却时间。 |
| `LARGE_ALBUM_WARNING_PAGES` | `100` | 超过该页数需要二次确认；`0` 不警告。 |
| `MAX_ALBUM_PAGES` | `300` | 超过该页数自动拒绝；`0` 不限制。 |

不要轻易关闭 `JOB_STALL_TIMEOUT_SECONDS`。它用于终止已经卡住且不再写入文件的下载子进程，防止一个任务永久堵住队列。

提高 `MAX_CONCURRENT_JOBS` 前应同时评估内存、磁盘、带宽和数据源限流。2 核 2GB 服务器建议保持 1。

---

## 缓存、审计与数据目录

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CACHE_CLEANUP_INTERVAL_SECONDS` | `3600` | 后端缓存清理间隔；`0` 关闭定期清理。 |
| `JOB_CACHE_TTL_SECONDS` | `259200` | 后端任务目录保留时间，默认 3 天。 |
| `BOT_DOWNLOAD_CACHE_TTL_SECONDS` | `259200` | Bot 下载和上传临时目录保留时间，默认 3 天。 |
| `PREVIEW_CACHE_TTL_SECONDS` | `86400` | JM 预览文件保留时间，默认 1 天。 |
| `AUDIT_RETENTION_DAYS` | `30` | 审计日志保留天数；`0` 不按天数清理。 |
| `DATA_DIR` | `./data` | SQLite 数据库、任务、预览和媒体缓存目录；Docker 使用 `/app/data`。 |

宿主机上的 `/opt/sanbot/data` 会挂载到容器 `/app/data`。配置 Docker 环境时应填写容器路径，不要把 `DATA_DIR` 改成宿主机路径 `/opt/sanbot/data`。

定期检查：

```bash
df -h
du -sh /opt/sanbot/data /opt/sanbot/backups
```

---

## JMComic 下载线程与配置文件

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `JM_DOWNLOAD_IMAGE_THREADS` | `8` | 实际图片下载线程数。 |
| `JM_DOWNLOAD_PHOTO_THREADS` | `2` | 实际章节或分册下载线程数。 |
| `JM_DOWNLOAD_MAX_IMAGE_THREADS` | `8` | 图片线程硬上限。 |
| `JM_DOWNLOAD_MAX_PHOTO_THREADS` | `2` | 章节线程硬上限。 |
| `JMCOMIC_OPTION_PATH` | `./config/jmcomic-option.yml` | JMComic YAML 配置路径；Docker 使用 `/app/config/jmcomic-option.yml`。 |

实际线程数不会超过对应硬上限。只提高 `JM_DOWNLOAD_IMAGE_THREADS` 而不提高 `JM_DOWNLOAD_MAX_IMAGE_THREADS` 不会获得更高并发。

一键安装的 JMComic 配置文件位于：

```text
/opt/sanbot/config/jmcomic-option.yml
```

示例：

```yaml
client:
  impl: api
  retry_times: 5
  postman:
    meta_data:
      headers:
        User-Agent: "Mozilla/5.0"
      cookies:
        AVS: "你的 AVS 值"

download:
  image:
    decode: true
  threading:
    image: 8
    photo: 2
```

更新 AVS 后执行：

```bash
sanbot restart
```

---

## 推荐配置示例

### 仅开放 JM 与 JAV 基础查询

```dotenv
ALLOWED_GROUP_IDS=123456789

ENABLE_JM_DOWNLOAD=true
JM_DOWNLOAD_ALLOWED_GROUP_IDS=123456789
ENABLE_JM_SEARCH=true
JM_SEARCH_ALLOWED_GROUP_IDS=123456789
ENABLE_JM_RANKING=true
JM_RANKING_ALLOWED_GROUP_IDS=123456789

ENABLE_JAVLIBRARY=true
ENABLE_JAV_QUERY=true
JAV_QUERY_ALLOWED_GROUP_IDS=123456789
ENABLE_AV_SEARCH=true
AV_SEARCH_ALLOWED_GROUP_IDS=123456789
ENABLE_ACTOR_SEARCH=true
ACTOR_SEARCH_ALLOWED_GROUP_IDS=123456789
ENABLE_DB_RANKING=true
DB_RANKING_ALLOWED_GROUP_IDS=123456789

ENABLE_JAV_TRAILER=true
JAV_TRAILER_ALLOWED_GROUP_IDS=123456789
ENABLE_JAV_STILLS=false
ENABLE_MISSAV_LINK=false
ENABLE_TG_MIRROR=false
ENABLE_TG_AUTO_FETCH=false
```

### 2 核 2GB 服务器

```dotenv
MAX_CONCURRENT_JOBS=1
MAX_ACTIVE_JOBS_PER_GROUP=3
MAX_ACTIVE_JOBS_PER_USER=1
JM_DOWNLOAD_IMAGE_THREADS=8
JM_DOWNLOAD_PHOTO_THREADS=2
JM_DOWNLOAD_MAX_IMAGE_THREADS=8
JM_DOWNLOAD_MAX_PHOTO_THREADS=2
JOB_TIMEOUT_SECONDS=1800
JOB_STALL_TIMEOUT_SECONDS=300
MAX_ALBUM_PAGES=300
```

### 完全关闭 Telegram

```dotenv
ENABLE_TG_MIRROR=false
ENABLE_TG_AUTO_FETCH=false
TG_MIRROR_ALLOWED_GROUP_IDS=
TG_AUTO_FETCH_GROUP_IDS=
TG_BOT_TOKEN=
TG_API_ID=
TG_API_HASH=
TG_SESSION_STRING=
```

---

## 修改后检查清单

1. 确认变量名没有拼写错误。
2. 确认布尔值是 `true` 或 `false`。
3. 确认群号使用英文逗号。
4. 确认没有把宿主机路径写进 Docker 容器路径变量。
5. 确认 OneBot Token 与 NapCat 配置一致。
6. 确认 Cookie 和 Token 没有被提交到 Git。
7. 执行：

```bash
sanbot restart
sanbot doctor
```

8. 查看日志中是否出现配置解析错误：

```bash
sanbot logs bot
sanbot logs backend
```

返回 **[项目主页](../README.md)** · 查看 **[一键安装教程](./tutorial.md)** · 查看 **[常见问题](./qa.md)**
