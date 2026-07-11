# SanBot 一键安装教程

安装完成后，服务器会运行三个服务：

- **SanBot Bot**：接收 QQ 群消息、调用后端并发送结果。
- **SanBot Backend**：处理 JM 下载、JAV 查询和任务记录。
- **NapCatQQ**：登录机器人 QQ，并通过 OneBot 11 与 SanBot 通信。

> SanBot 只能运行在你拥有管理权限的服务器上。请勿使用来历不明的 Cookie、Token 或 QQ 登录信息。

---

## 1. 安装前准备

### 服务器

推荐配置：

- Ubuntu 22.04、Ubuntu 24.04 或较新的 Debian
- 2 核 CPU
- 2GB 以上内存
- 10GB 以上可用磁盘空间
- `x86_64` 或 `arm64` 架构
- 可以使用 `root` 或 `sudo`

JM 漫画会占用较多磁盘空间。SanBot 会定期清理缓存，但仍建议预留足够空间。

在选择服务器的时候，带宽比性能更加重要，它直接决定你的下载速度与上传速度；

而在性能中，优先选择高内存而非多核，内存数直接决定了本子最大下载页数。

与此同时推荐选择香港等非大陆地区服务器，但不建议选择日本服务器，JMComic屏蔽了日本地区IP。

个人使用的是雨云香港五区大带宽服务器。活动期间每月约 30 元，使用下方邀请链接可参与首月优惠，实际价格与折扣请以购买页面为准：

