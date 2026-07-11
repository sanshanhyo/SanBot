#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="SanBot"
INSTALL_DIR="${SANBOT_HOME:-/opt/sanbot}"
SANBOT_REF="${SANBOT_REF:-main}"
RAW_BASE="${SANBOT_RAW_BASE:-https://raw.githubusercontent.com/sanshanhyo/SanBot/${SANBOT_REF}}"
SANBOT_IMAGE_DEFAULT="${SANBOT_IMAGE:-ghcr.io/sanshanhyo/sanbot:latest}"
NAPCAT_IMAGE_DEFAULT="${NAPCAT_IMAGE:-mlikiowa/napcat-docker:latest}"
CURRENT_STEP="启动安装向导"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

on_error() {
  local line="${1:-unknown}"
  printf '\n[安装失败] 第 %s 行，当前步骤：%s\n' "$line" "$CURRENT_STEP" >&2
  printf '不用慌，已经生成的数据不会被自动删除。修复问题后可再次运行同一条安装命令。\n' >&2
}
trap 'on_error "$LINENO"' ERR

banner() {
  cat <<'EOF'
  ____              ____        _
 / ___|  __ _ _ __ | __ )  ___ | |_
 \___ \ / _` | '_ \|  _ \ / _ \| __|
  ___) | (_| | | | | |_) | (_) | |_
 |____/ \__,_|_| |_|____/ \___/ \__|

        中文一键安装向导
EOF
}

section() {
  printf '\n========== %s ==========' "$1"
  printf '\n'
}

progress() {
  printf '[%3s%%] %s\n' "$1" "$2"
}

die() {
  printf '[错误] %s\n' "$1" >&2
  exit 1
}

warn() {
  printf '[提醒] %s\n' "$1" >&2
}

need_tty() {
  [ -r /dev/tty ] || die "安装向导需要交互式终端。请直接在 SSH 窗口粘贴 README 中的一行安装命令。"
}

ask() {
  local prompt="$1"
  local default="${2:-}"
  local value
  if [ -n "$default" ]; then
    printf '%s [%s]: ' "$prompt" "$default" > /dev/tty
  else
    printf '%s: ' "$prompt" > /dev/tty
  fi
  IFS= read -r value < /dev/tty || true
  printf '%s' "${value:-$default}"
}

ask_secret() {
  local prompt="$1"
  local value
  printf '%s（输入内容不会显示，可直接回车跳过）: ' "$prompt" > /dev/tty
  IFS= read -r -s value < /dev/tty || true
  printf '\n' > /dev/tty
  printf '%s' "$value"
}

ask_bool() {
  local prompt="$1"
  local default="${2:-false}"
  local value normalized
  while true; do
    printf '%s（请输入 true 或 false）[%s]: ' "$prompt" "$default" > /dev/tty
    IFS= read -r value < /dev/tty || true
    normalized="$(printf '%s' "${value:-$default}" | tr '[:upper:]' '[:lower:]')"
    case "$normalized" in
      true|false)
        printf '%s' "$normalized"
        return
        ;;
      *) printf '这里只需要输入 true 或 false。\n' > /dev/tty ;;
    esac
  done
}

ask_numeric() {
  local prompt="$1"
  local default="${2:-}"
  local value
  while true; do
    value="$(ask "$prompt" "$default")"
    if [[ "$value" =~ ^[0-9]+$ ]]; then
      printf '%s' "$value"
      return
    fi
    printf '这里需要填写纯数字。\n' > /dev/tty
  done
}

ask_id_list() {
  local prompt="$1"
  local default="${2:-}"
  local required="${3:-false}"
  local value
  while true; do
    value="$(ask "$prompt" "$default")"
    value="${value// /}"
    if [ -z "$value" ] && [ "$required" = "false" ]; then
      printf '%s' "$value"
      return
    fi
    if [[ "$value" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
      printf '%s' "$value"
      return
    fi
    printf '请填写纯数字群号；多个群用英文逗号分隔，例如 123456,654321。\n' > /dev/tty
  done
}

strip_newlines() {
  local value="$1"
  value="${value//$'\r'/}"
  value="${value//$'\n'/}"
  printf '%s' "$value"
}

escape_yaml_double() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="$(strip_newlines "$value")"
  printf '%s' "$value"
}

normalize_avs_cookie() {
  local value
  value="$(strip_newlines "$1")"
  if [[ "$value" == *"AVS="* ]]; then
    value="${value#*AVS=}"
    value="${value%%;*}"
  fi
  printf '%s' "$value"
}

random_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
  fi
}

require_platform() {
  [ "$(uname -s)" = "Linux" ] || die "这个一键脚本只支持 Linux 服务器。Windows 请使用 README 的本地测试方式。"
  [ "$(id -u)" -eq 0 ] || die "请使用 sudo 运行，例如：curl -fsSL <安装脚本地址> | sudo bash"
  command -v curl >/dev/null 2>&1 || die "系统缺少 curl，请先安装 curl。"
  case "$(uname -m)" in
    x86_64|amd64|aarch64|arm64) ;;
    *) die "当前 CPU 架构 $(uname -m) 暂不支持。" ;;
  esac
}

preflight() {
  local memory_mb free_mb check_path
  memory_mb="$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null || printf '0')"
  check_path="$INSTALL_DIR"
  while [ ! -e "$check_path" ] && [ "$check_path" != "/" ]; do
    check_path="$(dirname "$check_path")"
  done
  free_mb="$(df -Pm "$check_path" 2>/dev/null | awk 'NR==2 {print $4}' || true)"
  if [ "$memory_mb" -gt 0 ] && [ "$memory_mb" -lt 1800 ]; then
    warn "服务器内存约 ${memory_mb}MB，建议至少 2GB，并保持最大下载任务数为 1。"
  fi
  if [ -n "$free_mb" ] && [ "$free_mb" -lt 4096 ]; then
    warn "安装目录所在磁盘可用空间不足 4GB，漫画 PDF 可能很快占满磁盘。"
  fi
}

show_agreement() {
  cat <<'EOF'
继续安装表示你理解：
1. 脚本会安装 Docker、下载 SanBot 和 NapCat 镜像，并创建 /opt/sanbot。
2. QQ 扫码必须由账号本人完成；Cookie、Token 只保存在服务器本地。
3. 3000/3001 端口不会暴露公网，WebUI 使用随机密码。
4. 请遵守当地法律、平台规则和内容版权要求。
EOF
}

install_docker() {
  CURRENT_STEP="检查 Docker"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    progress 25 "Docker 已安装，跳过。"
  else
    progress 18 "正在安装 Docker 和 Compose，这一步通常需要几分钟。"
    local installer
    installer="$(mktemp)"
    curl -fsSL --retry 3 https://get.docker.com -o "$installer"
    sh "$installer"
    rm -f "$installer"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker >/dev/null 2>&1 || warn "无法通过 systemctl 启动 Docker。"
  fi
  docker info >/dev/null 2>&1 || die "Docker 服务没有运行。"
  docker compose version >/dev/null 2>&1 || die "Docker Compose 插件不可用。"
}

backup_file() {
  local file="$1"
  [ ! -f "$file" ] || cp -a "$file" "${file}.bak.${TIMESTAMP}"
}

env_get() {
  local file="$1" key="$2" line
  [ -f "$file" ] || return 0
  line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

set_env_value() {
  local file="$1" key="$2" value="$3" tmp found
  tmp="$(mktemp "${file}.tmp.XXXXXX")"
  found=false
  value="$(strip_newlines "$value")"
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" == "${key}="* ]]; then
      printf '%s=%s\n' "$key" "$value" >> "$tmp"
      found=true
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$file"
  if [ "$found" = "false" ]; then
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  mv "$tmp" "$file"
}

download_env_template() {
  local target="$1"
  curl -fsSL --retry 3 "${RAW_BASE}/.env.example" -o "$target"
  grep -q '^BOT_QQ_ID=' "$target" || die "下载到的环境变量模板不完整。"
}

merge_env_template() {
  local old_file="$1" new_file="$2" line key value
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      set_env_value "$new_file" "$key" "$value"
    fi
  done < "$old_file"
}

feature_groups() {
  local label="$1" enabled="$2" sensitive="${3:-false}"
  if [ "$enabled" = "false" ]; then
    printf '%s' "$CFG_GLOBAL_GROUPS"
    return
  fi
  if [ "$sensitive" = "true" ] && [ -z "$CFG_GLOBAL_GROUPS" ]; then
    ask_id_list "${label}必须限制群，请填写允许的群号" "" true
    return
  fi
  if [ "$CFG_CUSTOM_WHITELISTS" = "true" ]; then
    ask_id_list "${label}允许在哪些群使用" "$CFG_GLOBAL_GROUPS" false
  else
    printf '%s' "$CFG_GLOBAL_GROUPS"
  fi
}

collect_config() {
  section "第 1 步：机器人身份"
  printf '机器人 QQ 是登录 NapCat 的 QQ；管理者 QQ 可以执行取消任务、审计等管理命令。\n'
  CFG_BOT_QQ="$(ask_numeric "机器人 QQ 号")"
  CFG_BOT_NAME="$(ask "机器人显示名称" "SanBot")"
  CFG_MANAGER_NAME="$(ask "管理者显示名称" "管理者")"
  CFG_MANAGER_QQ="$(ask_numeric "管理者 QQ 号")"

  section "第 2 步：群聊安全范围"
  printf '建议先填写允许使用机器人的群号。多个群号使用英文逗号分隔。\n'
  while true; do
    CFG_GLOBAL_GROUPS="$(ask_id_list "全局群白名单（留空表示所有群都能使用）" "" false)"
    if [ -n "$CFG_GLOBAL_GROUPS" ]; then
      break
    fi
    if [ "$(ask_bool "当前没有群限制，确认允许机器人在所有群使用吗" "false")" = "true" ]; then
      break
    fi
  done
  CFG_CUSTOM_WHITELISTS="$(ask_bool "是否给每个功能分别设置群白名单" "false")"

  section "第 3 步：JM 功能"
  CFG_ENABLE_JM_DOWNLOAD="$(ask_bool "启用 JM 漫画下载" "true")"
  CFG_ENABLE_JM_SEARCH="$(ask_bool "启用 JM 中文关键词搜索" "true")"
  CFG_ENABLE_JM_RANKING="$(ask_bool "启用 JM 日榜、周榜、月榜" "true")"

  section "第 4 步：JAV 元数据功能"
  printf '这些功能只查询公开元数据，不下载影片。JavDB 会作为默认数据源。\n'
  CFG_ENABLE_JAV="$(ask_bool "启用 JAV 元数据总功能" "true")"
  if [ "$CFG_ENABLE_JAV" = "true" ]; then
    CFG_ENABLE_JAV_QUERY="$(ask_bool "启用番号详情查询" "true")"
    CFG_ENABLE_AV_SEARCH="$(ask_bool "启用 AV 中文标题搜索" "true")"
    CFG_ENABLE_ACTOR_SEARCH="$(ask_bool "启用演员搜索" "true")"
    CFG_ENABLE_DB_RANKING="$(ask_bool "启用 JavDB 日榜、周榜、月榜" "true")"
    CFG_ENABLE_RESOURCE_PAGE="$(ask_bool "启用 JavDB 资源页链接" "true")"
    CFG_ENABLE_TRAILER="$(ask_bool "启用预告片 MP4 发送" "true")"
    CFG_ENABLE_STILLS="$(ask_bool "启用剧照预览" "false")"
    if [ "$CFG_ENABLE_STILLS" = "true" ]; then
      CFG_ENABLE_STILLS_PDF="$(ask_bool "把全部合格剧照打包为 PDF" "true")"
    else
      CFG_ENABLE_STILLS_PDF="false"
    fi
    printf '在线播放入口风险较高，默认关闭，并且必须设置群白名单。\n'
    CFG_ENABLE_MISSAV="$(ask_bool "启用 MissAV 外部在线播放链接" "false")"
  else
    CFG_ENABLE_JAV_QUERY=false
    CFG_ENABLE_AV_SEARCH=false
    CFG_ENABLE_ACTOR_SEARCH=false
    CFG_ENABLE_DB_RANKING=false
    CFG_ENABLE_RESOURCE_PAGE=false
    CFG_ENABLE_TRAILER=false
    CFG_ENABLE_STILLS=false
    CFG_ENABLE_STILLS_PDF=false
    CFG_ENABLE_MISSAV=false
  fi

  section "第 5 步：Telegram 转发"
  printf 'TG 转发需要额外 Token 或 API 会话，默认关闭；以后也可以编辑 .env 再开启。\n'
  CFG_ENABLE_TG="$(ask_bool "启用 Telegram 频道转发" "false")"
  CFG_TG_MODE=bot
  CFG_TG_BOT_TOKEN=""
  CFG_TG_API_ID=""
  CFG_TG_API_HASH=""
  CFG_TG_SESSION_STRING=""
  if [ "$CFG_ENABLE_TG" = "true" ]; then
    if [ "$(ask_bool "使用 Bot Token 模式（false 表示 Telethon 用户会话）" "true")" = "true" ]; then
      CFG_TG_MODE=bot
      CFG_TG_BOT_TOKEN="$(ask_secret "Telegram Bot Token")"
      [ -n "$CFG_TG_BOT_TOKEN" ] || warn "未填写 Bot Token，TG 功能启动后会提示配置错误。"
    else
      CFG_TG_MODE=telethon
      CFG_TG_API_ID="$(ask_numeric "Telegram API ID")"
      CFG_TG_API_HASH="$(ask_secret "Telegram API Hash")"
      CFG_TG_SESSION_STRING="$(ask_secret "Telegram Session String")"
    fi
    CFG_ENABLE_TG_AUTO="$(ask_bool "启用每小时静默自动拉取" "false")"
  else
    CFG_ENABLE_TG_AUTO=false
  fi

  section "第 6 步：辅助功能"
  CFG_ENABLE_HISTORY="$(ask_bool "启用任务历史查询" "true")"
  CFG_ENABLE_ADMIN="$(ask_bool "启用管理员命令和审计日志" "true")"
  CFG_ENABLE_HEALTH="$(ask_bool "启用后端健康监控" "true")"

  section "第 7 步：Cookie 和网络"
  printf 'JM 下载需要 AVS Cookie。现在不填也能安装，之后可编辑配置补充。\n'
  CFG_JM_COOKIE="$(normalize_avs_cookie "$(ask_secret "JMComic AVS Cookie")")"
  CFG_JAV_COOKIE=""
  CFG_JAV_PROXY=""
  if [ "$CFG_ENABLE_JAV" = "true" ]; then
    CFG_JAV_COOKIE="$(ask_secret "JavDB/Javlibrary Cookie")"
    CFG_JAV_PROXY="$(ask "JAV 请求代理（例如 http://127.0.0.1:7890，直连请留空）" "")"
  fi

  section "第 8 步：性能"
  printf '2 核 2GB 服务器建议保持默认值。线程过高可能触发限流或耗尽内存。\n'
  CFG_MAX_JOBS="$(ask_numeric "同时下载任务数" "1")"
  CFG_IMAGE_THREADS="$(ask_numeric "JM 图片下载线程数" "8")"
  CFG_PHOTO_THREADS="$(ask_numeric "JM 分册下载线程数" "2")"

  section "第 9 步：NapCat WebUI"
  printf '首次登录需要打开 WebUI 扫码。脚本会生成随机密码。\n'
  CFG_PUBLIC_WEBUI="$(ask_bool "临时允许通过服务器公网 IP 打开 WebUI" "true")"
  CFG_WEBUI_PORT="$(ask_numeric "WebUI 端口" "6099")"

  CFG_JM_DOWNLOAD_GROUPS="$(feature_groups "JM 下载" "$CFG_ENABLE_JM_DOWNLOAD")"
  CFG_JM_SEARCH_GROUPS="$(feature_groups "JM 搜索" "$CFG_ENABLE_JM_SEARCH")"
  CFG_JM_RANKING_GROUPS="$(feature_groups "JM 排行榜" "$CFG_ENABLE_JM_RANKING")"
  CFG_JAV_QUERY_GROUPS="$(feature_groups "番号查询" "$CFG_ENABLE_JAV_QUERY")"
  CFG_AV_SEARCH_GROUPS="$(feature_groups "AV 搜索" "$CFG_ENABLE_AV_SEARCH")"
  CFG_ACTOR_SEARCH_GROUPS="$(feature_groups "演员搜索" "$CFG_ENABLE_ACTOR_SEARCH")"
  CFG_DB_RANKING_GROUPS="$(feature_groups "DB 排行榜" "$CFG_ENABLE_DB_RANKING")"
  CFG_RESOURCE_GROUPS="$(feature_groups "JavDB 资源页" "$CFG_ENABLE_RESOURCE_PAGE")"
  CFG_TRAILER_GROUPS="$(feature_groups "预告片" "$CFG_ENABLE_TRAILER")"
  CFG_STILLS_GROUPS="$(feature_groups "剧照" "$CFG_ENABLE_STILLS" true)"
  CFG_STILLS_PDF_GROUPS="$(feature_groups "剧照 PDF" "$CFG_ENABLE_STILLS_PDF" true)"
  CFG_MISSAV_GROUPS="$(feature_groups "MissAV 在线播放" "$CFG_ENABLE_MISSAV" true)"
  CFG_TG_GROUPS="$(feature_groups "Telegram 转发" "$CFG_ENABLE_TG" true)"
  CFG_HISTORY_GROUPS="$(feature_groups "任务历史" "$CFG_ENABLE_HISTORY")"
  CFG_ADMIN_GROUPS="$(feature_groups "管理员命令" "$CFG_ENABLE_ADMIN")"

  CFG_BACKEND_TOKEN="$(random_token)"
  CFG_ONEBOT_TOKEN="$(random_token)"
  CFG_WEBUI_TOKEN="$(random_token)"
  if [ "$CFG_PUBLIC_WEBUI" = "true" ]; then
    CFG_WEBUI_BIND="0.0.0.0"
  else
    CFG_WEBUI_BIND="127.0.0.1"
  fi
}

write_fresh_env() {
  local env_file="$INSTALL_DIR/.env" tmp="$INSTALL_DIR/.env.new"
  download_env_template "$tmp"
  backup_file "$env_file"
  mv "$tmp" "$env_file"

  set_env_value "$env_file" SANBOT_IMAGE "$SANBOT_IMAGE_DEFAULT"
  set_env_value "$env_file" NAPCAT_IMAGE "$NAPCAT_IMAGE_DEFAULT"
  set_env_value "$env_file" NAPCAT_UID "$(id -u)"
  set_env_value "$env_file" NAPCAT_GID "$(id -g)"
  set_env_value "$env_file" NAPCAT_ACCOUNT "$CFG_BOT_QQ"
  set_env_value "$env_file" NAPCAT_WEBUI_BIND "$CFG_WEBUI_BIND"
  set_env_value "$env_file" NAPCAT_WEBUI_PORT "$CFG_WEBUI_PORT"
  set_env_value "$env_file" NAPCAT_WEBUI_TOKEN "$CFG_WEBUI_TOKEN"
  set_env_value "$env_file" BACKEND_HOST "0.0.0.0"
  set_env_value "$env_file" BACKEND_PORT "8000"
  set_env_value "$env_file" LOG_LEVEL "INFO"

  set_env_value "$env_file" BOT_QQ_ID "$CFG_BOT_QQ"
  set_env_value "$env_file" BOT_DISPLAY_NAME "$CFG_BOT_NAME"
  set_env_value "$env_file" BOT_MANAGER_NAME "$CFG_MANAGER_NAME"
  set_env_value "$env_file" BOT_MANAGER_QQ "$CFG_MANAGER_QQ"
  set_env_value "$env_file" BOT_MANAGER_QQ_IDS "$CFG_MANAGER_QQ"
  set_env_value "$env_file" BOT_I18N_DIR "/app/i18n"
  set_env_value "$env_file" ALLOWED_GROUP_IDS "$CFG_GLOBAL_GROUPS"
  set_env_value "$env_file" BOT_ALLOWED_GROUP_IDS "$CFG_GLOBAL_GROUPS"
  set_env_value "$env_file" HEALTH_CHECK_INTERVAL_SECONDS "$([ "$CFG_ENABLE_HEALTH" = true ] && printf '60' || printf '0')"
  set_env_value "$env_file" HEALTH_NOTIFY_GROUP_IDS "$CFG_GLOBAL_GROUPS"

  set_env_value "$env_file" NAPCAT_WS_URL "ws://napcat:3001"
  set_env_value "$env_file" NAPCAT_HTTP_URL "http://napcat:3000"
  set_env_value "$env_file" NAPCAT_ACCESS_TOKEN "$CFG_ONEBOT_TOKEN"
  set_env_value "$env_file" BACKEND_URL "http://backend:8000"
  set_env_value "$env_file" BACKEND_API_TOKEN "$CFG_BACKEND_TOKEN"

  set_env_value "$env_file" ENABLE_JM_DOWNLOAD "$CFG_ENABLE_JM_DOWNLOAD"
  set_env_value "$env_file" JM_DOWNLOAD_ALLOWED_GROUP_IDS "$CFG_JM_DOWNLOAD_GROUPS"
  set_env_value "$env_file" ENABLE_JM_SEARCH "$CFG_ENABLE_JM_SEARCH"
  set_env_value "$env_file" ENABLE_SEARCH "$CFG_ENABLE_JM_SEARCH"
  set_env_value "$env_file" JM_SEARCH_ALLOWED_GROUP_IDS "$CFG_JM_SEARCH_GROUPS"
  set_env_value "$env_file" ENABLE_JM_RANKING "$CFG_ENABLE_JM_RANKING"
  set_env_value "$env_file" JM_RANKING_ALLOWED_GROUP_IDS "$CFG_JM_RANKING_GROUPS"

  set_env_value "$env_file" ENABLE_JAVLIBRARY "$CFG_ENABLE_JAV"
  set_env_value "$env_file" ENABLE_JAV_QUERY "$CFG_ENABLE_JAV_QUERY"
  set_env_value "$env_file" JAV_QUERY_ALLOWED_GROUP_IDS "$CFG_JAV_QUERY_GROUPS"
  set_env_value "$env_file" ENABLE_AV_SEARCH "$CFG_ENABLE_AV_SEARCH"
  set_env_value "$env_file" AV_SEARCH_ALLOWED_GROUP_IDS "$CFG_AV_SEARCH_GROUPS"
  set_env_value "$env_file" ENABLE_ACTOR_SEARCH "$CFG_ENABLE_ACTOR_SEARCH"
  set_env_value "$env_file" ACTOR_SEARCH_ALLOWED_GROUP_IDS "$CFG_ACTOR_SEARCH_GROUPS"
  set_env_value "$env_file" ENABLE_DB_RANKING "$CFG_ENABLE_DB_RANKING"
  set_env_value "$env_file" DB_RANKING_ALLOWED_GROUP_IDS "$CFG_DB_RANKING_GROUPS"
  set_env_value "$env_file" JAVLIBRARY_PROVIDER_ORDER "javdb,javlibrary,jav321,javbus"
  set_env_value "$env_file" JAVLIBRARY_FETCHER "curl"
  set_env_value "$env_file" JAVLIBRARY_COOKIE "$CFG_JAV_COOKIE"
  set_env_value "$env_file" JAVLIBRARY_PROXY "$CFG_JAV_PROXY"
  set_env_value "$env_file" JAVLIBRARY_BROWSER_PROFILE_DIR "/app/data/javlibrary-browser"
  set_env_value "$env_file" JAV_ACTOR_ALIAS_PATH "/app/config/actor-aliases.yml"
  set_env_value "$env_file" ENABLE_JAV_RESOURCE_PAGE "$CFG_ENABLE_RESOURCE_PAGE"
  set_env_value "$env_file" JAV_RESOURCE_PAGE_ALLOWED_GROUP_IDS "$CFG_RESOURCE_GROUPS"
  set_env_value "$env_file" ENABLE_JAV_TRAILER "$CFG_ENABLE_TRAILER"
  set_env_value "$env_file" JAV_TRAILER_ALLOWED_GROUP_IDS "$CFG_TRAILER_GROUPS"
  set_env_value "$env_file" JAV_TRAILER_FFMPEG_PATH "ffmpeg"
  set_env_value "$env_file" JAV_TRAILER_COOKIE ""
  set_env_value "$env_file" ENABLE_JAV_STILLS "$CFG_ENABLE_STILLS"
  set_env_value "$env_file" JAV_STILLS_ALLOWED_GROUP_IDS "$CFG_STILLS_GROUPS"
  set_env_value "$env_file" ENABLE_JAV_STILLS_PDF "$CFG_ENABLE_STILLS_PDF"
  set_env_value "$env_file" JAV_STILLS_PDF_ALLOWED_GROUP_IDS "$CFG_STILLS_PDF_GROUPS"
  set_env_value "$env_file" JAV_STILLS_PDF_MAX_IMAGES "0"
  set_env_value "$env_file" ENABLE_MISSAV_LINK "$CFG_ENABLE_MISSAV"
  set_env_value "$env_file" MISSAV_ALLOWED_GROUP_IDS "$CFG_MISSAV_GROUPS"

  set_env_value "$env_file" ENABLE_TG_MIRROR "$CFG_ENABLE_TG"
  set_env_value "$env_file" TG_MIRROR_ALLOWED_GROUP_IDS "$CFG_TG_GROUPS"
  set_env_value "$env_file" ENABLE_TG_AUTO_FETCH "$CFG_ENABLE_TG_AUTO"
  set_env_value "$env_file" TG_AUTO_FETCH_GROUP_IDS "$CFG_TG_GROUPS"
  set_env_value "$env_file" TG_MIRROR_MODE "$CFG_TG_MODE"
  set_env_value "$env_file" TG_BOT_TOKEN "$CFG_TG_BOT_TOKEN"
  set_env_value "$env_file" TG_API_ID "$CFG_TG_API_ID"
  set_env_value "$env_file" TG_API_HASH "$CFG_TG_API_HASH"
  set_env_value "$env_file" TG_SESSION_STRING "$CFG_TG_SESSION_STRING"
  set_env_value "$env_file" TG_SESSION_PATH "/app/data/telegram.session"
  if [ "$CFG_TG_MODE" = "bot" ]; then
    set_env_value "$env_file" TG_MAX_FILE_BYTES "20971520"
  else
    set_env_value "$env_file" TG_MAX_FILE_BYTES "104857600"
  fi

  set_env_value "$env_file" ENABLE_HISTORY "$CFG_ENABLE_HISTORY"
  set_env_value "$env_file" HISTORY_ALLOWED_GROUP_IDS "$CFG_HISTORY_GROUPS"
  set_env_value "$env_file" ENABLE_ADMIN_COMMANDS "$CFG_ENABLE_ADMIN"
  set_env_value "$env_file" ADMIN_ALLOWED_GROUP_IDS "$CFG_ADMIN_GROUPS"
  set_env_value "$env_file" MAX_CONCURRENT_JOBS "$CFG_MAX_JOBS"
  set_env_value "$env_file" JM_DOWNLOAD_IMAGE_THREADS "$CFG_IMAGE_THREADS"
  set_env_value "$env_file" JM_DOWNLOAD_PHOTO_THREADS "$CFG_PHOTO_THREADS"
  set_env_value "$env_file" JM_DOWNLOAD_MAX_IMAGE_THREADS "$CFG_IMAGE_THREADS"
  set_env_value "$env_file" JM_DOWNLOAD_MAX_PHOTO_THREADS "$CFG_PHOTO_THREADS"
  set_env_value "$env_file" DATA_DIR "/app/data"
  set_env_value "$env_file" JMCOMIC_OPTION_PATH "/app/config/jmcomic-option.yml"
  chmod 600 "$env_file"
}

upgrade_env() {
  local env_file="$INSTALL_DIR/.env" tmp="$INSTALL_DIR/.env.new"
  download_env_template "$tmp"
  merge_env_template "$env_file" "$tmp"
  backup_file "$env_file"
  mv "$tmp" "$env_file"
  set_env_value "$env_file" SANBOT_IMAGE "$(env_get "$env_file" SANBOT_IMAGE)"
  set_env_value "$env_file" NAPCAT_IMAGE "$(env_get "$env_file" NAPCAT_IMAGE)"
  set_env_value "$env_file" NAPCAT_WS_URL "ws://napcat:3001"
  set_env_value "$env_file" NAPCAT_HTTP_URL "http://napcat:3000"
  set_env_value "$env_file" BACKEND_URL "http://backend:8000"
  set_env_value "$env_file" BACKEND_HOST "0.0.0.0"
  set_env_value "$env_file" DATA_DIR "/app/data"
  set_env_value "$env_file" JMCOMIC_OPTION_PATH "/app/config/jmcomic-option.yml"
  set_env_value "$env_file" JAVLIBRARY_BROWSER_PROFILE_DIR "/app/data/javlibrary-browser"
  set_env_value "$env_file" JAV_ACTOR_ALIAS_PATH "/app/config/actor-aliases.yml"
  set_env_value "$env_file" JAV_TRAILER_FFMPEG_PATH "ffmpeg"
  set_env_value "$env_file" TG_SESSION_PATH "/app/data/telegram.session"
  [ -n "$(env_get "$env_file" NAPCAT_ACCESS_TOKEN)" ] || set_env_value "$env_file" NAPCAT_ACCESS_TOKEN "$(random_token)"
  [ -n "$(env_get "$env_file" NAPCAT_WEBUI_TOKEN)" ] || set_env_value "$env_file" NAPCAT_WEBUI_TOKEN "$(random_token)"
  [ -n "$(env_get "$env_file" BACKEND_API_TOKEN)" ] || set_env_value "$env_file" BACKEND_API_TOKEN "$(random_token)"
  [ -n "$(env_get "$env_file" NAPCAT_ACCOUNT)" ] || set_env_value "$env_file" NAPCAT_ACCOUNT "$(env_get "$env_file" BOT_QQ_ID)"
  [ -n "$(env_get "$env_file" SANBOT_IMAGE)" ] || set_env_value "$env_file" SANBOT_IMAGE "$SANBOT_IMAGE_DEFAULT"
  [ -n "$(env_get "$env_file" NAPCAT_IMAGE)" ] || set_env_value "$env_file" NAPCAT_IMAGE "$NAPCAT_IMAGE_DEFAULT"
  [ -n "$(env_get "$env_file" NAPCAT_WEBUI_BIND)" ] || set_env_value "$env_file" NAPCAT_WEBUI_BIND "127.0.0.1"
  [ -n "$(env_get "$env_file" NAPCAT_WEBUI_PORT)" ] || set_env_value "$env_file" NAPCAT_WEBUI_PORT "6099"
  [ -n "$(env_get "$env_file" BACKEND_PORT)" ] || set_env_value "$env_file" BACKEND_PORT "8000"
  chmod 600 "$env_file"
}

write_jmcomic_option() {
  local file="$INSTALL_DIR/config/jmcomic-option.yml" cookie
  cookie="$(escape_yaml_double "$CFG_JM_COOKIE")"
  backup_file "$file"
  cat > "$file" <<EOF
client:
  impl: api
  retry_times: 5
  postman:
    meta_data:
      headers:
        User-Agent: "Mozilla/5.0"
      cookies:
        AVS: "${cookie}"
download:
  image:
    decode: true
  threading:
    image: ${CFG_IMAGE_THREADS}
    photo: ${CFG_PHOTO_THREADS}
dir_rule:
  base_dir: /app/data/jmcomic
  rule: Bd_Aid_Pindex
EOF
  chmod 600 "$file"
}

write_jav_option() {
  local file="$INSTALL_DIR/config/javlibrary-option.yml" cookie proxy
  cookie="$(escape_yaml_double "$CFG_JAV_COOKIE")"
  proxy="$(escape_yaml_double "$CFG_JAV_PROXY")"
  backup_file "$file"
  cat > "$file" <<EOF
base_url: https://www.javlibrary.com
language: cn
provider_order:
  - javdb
  - javlibrary
  - jav321
  - javbus
javdb_base_url: https://javdb.com
javbus_base_url: https://www.javbus.com
jav321_base_url: https://www.jav321.com
timeout_seconds: 8
total_timeout_seconds: 15
fetcher: curl
request:
  user_agent:
  cookie: "${cookie}"
  proxy: "${proxy}"
  impersonate: random
  retry_times: 1
browser:
  profile_dir: ../data/javlibrary-browser
  channel:
  headless: true
  wait_seconds: 120
EOF
  chmod 600 "$file"
}

ensure_runtime_configs() {
  local env_file="$INSTALL_DIR/.env"
  if [ ! -f "$INSTALL_DIR/config/jmcomic-option.yml" ]; then
    CFG_JM_COOKIE=""
    CFG_IMAGE_THREADS="$(env_get "$env_file" JM_DOWNLOAD_IMAGE_THREADS)"
    CFG_PHOTO_THREADS="$(env_get "$env_file" JM_DOWNLOAD_PHOTO_THREADS)"
    CFG_IMAGE_THREADS="${CFG_IMAGE_THREADS:-8}"
    CFG_PHOTO_THREADS="${CFG_PHOTO_THREADS:-2}"
    write_jmcomic_option
  fi
  if [ ! -f "$INSTALL_DIR/config/javlibrary-option.yml" ]; then
    CFG_JAV_COOKIE="$(env_get "$env_file" JAVLIBRARY_COOKIE)"
    CFG_JAV_PROXY="$(env_get "$env_file" JAVLIBRARY_PROXY)"
    write_jav_option
  fi
}

ensure_actor_aliases() {
  local file="$INSTALL_DIR/config/actor-aliases.yml"
  [ -f "$file" ] && return
  curl -fsSL --retry 3 "${RAW_BASE}/config/actor-aliases.yml.example" -o "$file" || printf 'aliases: {}\n' > "$file"
  chmod 600 "$file"
}

write_napcat_config() {
  local env_file="$INSTALL_DIR/.env" bot_qq onebot_token webui_token
  bot_qq="$(env_get "$env_file" BOT_QQ_ID)"
  onebot_token="$(env_get "$env_file" NAPCAT_ACCESS_TOKEN)"
  webui_token="$(env_get "$env_file" NAPCAT_WEBUI_TOKEN)"
  [ -n "$bot_qq" ] || die "BOT_QQ_ID 为空，无法生成 NapCat 配置。"
  backup_file "$INSTALL_DIR/napcat/config/webui.json"
  backup_file "$INSTALL_DIR/napcat/config/onebot11_${bot_qq}.json"
  cat > "$INSTALL_DIR/napcat/config/webui.json" <<EOF
{
  "host": "0.0.0.0",
  "port": 6099,
  "token": "${webui_token}",
  "loginRate": 3
}
EOF
  cat > "$INSTALL_DIR/napcat/config/onebot11_${bot_qq}.json" <<EOF
{
  "network": {
    "httpServers": [
      {
        "name": "SanBot HTTP",
        "enable": true,
        "port": 3000,
        "host": "0.0.0.0",
        "enableCors": false,
        "enableWebsocket": false,
        "messagePostFormat": "array",
        "token": "${onebot_token}",
        "debug": false
      }
    ],
    "httpClients": [],
    "websocketServers": [
      {
        "name": "SanBot WebSocket",
        "enable": true,
        "host": "0.0.0.0",
        "port": 3001,
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "token": "${onebot_token}",
        "enableForcePushEvent": true,
        "debug": false,
        "heartInterval": 30000
      }
    ],
    "websocketClients": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false
}
EOF
  chmod 600 "$INSTALL_DIR/napcat/config/webui.json" "$INSTALL_DIR/napcat/config/onebot11_${bot_qq}.json"
}

write_compose_file() {
  local file="$INSTALL_DIR/docker-compose.yml"
  backup_file "$file"
  cat > "$file" <<'EOF'
services:
  backend:
    image: ${SANBOT_IMAGE}
    container_name: sanbot-backend
    restart: unless-stopped
    init: true
    command: ["python", "-m", "backend.main"]
    env_file: [.env]
    environment:
      BACKEND_HOST: 0.0.0.0
      BACKEND_URL: http://backend:8000
      DATA_DIR: /app/data
      JMCOMIC_OPTION_PATH: /app/config/jmcomic-option.yml
    volumes:
      - ./data:/app/data
      - ./config/jmcomic-option.yml:/app/config/jmcomic-option.yml:ro
      - ./config/javlibrary-option.yml:/app/config/javlibrary-option.yml:ro
      - ./config/actor-aliases.yml:/app/config/actor-aliases.yml:ro
    ports:
      - "127.0.0.1:${BACKEND_PORT:-8000}:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]
      interval: 15s
      timeout: 5s
      retries: 20
      start_period: 20s

  bot:
    image: ${SANBOT_IMAGE}
    container_name: sanbot-bot
    restart: unless-stopped
    init: true
    command: ["python", "-m", "bot.main"]
    depends_on:
      backend:
        condition: service_healthy
      napcat:
        condition: service_started
    env_file: [.env]
    environment:
      BACKEND_URL: http://backend:8000
      NAPCAT_HTTP_URL: http://napcat:3000
      NAPCAT_WS_URL: ws://napcat:3001
      DATA_DIR: /app/data
    volumes:
      - ./data:/app/data
      - ./config/jmcomic-option.yml:/app/config/jmcomic-option.yml:ro
      - ./config/javlibrary-option.yml:/app/config/javlibrary-option.yml:ro
      - ./config/actor-aliases.yml:/app/config/actor-aliases.yml:ro

  napcat:
    image: ${NAPCAT_IMAGE}
    container_name: sanbot-napcat
    restart: unless-stopped
    init: true
    environment:
      ACCOUNT: ${NAPCAT_ACCOUNT}
      NAPCAT_UID: ${NAPCAT_UID:-0}
      NAPCAT_GID: ${NAPCAT_GID:-0}
      TZ: Asia/Shanghai
    ports:
      - "${NAPCAT_WEBUI_BIND:-127.0.0.1}:${NAPCAT_WEBUI_PORT:-6099}:6099"
    expose:
      - "3000"
      - "3001"
    volumes:
      - ./napcat/QQ:/app/.config/QQ
      - ./napcat/config:/app/napcat/config
      - ./napcat/plugins:/app/napcat/plugins
      - ./data:/app/data
EOF
}

write_manager_command() {
  cat > /usr/local/bin/sanbot <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${SANBOT_HOME:-/opt/sanbot}"
COMPOSE=(docker compose -f "$APP_DIR/docker-compose.yml" --env-file "$APP_DIR/.env")

usage() {
  cat <<'HELP'
SanBot 管理命令：
  sanbot status          查看服务状态
  sanbot logs [服务]     查看日志：bot / backend / napcat
  sanbot doctor          自动检查后端、NapCat、Bot 和 ffmpeg
  sanbot webui           显示 NapCat WebUI 地址和密码
  sanbot close-webui     登录完成后关闭 WebUI 公网入口
  sanbot config          编辑带中文注释的 .env
  sanbot restart         重启全部服务
  sanbot update          备份配置、拉取新镜像并重启
  sanbot backup          备份配置和 SQLite 数据库
  sanbot stop|start      停止或启动服务
  sanbot uninstall       移除容器，保留配置和数据
HELP
}

ensure_app() {
  [ -f "$APP_DIR/docker-compose.yml" ] || { echo "没有找到 $APP_DIR，请先安装 SanBot。" >&2; exit 1; }
}

env_value() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$APP_DIR/.env" | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

set_env_value() {
  local key="$1" value="$2" tmp line found=false
  tmp="$(mktemp)"
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" == "${key}="* ]]; then
      printf '%s=%s\n' "$key" "$value" >> "$tmp"
      found=true
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$APP_DIR/.env"
  [ "$found" = true ] || printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
}

backup() {
  local output="$APP_DIR/backups/sanbot-$(date +%Y%m%d-%H%M%S).tar.gz"
  local database relative
  local files=(.env docker-compose.yml config napcat/config)
  mkdir -p "$APP_DIR/backups"
  while IFS= read -r -d '' database; do
    relative="${database#${APP_DIR}/}"
    files+=("$relative")
  done < <(find "$APP_DIR/data" -maxdepth 1 -type f -name '*.sqlite3' -print0)
  tar -czf "$output" -C "$APP_DIR" "${files[@]}"
  echo "备份完成：$output"
}

case "${1:-help}" in
  status) ensure_app; "${COMPOSE[@]}" ps ;;
  logs) ensure_app; shift || true; "${COMPOSE[@]}" logs -f --tail=200 "$@" ;;
  restart) ensure_app; "${COMPOSE[@]}" restart ;;
  stop) ensure_app; "${COMPOSE[@]}" stop ;;
  start) ensure_app; "${COMPOSE[@]}" up -d ;;
  backup) ensure_app; backup ;;
  update)
    ensure_app
    backup
    "${COMPOSE[@]}" pull
    "${COMPOSE[@]}" up -d --remove-orphans
    echo "更新完成。运行 sanbot doctor 检查服务。"
    ;;
  config)
    ensure_app
    editor="${EDITOR:-}"
    if [ -z "$editor" ]; then
      command -v nano >/dev/null 2>&1 && editor=nano || editor=vi
    fi
    "$editor" "$APP_DIR/.env"
    echo "配置已保存。执行 sanbot restart 让配置生效。"
    ;;
  webui)
    ensure_app
    bind="$(env_value NAPCAT_WEBUI_BIND)"
    port="$(env_value NAPCAT_WEBUI_PORT)"
    token="$(env_value NAPCAT_WEBUI_TOKEN)"
    ip="$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true)"
    if [ "$bind" = "0.0.0.0" ]; then
      echo "WebUI：http://${ip:-服务器IP}:${port:-6099}/webui?token=${token}"
    else
      echo "WebUI 仅限服务器本机：http://127.0.0.1:${port:-6099}/webui?token=${token}"
      echo "远程访问请使用 SSH 隧道。"
    fi
    ;;
  close-webui)
    ensure_app
    set_env_value NAPCAT_WEBUI_BIND 127.0.0.1
    "${COMPOSE[@]}" up -d --force-recreate napcat
    echo "WebUI 已只绑定到 127.0.0.1，公网入口已关闭。"
    ;;
  doctor)
    ensure_app
    failed=0
    echo "[1/5] Docker Compose 配置"
    "${COMPOSE[@]}" config >/dev/null && echo "正常" || failed=1
    echo "[2/5] 容器状态"
    "${COMPOSE[@]}" ps
    echo "[3/5] 后端健康检查"
    port="$(env_value BACKEND_PORT)"; port="${port:-8000}"
    curl -fsS "http://127.0.0.1:${port}/health" && echo || failed=1
    echo "[4/5] NapCat 登录和 OneBot 状态"
    "${COMPOSE[@]}" exec -T bot python -c 'import json, os, urllib.request; r=urllib.request.Request("http://napcat:3000/get_status", headers={"Authorization":"Bearer "+os.getenv("NAPCAT_ACCESS_TOKEN", "")}); d=json.load(urllib.request.urlopen(r, timeout=8)); print(d); raise SystemExit(0 if d.get("status")=="ok" and d.get("data",{}).get("online") else 1)' || failed=1
    echo "[5/5] 预告片转换组件"
    if "${COMPOSE[@]}" exec -T bot ffmpeg -version >/dev/null 2>&1; then
      echo "正常"
    else
      failed=1
    fi
    [ "$failed" -eq 0 ] || { echo "检查发现异常，请执行 sanbot logs 查看日志。" >&2; exit 1; }
    echo "全部检查通过。"
    ;;
  uninstall)
    ensure_app
    read -r -p "输入 true 确认移除容器（配置和数据不会删除）: " answer
    if [ "$answer" = true ]; then
      "${COMPOSE[@]}" down
      echo "容器已移除，$APP_DIR 中的数据仍然保留。"
    else
      echo "已取消。"
    fi
    ;;
  help|-h|--help) usage ;;
  *) usage; exit 1 ;;
esac
EOF
  chmod 755 /usr/local/bin/sanbot
}

wait_for_backend() {
  local port attempts
  port="$(env_get "$INSTALL_DIR/.env" BACKEND_PORT)"
  port="${port:-8000}"
  for attempts in $(seq 1 30); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

public_ip() {
  curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true
}

finish_message() {
  local env_file="$INSTALL_DIR/.env" bind port token ip
  bind="$(env_get "$env_file" NAPCAT_WEBUI_BIND)"
  port="$(env_get "$env_file" NAPCAT_WEBUI_PORT)"
  token="$(env_get "$env_file" NAPCAT_WEBUI_TOKEN)"
  ip="$(public_ip)"
  section "安装完成"
  printf '安装目录：%s\n' "$INSTALL_DIR"
  printf '服务管理：sanbot status\n'
  printf '自动检查：sanbot doctor\n'
  printf '\n接下来只需要登录 QQ：\n'
  if [ "$bind" = "0.0.0.0" ]; then
    printf '1. 浏览器打开：http://%s:%s/webui?token=%s\n' "${ip:-服务器IP}" "${port:-6099}" "$token"
  else
    printf '1. WebUI 当前只允许本机访问。执行 sanbot webui 查看 SSH 隧道提示。\n'
  fi
  printf '2. 在 WebUI 的“QQ 登录”页面扫码，等待机器人 QQ 显示在线。\n'
  printf '3. 不需要手动创建 HTTP 或 WebSocket，脚本已经配置完成。\n'
  printf '4. 执行 sanbot doctor；全部通过后即可在白名单群测试 @机器人 帮助。\n'
  if [ "$bind" = "0.0.0.0" ]; then
    printf '5. 扫码成功后执行 sanbot close-webui，关闭 WebUI 公网入口。\n'
  fi
  printf '\n常用命令：sanbot logs bot、sanbot logs napcat、sanbot restart、sanbot update\n'
}

main() {
  need_tty
  require_platform
  banner
  show_agreement
  [ "$(ask_bool "同意以上内容并继续安装" "true")" = "true" ] || die "安装已取消。"

  local preserve=false existing=false
  if [ -f "$INSTALL_DIR/.env" ] && [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
    existing=true
    section "检测到已有安装"
    printf '已有配置和数据库不会被删除。\n'
    preserve="$(ask_bool "保留现有功能开关、白名单、Cookie 和 Token，只升级程序" "true")"
  fi

  preflight
  if [ "$preserve" = "false" ]; then
    collect_config
  fi
  install_docker

  CURRENT_STEP="创建安装目录"
  progress 35 "正在准备 ${INSTALL_DIR}。"
  install -d -m 755 "$INSTALL_DIR" "$INSTALL_DIR/config" "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/backups"
  install -d -m 755 "$INSTALL_DIR/napcat/QQ" "$INSTALL_DIR/napcat/config" "$INSTALL_DIR/napcat/plugins"

  CURRENT_STEP="生成配置"
  progress 48 "正在生成完整中文配置和安全 Token。"
  if [ "$preserve" = "true" ]; then
    upgrade_env
  else
    write_fresh_env
    write_jmcomic_option
    write_jav_option
  fi
  ensure_runtime_configs
  ensure_actor_aliases
  write_napcat_config
  write_compose_file
  write_manager_command

  CURRENT_STEP="检查 Docker Compose"
  progress 62 "正在检查配置格式。"
  (cd "$INSTALL_DIR" && docker compose --env-file .env config >/dev/null)

  CURRENT_STEP="下载镜像"
  progress 72 "正在下载 SanBot 和 NapCat，首次安装可能需要几分钟。"
  (cd "$INSTALL_DIR" && docker compose --env-file .env pull)

  CURRENT_STEP="启动服务"
  progress 90 "正在启动服务并设置自动重启。"
  (cd "$INSTALL_DIR" && docker compose --env-file .env up -d --remove-orphans)

  CURRENT_STEP="等待后端"
  if wait_for_backend; then
    progress 100 "SanBot 后端已经就绪。"
  else
    warn "后端暂未通过健康检查，请执行 sanbot logs backend 查看原因。"
  fi
  finish_message

  if [ "$existing" = "true" ] && [ "$preserve" = "true" ]; then
    printf '\n本次为保留配置升级，旧 .env 已自动备份。\n'
  fi
}

main "$@"
