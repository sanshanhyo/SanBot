# SanBot

基于 NapCatQQ + OneBot 11 的 QQ 群机器人。群成员发送 `@机器人 JM123456` 后，机器人会把 JM 编号提交给后端，后端调用 `jmcomic` 下载并导出 PDF，最后由机器人把 PDF 上传回原群。

这个项目只封装调用 [`JMComic-Crawler-Python`](https://github.com/hect0x7/JMComic-Crawler-Python) 发布的 `jmcomic` 包，不修改第三方项目源码。

## 功能

- 只处理 QQ 群消息。
- 使用 OneBot 11 结构化消息段判断是否真的 `@` 了机器人。
- 支持 `JM123456`、`jm123456` 两种输入；纯数字不会触发下载。
- 默认开启 JM 关键词搜索：`@机器人 JM搜索 关键词`，用户回复序号后进入同一套预览确认流程。
- 支持 JavDB 标题搜索：`@机器人 AV搜索 中文标题`，以及演员搜索：`@机器人 演员搜索 演员名`。
- 支持 `JM日榜` / `JM周榜` / `JM月榜` 和 `DB日榜` / `DB周榜` / `DB月榜`。
- 番号查询后可按配置查看 JavDB 资源页、预告片、少量剧照和白名单外部播放入口。
- 单独 `@机器人` 会显示机器人介绍；`@机器人 帮助` 和 `@机器人 功能` 会显示使用说明和功能列表。
- 支持任务历史查询：用户可查自己的最近任务，群管理员可查本群最近任务。
- 一条消息只允许一个编号。
- 先发送封面和标题预览，用户回复确认后才加入下载队列。
- 如果预览检测到页数超过阈值，用户需要二次确认后才会开始下载。
- 同一群内同一用户同时只能有一个排队中、下载中或转换中的任务。
- 每个用户默认最多 1 个活跃任务，每个群默认最多 3 个活跃任务。
- 群主、群管理员和机器人管理者可以查询状态、队列、审计日志和取消任务；清理缓存只允许机器人管理者执行。
- 可配置群白名单；默认不配置时，机器人仍然可在加入的群内使用。
- Bot 会定期检查后端健康状态，服务异常和恢复时可通知指定群。
- 下载任务写入 SQLite，服务重启后不会只依赖内存状态。
- 后端控制台会显示下载进度条；如果预览拿到了页数，会显示百分比和 `已下载/总页数`。
- 群内只发送关键状态，不会按“已保存 N 张图片”频繁刷屏。
- 任务失败会保存并返回稳定报错码，Bot 群消息也会显示报错码。
- JMComic 下载和 PDF 导出在独立子进程执行，总超时或长时间无文件写入都会终止子进程，避免单个卡死任务堵住队列。
- PDF 文件会命名为 `[JM编号]漫画标题.pdf`，并自动清理 Windows 不允许的字符。
- 下载完成后调用 NapCatQQ `upload_group_file` 上传 PDF。
- PDF 过大时会自动拆分为多个分卷 PDF 上传，分卷文件名使用 `JM123456_part01-of03.pdf`，方便在 QQ 群文件列表里识别。
- 上传失败会按配置重试，默认最多重试 5 次。
- 后端会定期清理过期缓存；Bot 上传成功后也会清理本次上传缓存，避免 `data/` 目录无限增长。
- Bot 群内文案集中放在 `i18n/zh_CN.json`，后续维护提示语不用翻代码。
- Token、Cookie 和登录信息都通过本地配置提供，不写死在代码里。

## 环境要求

- Python 3.12+
- NapCatQQ
- OneBot 11 HTTP 和 WebSocket
- 可用的 JMComic 配置文件

## 快速开始

克隆项目后，先创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

如果要使用 Javlibrary 的 `browser` 抓取模式，请安装浏览器可选依赖并下载 Chromium：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[browser,test]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

后续从 GitHub 拉取更新后，也建议重新执行一次安装命令，确保新增依赖例如 `img2pdf` 已安装。

如果 PyPI 访问较慢，可以使用镜像：

```powershell
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[test]"
```

复制环境变量文件：

```powershell
Copy-Item .env.example .env
```

复制 JMComic 配置文件：

```powershell
Copy-Item config\jmcomic-option.yml.example config\jmcomic-option.yml
```

然后编辑 `.env` 和 `config/jmcomic-option.yml`。

## 配置

`.env` 示例：

```env
BOT_QQ_ID=
NAPCAT_WS_URL=ws://127.0.0.1:3001
NAPCAT_HTTP_URL=http://127.0.0.1:3000
NAPCAT_ACCESS_TOKEN=
NAPCAT_HTTP_TIMEOUT_SECONDS=60
NAPCAT_UPLOAD_TIMEOUT_SECONDS=900
NAPCAT_MAX_UPLOAD_BYTES=104857600
NAPCAT_MAX_UPLOAD_FILENAME_BYTES=96
NAPCAT_UPLOAD_RETRIES=5
BOT_LANG=zh_CN
BOT_I18N_DIR=
BOT_DISPLAY_NAME=SanBot
BOT_MANAGER_NAME=
BOT_MANAGER_QQ=
BOT_MANAGER_QQ_IDS=
ALLOWED_GROUP_IDS=
HEALTH_CHECK_INTERVAL_SECONDS=60
HEALTH_NOTIFY_GROUP_IDS=
BACKEND_URL=http://127.0.0.1:8000
BACKEND_API_TOKEN=
ENABLE_SEARCH=true
SEARCH_TIMEOUT_SECONDS=20
SEARCH_RESULT_LIMIT=5
SEARCH_CONFIRM_TIMEOUT_SECONDS=600
RANKING_TIMEOUT_SECONDS=20
RANKING_RESULT_LIMIT=10
ENABLE_JAVLIBRARY=true
JAVLIBRARY_TIMEOUT_SECONDS=8
JAVLIBRARY_TOTAL_TIMEOUT_SECONDS=15
JAVLIBRARY_CACHE_TTL_SECONDS=604800
JAVLIBRARY_FAILURE_CACHE_TTL_SECONDS=60
JAVLIBRARY_NOT_FOUND_CACHE_TTL_SECONDS=86400
JAVLIBRARY_BLOCKED_CACHE_TTL_SECONDS=120
JAVLIBRARY_TIMEOUT_CACHE_TTL_SECONDS=60
JAVLIBRARY_BASE_URL=https://www.javlibrary.com
JAVLIBRARY_LANGUAGE=cn
JAVLIBRARY_PROVIDER_ORDER=javdb,javlibrary,jav321,javbus
JAVDB_BASE_URL=https://javdb.com
JAVBUS_BASE_URL=https://www.javbus.com
JAV321_BASE_URL=https://www.jav321.com
JAVLIBRARY_FETCHER=curl
JAVLIBRARY_USER_AGENT=
JAVLIBRARY_COOKIE=
JAVLIBRARY_PROXY=
JAVLIBRARY_IMPERSONATE=random
JAVLIBRARY_RETRY_TIMES=1
JAVLIBRARY_BROWSER_PROFILE_DIR=./data/javlibrary-browser
JAVLIBRARY_BROWSER_CHANNEL=
JAVLIBRARY_BROWSER_HEADLESS=false
JAVLIBRARY_BROWSER_WAIT_SECONDS=120
JAV_ACTOR_ALIAS_PATH=./config/actor-aliases.yml
JAV_ACTOR_ALIAS_ONLINE=true
JAV_ACTOR_ALIAS_TIMEOUT_SECONDS=4
JAV_ACTOR_ALIAS_CANDIDATE_LIMIT=6
JAV_ACTION_TIMEOUT_SECONDS=300
ENABLE_JAV_RESOURCE_PAGE=true
ENABLE_JAV_TRAILER=true
ENABLE_JAV_STILLS=false
JAV_STILLS_MAX_COUNT=3
JAV_STILLS_MAX_GROUP_MEMBERS=150
ENABLE_JAV_STILLS_PDF=true
JAV_STILLS_PDF_MAX_IMAGES=0
JAV_STILLS_PDF_DOWNLOAD_CONCURRENCY=4
JAV_STILLS_PDF_DOWNLOAD_TIMEOUT_SECONDS=60
JAV_STILLS_MAX_IMAGE_BYTES=8388608
JAV_STILLS_MIN_IMAGE_WIDTH=300
JAV_STILLS_MIN_IMAGE_HEIGHT=200
ENABLE_MISSAV_LINK=false
MISSAV_BASE_URL=https://missav.live
MISSAV_ALLOWED_GROUP_IDS=
MISSAV_MAX_GROUP_MEMBERS=150
MAX_CONCURRENT_JOBS=1
MAX_ACTIVE_JOBS_PER_GROUP=3
MAX_ACTIVE_JOBS_PER_USER=1
JOB_TIMEOUT_SECONDS=1800
JOB_STALL_TIMEOUT_SECONDS=300
JOB_PROGRESS_CHECK_SECONDS=10
PREVIEW_TIMEOUT_SECONDS=30
JOB_PROGRESS_NOTIFY_SECONDS=300
JOB_CONFIRM_TIMEOUT_SECONDS=600
USER_COMMAND_COOLDOWN_SECONDS=10
LARGE_ALBUM_WARNING_PAGES=100
MAX_ALBUM_PAGES=300
CACHE_CLEANUP_INTERVAL_SECONDS=3600
JOB_CACHE_TTL_SECONDS=259200
BOT_DOWNLOAD_CACHE_TTL_SECONDS=259200
PREVIEW_CACHE_TTL_SECONDS=86400
AUDIT_RETENTION_DAYS=30
JM_DOWNLOAD_IMAGE_THREADS=8
JM_DOWNLOAD_PHOTO_THREADS=2
JM_DOWNLOAD_MAX_IMAGE_THREADS=8
JM_DOWNLOAD_MAX_PHOTO_THREADS=2
JMCOMIC_OPTION_PATH=./config/jmcomic-option.yml
DATA_DIR=./data
```

字段说明：

| 变量 | 说明 |
| --- | --- |
| `BOT_QQ_ID` | 机器人 QQ 号，必须填写 |
| `NAPCAT_WS_URL` | NapCatQQ OneBot 11 WebSocket 地址 |
| `NAPCAT_HTTP_URL` | NapCatQQ OneBot 11 HTTP 地址 |
| `NAPCAT_ACCESS_TOKEN` | NapCatQQ access token，没有则留空 |
| `NAPCAT_HTTP_TIMEOUT_SECONDS` | NapCatQQ 普通 HTTP API 超时，默认 `60` 秒 |
| `NAPCAT_UPLOAD_TIMEOUT_SECONDS` | NapCatQQ 上传群文件超时，默认 `900` 秒；大 PDF 建议保持较大 |
| `NAPCAT_MAX_UPLOAD_BYTES` | 单个上传文件大小上限，超过会自动拆分 PDF；默认 `104857600`，即 100MB |
| `NAPCAT_MAX_UPLOAD_FILENAME_BYTES` | 上传到 QQ 群文件时使用的展示文件名字节上限，默认 `96` |
| `NAPCAT_UPLOAD_RETRIES` | 单个文件上传失败后的重试次数，默认 `5` |
| `BOT_LANG` | Bot 群内提示语言文件，默认 `zh_CN`，对应 `i18n/zh_CN.json` |
| `BOT_I18N_DIR` | 自定义语言文件目录，留空使用项目内 `i18n/` |
| `BOT_DISPLAY_NAME` | 群内介绍页显示的机器人名称 |
| `BOT_MANAGER_NAME` | 群内介绍页显示的机器人管理者名称 |
| `BOT_MANAGER_QQ` | 群内介绍页显示的管理者 QQ；留空时会使用 `BOT_MANAGER_QQ_IDS` 的第一个 QQ |
| `BOT_MANAGER_QQ_IDS` | 机器人管理者 QQ 号，多个用英文逗号分隔；管理者可执行清理缓存等维护命令 |
| `ALLOWED_GROUP_IDS` | 群白名单，多个群号用英文逗号分隔；留空表示不限制群 |
| `HEALTH_CHECK_INTERVAL_SECONDS` | Bot 检查后端 `/health` 的间隔，默认 `60` 秒；设为 `0` 可关闭 |
| `HEALTH_NOTIFY_GROUP_IDS` | 后端异常或恢复时通知的群号，多个用英文逗号分隔；留空时使用白名单群 |
| `BACKEND_URL` | 后端 FastAPI 地址 |
| `BACKEND_API_TOKEN` | 后端 API token，没有则留空 |
| `ENABLE_SEARCH` | 是否启用关键词搜索，默认 `true`；如需关闭可设为 `false` |
| `SEARCH_TIMEOUT_SECONDS` | 后端搜索子进程超时时间，默认 `20` 秒 |
| `SEARCH_RESULT_LIMIT` | 每次搜索返回结果数，默认 `5`，最大 `10` |
| `SEARCH_CONFIRM_TIMEOUT_SECONDS` | 搜索结果出来后等待用户回复序号的时间，默认 `600` 秒 |
| `RANKING_TIMEOUT_SECONDS` | 后端排行榜子进程超时时间，默认 `20` 秒 |
| `RANKING_RESULT_LIMIT` | 每次排行榜返回结果数，默认 `10`，最大 `20` |
| `ENABLE_JAVLIBRARY` | 是否启用番号信息查询，默认 `true` |
| `JAVLIBRARY_TIMEOUT_SECONDS` | 番号数据源单次请求超时时间，默认 `8` 秒 |
| `JAVLIBRARY_TOTAL_TIMEOUT_SECONDS` | 单次番号查询总超时时间，默认 `15` 秒；超过后会停止继续尝试后续数据源 |
| `JAVLIBRARY_CACHE_TTL_SECONDS` | 番号信息成功查询缓存时间，默认 `604800` 秒，即 7 天 |
| `JAVLIBRARY_FAILURE_CACHE_TTL_SECONDS` | 普通抓取失败缓存时间，默认 `60` 秒；临时失败不会长时间卡住重试 |
| `JAVLIBRARY_NOT_FOUND_CACHE_TTL_SECONDS` | 未找到番号时的失败缓存时间，默认 `86400` 秒 |
| `JAVLIBRARY_BLOCKED_CACHE_TTL_SECONDS` | 数据源阻断请求时的失败缓存时间，默认 `120` 秒 |
| `JAVLIBRARY_TIMEOUT_CACHE_TTL_SECONDS` | 数据源超时时的失败缓存时间，默认 `60` 秒 |
| `JAVLIBRARY_BASE_URL` | Javlibrary 源站地址，默认 `https://www.javlibrary.com` |
| `JAVLIBRARY_LANGUAGE` | Javlibrary 语言路径，默认 `cn` |
| `JAVLIBRARY_PROVIDER_ORDER` | 番号数据源尝试顺序，默认 `javdb,javlibrary,jav321,javbus` |
| `JAVDB_BASE_URL` | JavDB 源站地址，默认 `https://javdb.com` |
| `JAVBUS_BASE_URL` | JavBus 源站地址，默认 `https://www.javbus.com` |
| `JAV321_BASE_URL` | Jav321 源站地址，默认 `https://www.jav321.com` |
| `JAVLIBRARY_FETCHER` | 番号数据源抓取模式，`curl`、`http` 或 `browser`，默认 `curl` |
| `JAVLIBRARY_USER_AGENT` | 番号数据源请求使用的 User-Agent，留空使用内置浏览器 UA |
| `JAVLIBRARY_COOKIE` | 请求 Cookie，留空则不带；不要提交到 Git |
| `JAVLIBRARY_PROXY` | 请求代理地址，留空则直连 |
| `JAVLIBRARY_IMPERSONATE` | `curl` 模式使用的 curl-cffi 浏览器指纹目标，默认 `random`；可填单个值或逗号分隔列表 |
| `JAVLIBRARY_RETRY_TIMES` | 临时网络错误重试次数，默认 `1`；多数据源会继续尝试下一个源 |
| `JAVLIBRARY_BROWSER_PROFILE_DIR` | `browser` 模式使用的持久化浏览器资料目录 |
| `JAVLIBRARY_BROWSER_CHANNEL` | `browser` 模式使用的本机浏览器通道，例如 `chrome` 或 `msedge`；留空使用 Playwright 自带 Chromium |
| `JAVLIBRARY_BROWSER_HEADLESS` | `browser` 模式是否无头运行；首次验证建议 `false` |
| `JAVLIBRARY_BROWSER_WAIT_SECONDS` | `browser` 模式等待手动验证的时间，默认 `120` 秒 |
| `JAV_ACTOR_ALIAS_PATH` | 演员中文译名别名配置文件，默认 `./config/actor-aliases.yml` |
| `JAV_ACTOR_ALIAS_ONLINE` | 演员搜索是否启用在线别名解析，默认 `true` |
| `JAV_ACTOR_ALIAS_TIMEOUT_SECONDS` | 在线别名解析超时时间，默认 `4` 秒 |
| `JAV_ACTOR_ALIAS_CANDIDATE_LIMIT` | 单次演员搜索最多尝试的候选名数量，默认 `6` |
| `JAV_ACTION_TIMEOUT_SECONDS` | 番号查询后等待用户回复操作的时间，默认 `300` 秒 |
| `ENABLE_JAV_RESOURCE_PAGE` | 是否允许回复“资源页”查看 JavDB 外部页面，默认 `true` |
| `ENABLE_JAV_TRAILER` | 是否允许回复“预告片”发送预告片，默认 `true` |
| `ENABLE_JAV_STILLS` | 是否允许回复“剧照”发送剧照预览，默认 `false` |
| `JAV_STILLS_MAX_COUNT` | 每次最多发送剧照数量，默认 `3`，最大 `6` |
| `JAV_STILLS_MAX_GROUP_MEMBERS` | 剧照入口允许的最大群人数，默认 `150`；超过会隐藏 |
| `ENABLE_JAV_STILLS_PDF` | 回复“剧照”后是否把剧照打包为 PDF 上传，默认 `true` |
| `JAV_STILLS_PDF_MAX_IMAGES` | 剧照 PDF 最多打包图片数，默认 `0` 表示不限量 |
| `JAV_STILLS_PDF_DOWNLOAD_CONCURRENCY` | 剧照 PDF 下载并发，默认 `4`，最大 `8` |
| `JAV_STILLS_PDF_DOWNLOAD_TIMEOUT_SECONDS` | 单次剧照图片请求超时，默认 `60` 秒 |
| `JAV_STILLS_MAX_IMAGE_BYTES` | 单张剧照最大下载体积，默认 `8388608` 字节 |
| `JAV_STILLS_MIN_IMAGE_WIDTH` | 剧照 PDF 最小图片宽度，默认 `300`；低于会跳过，设为 `0` 可关闭 |
| `JAV_STILLS_MIN_IMAGE_HEIGHT` | 剧照 PDF 最小图片高度，默认 `200`；低于会跳过，设为 `0` 可关闭 |
| `ENABLE_MISSAV_LINK` | 是否显示外部播放入口，默认 `false` |
| `MISSAV_BASE_URL` | 外部播放入口基础地址，默认 `https://missav.live` |
| `MISSAV_ALLOWED_GROUP_IDS` | 外部播放入口白名单群号，逗号分隔；未配置时不显示 |
| `MISSAV_MAX_GROUP_MEMBERS` | 外部播放入口允许的最大群人数，默认 `150`；超过会强制隐藏 |
| `MAX_CONCURRENT_JOBS` | 同时下载任务数，默认 `1` |
| `MAX_ACTIVE_JOBS_PER_GROUP` | 每个群允许同时存在的活跃任务数，默认 `3` |
| `MAX_ACTIVE_JOBS_PER_USER` | 每个用户允许同时存在的活跃任务数，默认 `1` |
| `JOB_TIMEOUT_SECONDS` | 单个任务总超时时间，默认 `1800` 秒 |
| `JOB_STALL_TIMEOUT_SECONDS` | 下载子进程无文件变化的卡住超时，默认 `300` 秒；设为 `0` 可关闭 |
| `JOB_PROGRESS_CHECK_SECONDS` | 后端检查下载进度和卡住状态的间隔，默认 `10` 秒 |
| `PREVIEW_TIMEOUT_SECONDS` | 获取漫画封面和标题的超时时间，默认 `30` 秒 |
| `JOB_PROGRESS_NOTIFY_SECONDS` | 群内非下载阶段进度通知间隔，默认 `300` 秒；后端控制台进度条不受影响 |
| `JOB_CONFIRM_TIMEOUT_SECONDS` | 预览后等待用户确认的时间，默认 `600` 秒 |
| `USER_COMMAND_COOLDOWN_SECONDS` | 同一群同一用户发送新任务或搜索命令的冷却时间，默认 `10` 秒 |
| `LARGE_ALBUM_WARNING_PAGES` | 超过多少页触发二次确认，默认 `100`；设为 `0` 可关闭 |
| `MAX_ALBUM_PAGES` | 超过多少页自动拒绝加入下载队列，默认 `300`；设为 `0` 可关闭 |
| `CACHE_CLEANUP_INTERVAL_SECONDS` | 后端缓存清理间隔，默认 `3600` 秒；设为 `0` 可关闭 |
| `JOB_CACHE_TTL_SECONDS` | 已完成/已失败任务目录保留时间，默认 `259200` 秒，即 3 天 |
| `BOT_DOWNLOAD_CACHE_TTL_SECONDS` | Bot 下载到本地准备上传的 PDF 缓存保留时间，默认 3 天 |
| `PREVIEW_CACHE_TTL_SECONDS` | 漫画预览临时文件保留时间，默认 `86400` 秒，即 1 天 |
| `AUDIT_RETENTION_DAYS` | 命令审计日志保留天数，默认 `30` 天；设为 `0` 不自动清理 |
| `JM_DOWNLOAD_IMAGE_THREADS` | JMComic 图片下载线程数，默认建议 `8` |
| `JM_DOWNLOAD_PHOTO_THREADS` | JMComic 章节下载线程数，默认建议 `2` |
| `JM_DOWNLOAD_MAX_IMAGE_THREADS` | 图片下载线程硬上限，默认 `8`，防止小服务器被过高并发拖死 |
| `JM_DOWNLOAD_MAX_PHOTO_THREADS` | 章节下载线程硬上限，默认 `2` |
| `JMCOMIC_OPTION_PATH` | JMComic 配置文件路径 |
| `DATA_DIR` | 数据目录 |

不要提交 `.env`、JMComic Cookie、Javlibrary Cookie、NapCat token 或任何登录信息。

## NapCatQQ 配置

在 NapCatQQ 中开启 OneBot 11：

- HTTP 服务地址对应 `NAPCAT_HTTP_URL`，例如 `http://127.0.0.1:3000`。
- WebSocket 服务地址对应 `NAPCAT_WS_URL`，例如 `ws://127.0.0.1:3001`。
- 如果 NapCatQQ 配置了 access token，把同一个值写到 `NAPCAT_ACCESS_TOKEN`。

本项目默认 Bot、后端、NapCatQQ 同机部署。上传文件时会调用：

```json
{
  "group_id": "123456789",
  "file": "PDF绝对路径",
  "name": "[JM123456]title.pdf"
}
```

Bot 会检查 NapCatQQ 响应中的 `status` 和 `retcode`，不会只看 HTTP 状态码。大 PDF 如果触发 `rich media transfer failed`，通常是 NapCat/QQ 上传阶段拒绝了大文件；本项目会按 `NAPCAT_MAX_UPLOAD_BYTES` 自动拆成多个 PDF 分卷再上传。

## JMComic 配置

编辑：

```text
config/jmcomic-option.yml
```

填入你自己的 JMComic 客户端、Cookie 或下载配置。服务器部署建议优先使用 `impl: api`，通常比网页端更不容易遇到 IP 地区限制。

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
        AVS: "你的AVS Cookie"

download:
  image:
    decode: true
  threading:
    image: 8
    photo: 2
```

`download.threading.image` 和 `download.threading.photo` 可以影响下载并发。也可以在 `.env` 里用 `JM_DOWNLOAD_IMAGE_THREADS` 和 `JM_DOWNLOAD_PHOTO_THREADS` 覆盖它们，2GB 左右的小服务器建议先从 `8` 和 `2` 开始试。数值越大不一定越快，过高可能触发限流、CPU/IO 飙升、OOM，甚至让 SSH 都响应变慢。后端会再用 `JM_DOWNLOAD_MAX_IMAGE_THREADS` 和 `JM_DOWNLOAD_MAX_PHOTO_THREADS` 做硬上限，避免误填过高并发把小服务器拖死。后端只在 `backend/downloader.py` 中调用 `jmcomic`。

每个任务会使用独立目录：

```text
data/jobs/{job_id}/
```

PDF 生成后会校验：

- PDF 文件存在
- 文件大小大于 0
- 最终只能有一个 PDF
- 文件名包含 JM 编号和漫画标题
- 文件名会清理 Windows 非法字符

## 启动

先启动后端：

```powershell
.\.venv\Scripts\python.exe -m backend.main
```

再启动 Bot：

```powershell
.\.venv\Scripts\python.exe -m bot.main
```

## JAV Metadata Crawler 独立使用

SanBot 内置了一份 JAV Metadata Crawler，因此不需要额外安装也能使用群内番号查询。这个 crawler 也拆成了独立仓库：[sanshanhyo/jav-meta-crawler](https://github.com/sanshanhyo/jav-meta-crawler)。

`javlibrary_crawler` 可以不启动 QQ 机器人单独使用。它只查询公开番号元数据，不下载视频。安装项目后会注册 `jav-meta`、`javlibrary` 和 `javv` 三个命令：

```bash
pip install .
jav-meta SSIS-123
javlibrary SSIS-123
javlibrary SSIS-123 --json
javv FC2-PPV-1234567 -o ./config/javlibrary-option.yml
javlibrary SSIS-123 -o ./config/javlibrary-option.yml --fetcher curl
javlibrary SSIS-123 --providers javdb,javlibrary,jav321,javbus --timeout 8 --total-timeout 15
python -m javlibrary_crawler SSIS-123
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--json` | 输出 JSON，方便脚本读取 |
| `-o, --option` | 指定配置文件，支持 YAML、JSON、TOML |
| `--providers` | 指定数据源顺序，例如 `javdb,javlibrary,jav321,javbus` |
| `--timeout` | 单个请求超时秒数 |
| `--total-timeout` | 整次查询总超时秒数 |
| `--proxy` | 指定 HTTP 代理，例如 `http://127.0.0.1:7890` |
| `--fetcher` | 抓取模式，支持 `curl`、`http`、`browser` |

也可以作为 Python 库使用：

```python
from javlibrary_crawler import create_option_by_file, lookup

option = create_option_by_file("./config/javlibrary-option.yml")
video = lookup("SSIS-123", option)
print(video.title)
```

配置文件示例见 `config/javlibrary-option.yml.example`：

```yaml
base_url: https://www.javlibrary.com
language: cn
provider_order:
  - javlibrary
  - jav321
  - javdb
  - javbus
javdb_base_url: https://javdb.com
javbus_base_url: https://www.javbus.com
jav321_base_url: https://www.jav321.com
timeout_seconds: 8
total_timeout_seconds: 15
fetcher: curl

request:
  user_agent:
  cookie:
  proxy:
  impersonate: random
  retry_times: 1

browser:
  # 相对路径会按这个配置文件所在目录解析，建议放到 git 忽略的 data 目录。
  profile_dir: ../data/javlibrary-browser
  # 可选：使用本机真实浏览器通道，例如 chrome 或 msedge。
  channel:
  headless: false
  wait_seconds: 120
```

命令行参数会覆盖环境变量和配置文件。遇到源站阻断时会返回 `JAV_SOURCE_BLOCKED`，查询超时返回 `JAV_FETCH_TIMEOUT`，未找到返回 `JAV_NOT_FOUND`。
默认的 `curl` 模式参考 `jmcomic` 和 MDCX 的网络层思路，使用 `curl-cffi` 发起请求并支持浏览器指纹候选、Cookie、代理、保守重试和失败缓存。查询时会按 `provider_order` 依次尝试 JavDB、Javlibrary、Jav321、JavBus，先返回第一个成功的数据源。`browser` 模式只作为备用调试手段；它首次验证更适合在 Windows 桌面或有图形界面的主机上完成，纯 Docker/SSH 服务器通常没有可见浏览器窗口。

群内使用示例：

```text
@机器人
@机器人 帮助
@机器人 功能
@机器人 JM123456
@机器人 JM日榜
@机器人 JM周榜
@机器人 JM月榜
@机器人 JAV SSIS-123
@机器人 AV搜索 中文标题
@机器人 演员搜索 三上悠亚
@机器人 DB日榜
@机器人 我的任务
```

单独 `@机器人` 会显示机器人介绍、项目地址和基础入口。`@机器人 帮助` 会显示指令说明，`@机器人 功能` 会显示当前支持的功能模块。

默认已开启 JM 关键词搜索，也可以搜索关键词：

```text
@机器人 JM搜索 戦乙女
```

机器人会返回最多 `SEARCH_RESULT_LIMIT` 条结果。用户回复序号后，机器人会继续发送封面、标题、页数和预计时间，并询问是否下载；不会直接加入下载队列。

排行榜查询不会加入下载队列，只返回文字列表：

```text
@机器人 JM日榜
@机器人 JM周榜
@机器人 JM月榜
```

番号信息查询只返回公开元数据，不下载视频：

```text
@机器人 JAV SSIS-123
@机器人 番号 FC2-PPV-1234567
```

结果会包含标题、发行日期、时长、制作商、演员、类别、评分、链接和封面。默认优先使用 JavDB，Javlibrary、Jav321、JavBus 会作为 fallback。查询结果会缓存到 SQLite，源站阻断、超时或未找到时会返回错误码。

如果源站提供对应数据，番号查询后还可以直接回复：

```text
预告片
资源页
剧照
在线播放
```

`预告片` 默认开启，会尝试发送 JavDB 预告片；`资源页` 默认开启，只发送 JavDB 外部页面链接，不展开磁力；`剧照` 默认关闭，开启后会先发送最多 `JAV_STILLS_MAX_COUNT` 张预览图，再按 `ENABLE_JAV_STILLS_PDF` 配置把剧照打包成 PDF 上传，群人数超过 `JAV_STILLS_MAX_GROUP_MEMBERS` 时会隐藏；`在线播放` 默认关闭，必须配置 `ENABLE_MISSAV_LINK=true` 且群号在 `MISSAV_ALLOWED_GROUP_IDS` 内，群人数超过 `MISSAV_MAX_GROUP_MEMBERS` 时会强制隐藏入口。外部页面与播放入口均会附带合规和版权提示。

JavDB 搜索和排行榜只返回公开元数据列表，不下载视频：

```text
@机器人 AV搜索 中文标题
@机器人 演员搜索 演员名
@机器人 DB日榜
@机器人 DB周榜
@机器人 DB月榜
```

`AV搜索` 会优先按标题排序；演员请使用独立的 `演员搜索`，例如 `@机器人 演员搜索 三上悠亚`。演员搜索会尝试中文译名、常见艺名、缓存别名和可选在线解析，搜索结果里的番号可以继续用 `@机器人 JAV SSIS-123` 查看详情。

如果某个演员中文译名搜不到，可以复制 `config/actor-aliases.yml.example` 为 `config/actor-aliases.yml`，手动补一条别名：

```yaml
aliases:
  桥本有菜:
    - 橋本ありな
    - Hashimoto Arina
```

后端也会把成功搜索到的演员名写入 SQLite 缓存，下次同名搜索会优先复用。

输入了无法识别的内容时，机器人会回复：

```text
未知命令！输入‘帮助’获取命令列表
```

机器人会先发送封面、标题、页数和预计时间，并询问是否下载。用户回复：

```text
下载
```

如果页数超过 `LARGE_ALBUM_WARNING_PAGES`，机器人会先发送警告，用户需要再次回复“下载”才会加入队列。
如果页数超过 `MAX_ALBUM_PAGES`，机器人会自动拒绝该任务，避免超大本子拖垮下载、转换和上传流程。

确认后，机器人会加入下载队列并回复：

```text
已接收 JM123456，任务编号：xxxx
```

如果正在下载的任务卡住或不想继续，同一个用户可以在群里回复：

```text
取消下载
```

取消会按“群号 + 用户 QQ”在后端查询当前任务，所以 Bot 重启后仍然可以取消排队中或下载中的任务。

管理员命令需要 `@机器人`：

```text
@机器人 状态
@机器人 队列
@机器人 审计
@机器人 最近任务
@机器人 取消 JM123456
@机器人 取消 任务编号前几位
@机器人 清理缓存
```

`状态`、`队列`、`审计`、`最近任务`、`取消` 允许 QQ 群主、QQ群管理员、机器人管理者执行。`审计` 默认查询当前群最近 10 条命令记录。`清理缓存` 只允许机器人管理者执行。机器人管理者由部署者在 `.env` 的 `BOT_MANAGER_QQ_IDS` 中配置，不等同于 QQ 群管理员。

任务完成后，机器人会上传 PDF 并发送完成消息。

## 如何测试

### 1. 跑单元测试

单元测试不需要真实 NapCatQQ，也不会真实下载 JMComic 内容：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖内容包括：

- `JM123456` 解析
- 没有 `@` 机器人时忽略
- 单独 `@机器人` 显示介绍页
- 帮助、功能和任务历史命令
- 无法识别内容时提示用法
- 正常创建任务
- 下载失败
- PDF 未生成
- 上传成功
- 上传失败重试
- 搜索命令解析
- 搜索结果选择后进入预览确认
- 任务历史查询按群和用户隔离
- 管理员命令权限
- 每群/每用户活跃任务限制
- 上传阶段管理员取消

### 2. 测后端是否能启动

启动后端：

```powershell
.\.venv\Scripts\python.exe -m backend.main
```

另开一个终端检查健康接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

正常会返回：

```json
{
  "status": "ok"
}
```

### 3. 测后端创建任务接口

确认 `config/jmcomic-option.yml` 已配置好后，可以手动创建一个任务：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/jobs `
  -ContentType "application/json" `
  -Body '{"album_id":"123456","group_id":"123456789","user_id":"987654321"}'
```

返回示例：

```json
{
  "job_id": "uuid",
  "status": "queued"
}
```

查询任务：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/jobs/{job_id}
```

任务完成后下载 PDF：

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8000/api/jobs/{job_id}/file `
  -OutFile .\test.pdf
```

### 4. 测 NapCatQQ 联通

确认 NapCatQQ 已登录并启用 OneBot 11 HTTP 后，可以直接调用发群消息接口：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:3000/send_group_msg `
  -ContentType "application/json" `
  -Body '{"group_id":"你的群号","message":"NapCatQQ 测试消息"}'
```

如果你配置了 `NAPCAT_ACCESS_TOKEN`，需要在请求里带上：

```powershell
-Headers @{ Authorization = "Bearer 你的token" }
```

### 5. 完整联调

1. 启动 NapCatQQ，并确认 HTTP / WebSocket 已开启。
2. 启动后端：`.\.venv\Scripts\python.exe -m backend.main`
3. 启动 Bot：`.\.venv\Scripts\python.exe -m bot.main`
4. 在 QQ 群发送：`@机器人 JM123456`
5. 检查机器人是否发送封面、标题和页数预览。
6. 回复：`下载`
7. 检查机器人是否回复任务编号。
8. 等待下载和转换完成。
9. 检查群文件里是否出现 PDF。

如果失败，先看两个终端里的日志。群内只会发送简短错误和报错码，完整异常会留在服务日志中。

常见报错码示例：

| 报错码 | 含义 |
| --- | --- |
| `JM_DOWNLOAD_FAILED` | JM 下载失败，通常是网络、Cookie、限流或请求失败 |
| `JM_NOT_FOUND` | JM 内容不存在或不可访问 |
| `PDF_GENERATION_FAILED` | 图片转 PDF 失败 |
| `JOB_TIMEOUT` | 任务超过 `JOB_TIMEOUT_SECONDS` 总超时 |
| `JOB_STALLED` | 超过 `JOB_STALL_TIMEOUT_SECONDS` 没有新文件写入，任务被自动终止 |
| `USER_CANCELLED` | 用户主动取消任务 |
| `NAPCAT_UPLOAD_FAILED` | PDF 已生成，但 NapCat 上传群文件失败 |

`PDF_GENERATION_FAILED` 优先检查后端虚拟环境是否安装了 `img2pdf`，并查看对应任务目录下的 `worker-output.log` 和 `worker-error.log`。

## 后端接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/albums/{album_id}/preview` | 获取漫画封面、标题、页数和预计时间 |
| `POST` | `/api/search` | 关键词搜索漫画；如需关闭可设置 `ENABLE_SEARCH=false` |
| `GET` | `/api/rankings/{period}` | 查询 JM 排行榜，`period` 支持 `day`、`week`、`month` |
| `GET` | `/api/jav/videos/{code}` | 查询番号元数据；如需关闭可设置 `ENABLE_JAVLIBRARY=false` |
| `POST` | `/api/jav/search` | 通过 JavDB 搜索番号标题 |
| `POST` | `/api/jav/actors/search` | 通过 JavDB 搜索演员作品 |
| `GET` | `/api/javdb/rankings/{period}` | 查询 JavDB 排行榜，`period` 支持 `day`、`week`、`month` |
| `POST` | `/api/jobs` | 创建下载任务 |
| `GET` | `/api/jobs/active?group_id=...&user_id=...` | 查询某个群用户当前活跃任务 |
| `POST` | `/api/jobs/active/cancel?group_id=...&user_id=...` | 取消某个群用户当前活跃任务 |
| `GET` | `/api/jobs/history?group_id=...&user_id=...` | 查询某个群用户的最近任务 |
| `GET` | `/api/jobs/{job_id}` | 查询任务状态，包含 `downloaded_files`、`total_files`、`error_code` |
| `POST` | `/api/jobs/{job_id}/cancel` | 按任务编号取消排队中或下载中的任务 |
| `GET` | `/api/jobs/{job_id}/file` | 下载 PDF |
| `POST` | `/api/audit/events` | 写入一条命令审计记录 |
| `GET` | `/api/admin/status` | 查询服务器状态、缓存大小和任务统计 |
| `GET` | `/api/admin/queue` | 查询当前队列和最近错误任务 |
| `GET` | `/api/admin/history?group_id=...` | 查询某个群的最近任务 |
| `GET` | `/api/admin/audit?group_id=...` | 查询某个群的最近命令审计 |
| `POST` | `/api/admin/jobs/{target}/cancel` | 管理员按 JM 编号或任务编号取消任务 |
| `POST` | `/api/admin/cache/cleanup` | 手动清理缓存；有活跃后端任务时会拒绝 |

任务状态：

```text
queued
downloading
converting
completed
failed
```

## 项目结构

```text
project/
├─ bot/
│  ├─ main.py
│  ├─ napcat_client.py
│  ├─ message_parser.py
│  ├─ backend_client.py
│  └─ lang.py
├─ backend/
│  ├─ main.py
│  ├─ search_worker.py
│  ├─ ranking_worker.py
│  ├─ javlibrary_service.py
│  ├─ downloader.py
│  ├─ task_manager.py
│  └─ models.py
├─ javlibrary_crawler/
│  ├─ client.py
│  ├─ cli.py
│  ├─ fetcher.py
│  ├─ parser.py
│  ├─ normalizer.py
│  ├─ option.py
│  ├─ models.py
│  └─ errors.py
├─ config/
│  ├─ jmcomic-option.yml.example
│  └─ javlibrary-option.yml.example
├─ i18n/
│  └─ zh_CN.json
├─ data/
├─ tests/
├─ .env.example
├─ pyproject.toml
└─ README.md
```

## 安全说明

- 不要把 Token、Cookie、账号密码提交到仓库。
- 只允许处理数字形式 JM 编号。
- 番号信息功能只查询公开元数据，不下载视频。
- 不允许用户控制文件路径。
- 不使用 `shell=True`。
- 下载、转换、上传失败时，群内只返回简短错误，详细异常写入日志。