[查看雨云服务器活动](https://www.rainyun.com/sanshan_)

### 需要提前记下的信息

- 作为机器人的 QQ 号（你应该先注册一个）
- 机器人管理者的 QQ 号
- 允许使用机器人的 QQ 群号
- JMComic 的 `AVS` Cookie，可稍后补充（在下文中有获取指南）
- 可选的 JavDB/Javlibrary Cookie（在下文中有获取指南）
- 可选的 Telegram Bot Token 或 Telethon 会话

如果暂时不使用 JAV 或 Telegram，可以在向导中输入 `false`，以后再通过配置文件开启。

---

## 2. 登录服务器

Windows 用户可以打开 PowerShell 或 Windows Terminal，然后运行：

```powershell
ssh root@你的服务器IP
```

例如：

```powershell
ssh root@192.0.2.10
```

第一次连接时可能出现以下提示：

```text
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

确认 IP 无误后输入 `yes`，再输入服务器密码。输入密码时终端不会显示字符，这是正常现象。

成功登录后，命令行通常会变成类似：

```text
root@server:~#
```

---

## 3. 运行一键安装命令

在服务器的 SSH 终端中粘贴：

```bash
curl -fsSL https://raw.githubusercontent.com/sanshanhyo/SanBot/main/scripts/install.sh | sudo bash
```

如果你已经使用 `root` 登录，并且服务器没有安装 `sudo`，可以运行：

```bash
curl -fsSL https://raw.githubusercontent.com/sanshanhyo/SanBot/main/scripts/install.sh | bash
```

脚本会显示 SanBot 标题和安装说明，然后进入中文向导。

所有功能开关都只接受：

```text
true
false
```

直接按回车会使用方括号中显示的推荐值。例如：

```text
启用 JM 漫画下载（请输入 true 或 false）[true]:
```

直接按回车就等于选择 `true`。

---

## 4. 填写安装向导

### 第 1 步：机器人身份

- **机器人 QQ 号**：之后在 NapCat 中扫码登录的 QQ。
- **机器人显示名称**：群内介绍中显示的名称，默认 `SanBot`。
- **管理者显示名称**：部署者或维护者的名字。
- **管理者 QQ 号**：可以执行审计、取消任务和清理缓存等管理命令。

QQ 号必须填写纯数字。

### 第 2 步：群聊安全范围

建议填写允许使用机器人的群号：

```text
123456789,987654321
```

多个群号使用英文逗号分隔，不要加入空格。

留空表示机器人加入任何群后都能使用。向导会再次要求确认，公开部署时**不推荐留空**。

如果选择“给每个功能分别设置群白名单”，向导会在最后继续询问 JM、JAV、剧照、Telegram 等功能各自允许使用的群。

### 第 3 步：JM 功能

推荐第一次安装全部保持 `true`：

- JM 漫画下载
- JM 中文关键词搜索
- JM 日榜、周榜、月榜

下载前机器人会先发送封面、标题和页数，并等待用户确认。超过设定页数的漫画会要求二次确认，超过 300 页会**默认拒绝**（由于其较为常见的爆内存风险，这条可以到配置文件中修改）。

### 第 4 步：JAV 元数据功能

JAV 功能只查询元数据，不下载影片。可以分别开启：

- 番号详情查询
- AV 中文标题搜索
- 演员搜索
- JavDB 排行榜
- JavDB 资源页
- MP4 预告片
- 剧照预览和剧照 PDF
- MissAV 外部播放入口

剧照和 MissAV 默认关闭。MissAV 属于外部链接功能，即使开启也必须配置群白名单，并受到群人数限制。

### 第 5 步：Telegram 转发

不使用 Telegram 时输入 `false`。

开启后可以选择：

- **Bot Token 模式**：配置简单，适合较小文件。
- **Telethon 用户会话模式**：需要 Telegram API ID、API Hash 和 Session String。

不要把 Bot Token、API Hash 或 Session String 发给他人，也不要提交到 GitHub。

**警告：经过实际测试，Telegram功能会显著提高封号风险，请谨慎使用，或仅当群友能100%保证不举报的前提下使用**

### 第 6 步：辅助功能

推荐保持默认开启：

- 任务历史查询
- 管理员命令和审计日志
- 后端健康监控

### 第 7 步：Cookie 和网络

JM 下载需要 `AVS` Cookie。可以填写以下任意格式：

```text
你的AVS值
```

或：

```text
AVS=你的AVS值;
```

安装器会自动提取 `AVS` 的值。Cookie 输入时不会显示在终端上。

JAV Cookie 和代理都可以留空。代理格式示例：

```text
http://127.0.0.1:7890
```

请注意：如果 SanBot 使用 Docker，而代理只监听服务器宿主机的 `127.0.0.1`，容器通常无法直接访问该代理。没有明确需要时建议留空。

#### AVS 获取指南

> 该站点包含成人内容，仅限达到所在地法定年龄，并确认访问行为符合当地法律法规的用户操作。

1. 使用浏览器打开 [禁漫天堂](https://18comic.vip/)，完成站点的年龄确认；如有账号，也可以先登录。
2. 按 `F12` 打开开发者工具。
3. Chrome、Edge 用户进入 **Application（应用）** → **Storage（存储）** → **Cookies**；Firefox 用户进入 **存储** → **Cookie**。
4. 在当前 JM 域名的 Cookie 列表中找到名称为 `AVS` 的项目。
5. 双击并复制它的 **Value（值）**，不要复制表头、域名或过期时间。
6. 回到 SanBot 安装向导，粘贴该值并按回车。

如果浏览器跳转到了其他 JM 分流，请在最终打开的分流域名下查找 Cookie。没有看到 `AVS` 时，可以刷新页面、完成年龄确认或登录后再检查。

`AVS` 相当于登录凭据。不要截图公开、发送到 QQ 群或提交到 GitHub。Cookie 失效后，重新按照以上步骤获取并更新 `/opt/sanbot/config/jmcomic-option.yml`，然后运行：

```bash
sanbot restart
```

#### JavDB/Javlibrary Cookie 获取指南

JAV 元数据查询通常可以不填写 Cookie。只有在数据源要求登录、出现验证页面，或预告片仅登录可见时，才建议配置。

1. 在浏览器中打开并登录你准备使用的数据源，例如 [JavDB](https://javdb.com/) 或 [Javlibrary](https://www.javlibrary.com/)。
2. 按 `F12` 打开开发者工具，进入 **Application（应用）** → **Cookies**。
3. 选择当前数据源域名，将需要的 Cookie 整理成标准请求头格式：

```text
cookie_name=value; another_cookie=another_value
```

4. 把整行内容粘贴到安装向导的 `JavDB/Javlibrary Cookie` 输入项。

Cookie 中不要包含 JSON 外壳、`name`、`domain` 等字段，只保留 `名称=值`，多个项目使用英文分号分隔。JavDB 常见的登录会话 Cookie 名称可能随站点更新而变化，因此应以浏览器当前实际保存的内容为准。

同样不要公开这些 Cookie。遇到查询失败时，先确认 Cookie 是否过期，再查看 `sanbot logs backend`，不要反复提高重试次数。

### 第 8 步：性能

2 核 2GB 服务器建议使用默认值：

```text
同时下载任务数：1
JM 图片下载线程数：8
JM 分册下载线程数：2
```

线程并不是越高越好。过高可能导致内存不足、下载源限流或任务更容易失败。

### 第 9 步：NapCat WebUI

第一次安装需要通过 WebUI 扫码登录 QQ。

选择临时开放公网 WebUI 后，脚本会把 WebUI 绑定到 `0.0.0.0`，默认端口为 `6099`，并生成随机登录 Token。

如果服务器提供商有“安全组”或“防火墙”设置，需要临时放行 TCP `6099`。建议只允许你自己的 IP 访问，不要长期向所有人开放。

---

## 5. 等待安装完成

脚本接下来会自动：

1. 安装 Docker 和 Docker Compose。
2. 创建 `/opt/sanbot`。
3. 生成带中文注释的 `.env`。
4. 生成 JMComic、JAV 和演员别名配置。
5. 为后端、OneBot 和 WebUI 生成随机 Token。
6. 自动配置 NapCat HTTP 3000 和 WebSocket 3001。
7. 下载并启动 SanBot 与 NapCat 镜像。
8. 等待后端健康检查通过。

首次下载镜像可能需要几分钟。不要在拉取镜像时关闭 SSH 窗口。

安装成功后会显示类似：

```text
========== 安装完成 ==========
安装目录：/opt/sanbot
服务管理：sanbot status
自动检查：sanbot doctor
```

下方还会显示带随机 Token 的 NapCat WebUI 地址。

---

## 6. 登录 NapCatQQ

在本地浏览器打开安装器显示的地址，例如：

```text
http://你的服务器IP:6099/webui?token=随机Token
```

然后：

1. 进入 **QQ 登录** 页面。
2. 选择二维码登录。
3. 使用机器人 QQ 的手机端扫码。
4. 在手机 QQ 上确认登录。
5. 等待 WebUI 显示 QQ 在线。

SanBot 安装器已经创建 OneBot HTTP 和 WebSocket 服务，不需要再进入“网络配置”手动新建 3000 或 3001。

如果浏览器提示“连接被拒绝”或一直转圈，请检查：

- 安装时是否选择了公网开放 WebUI。
- 云服务器安全组是否放行 TCP 6099。
- 系统防火墙是否允许 6099。
- `sanbot status` 中 NapCat 是否正在运行。

---

## 7. 完成安全检查

QQ 登录成功后，在 SSH 终端运行：

```bash
sanbot doctor
```

它会检查：

- Docker Compose 配置
- 三个容器的运行状态
- SanBot 后端健康状态
- NapCat 和 QQ 登录状态
- 预告片转换所需的 `ffmpeg`

全部正常后会显示：

```text
全部检查通过。
```

随后关闭 WebUI 公网入口：

```bash
sanbot close-webui
```

关闭后 WebUI 只监听服务器本机，不会影响机器人运行。以后确实需要进入 WebUI 时，可以使用 SSH 隧道，或临时修改配置再重启 NapCat。

---

## 8. 在 QQ 群中测试

先把机器人 QQ 拉进已经配置白名单的群，然后发送：

```text
@机器人 帮助
```

机器人应发送命令帮助图片。

测试 JM 查询：

```text
@机器人 JM123456
```

测试 JAV 元数据：

```text
@机器人 JAV SSIS-123
```

测试排行榜：

```text
@机器人 JM日榜
@机器人 DB日榜
```

必须通过 QQ 的结构化 `@` 选择机器人，只输入机器人昵称不会触发命令。

---

## 9. 常用管理命令

查看服务状态：

```bash
sanbot status
```

查看 Bot 日志：

```bash
sanbot logs bot
```

查看后端日志：

```bash
sanbot logs backend
```

查看 NapCat 日志：

```bash
sanbot logs napcat
```

重启所有服务：

```bash
sanbot restart
```

编辑环境变量：

```bash
sanbot config
```

保存配置后运行：

```bash
sanbot restart
```

备份配置和 SQLite 数据库：

```bash
sanbot backup
```

更新 SanBot：

```bash
sanbot update
```

`sanbot update` 会先备份配置和数据库，再拉取最新镜像并重启服务。

---

## 10. 重新运行安装器

以后再次执行一键安装命令时，脚本会检测到 `/opt/sanbot` 中的现有安装。

看到以下问题时建议选择 `true`：

```text
保留现有功能开关、白名单、Cookie 和 Token，只升级程序 [true]
```

脚本会保留原有配置与数据库，并为旧 `.env` 创建带时间戳的备份。不要为了普通更新重新填写全部 Cookie；日常更新优先使用 `sanbot update`。



## 11. 卸载

运行：

```bash
sanbot uninstall
```

输入 `true` 后会停止并移除容器，但保留 `/opt/sanbot` 下的配置和数据。

确认不再需要任何配置、Cookie、数据库和缓存后，才考虑手动删除 `/opt/sanbot`。删除目录不可恢复，请先执行：

```bash
sanbot backup
```

---

## 使用提醒

请确保机器人及其数据源的使用符合所在地法律法规、QQ 和 Telegram 平台规则以及内容版权要求。自动化登录 QQ 可能触发平台风控，建议使用专门的机器人账号，并妥善保管服务器密码、Cookie、Token 和会话文件。

返回 **[项目主页](../README.md)** · 查看 **[常见问题](./qa.md)** · 查看 **[配置说明](./env.md)**
