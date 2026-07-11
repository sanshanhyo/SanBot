from __future__ import annotations

import re

JM_DOWNLOAD_RE = re.compile(r"(?i)^\s*JM\s*\d{1,12}\s*$")
JM_SEARCH_RE = re.compile(r"(?i)^\s*JM\s*(?:搜索|搜|查找)\b.*$", re.S)
JAV_QUERY_RE = re.compile(
    r"(?i)^\s*(?:JAV|番号|AV)\s+[A-Z]{2,12}[-_\s]?\d{2,8}[A-Z]?\s*$"
    r"|^\s*(?:JAV|番号|AV)\s+FC2(?:[-_\s]?PPV)?[-_\s]?\d{3,10}\s*$"
)
AV_SEARCH_RE = re.compile(r"(?i)^\s*(?:AV|DB)\s*(?:搜索|搜|查找)\b.*$", re.S)
ACTOR_SEARCH_RE = re.compile(
    r"(?i)^\s*(?:演员|女优|女優|AV演员|AV女优|AV女優|DB演员|DB女优|DB女優)"
    r"\s*(?:搜索|搜|查找)?.*$",
    re.S,
)
TG_COMMAND_RE = re.compile(r"(?i)^\s*TG\s*(?:绑定|bind|列表|频道|订阅|最新|拉取|同步)\b.*$", re.S)
ADMIN_CANCEL_RE = re.compile(r"(?i)^\s*(?:取消|cancel)\s+.+$")

EXACT_SANBOT_COMMANDS = {
    "帮助",
    "help",
    "使用说明",
    "说明",
    "功能",
    "功能列表",
    "模块",
    "modules",
    "features",
    "历史",
    "我的任务",
    "任务历史",
    "我的历史",
    "history",
    "最近任务",
    "群任务",
    "群历史",
    "最近历史",
    "group history",
    "状态",
    "status",
    "队列",
    "queue",
    "审计",
    "审计日志",
    "操作日志",
    "audit",
    "清理缓存",
    "清除缓存",
    "cleanup",
    "JM日榜",
    "JM周榜",
    "JM月榜",
    "DB日榜",
    "DB周榜",
    "DB月榜",
}

PENDING_SANBOT_RESPONSES = {
    "下载",
    "确认",
    "同意",
    "是",
    "要",
    "yes",
    "y",
    "ok",
    "取消",
    "取消下载",
    "取消任务",
    "停止下载",
    "停止任务",
    "不要",
    "否",
    "不下",
    "no",
    "n",
    "在线播放",
    "播放",
    "链接",
    "资源页",
    "查看链接",
    "预告片",
    "预告",
    "剧照",
    "图片",
    "剧照pdf",
    "pdf",
}

EXACT_SANBOT_COMMANDS_CASEFOLD = {item.casefold() for item in EXACT_SANBOT_COMMANDS}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_sanbot_command(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    folded = normalized.casefold()
    if folded in EXACT_SANBOT_COMMANDS_CASEFOLD or folded in PENDING_SANBOT_RESPONSES:
        return True
    if re.match(r"(?i)^(?:JM|JAV|AV|DB|TG)", normalized):
        return True
    compact = re.sub(r"\s+", "", normalized).upper()
    if compact in {"JM日榜", "JM周榜", "JM月榜", "DB日榜", "DB周榜", "DB月榜"}:
        return True
    if re.fullmatch(r"\d{1,2}", normalized):
        return True
    return any(
        pattern.match(normalized)
        for pattern in (
            JM_DOWNLOAD_RE,
            JM_SEARCH_RE,
            JAV_QUERY_RE,
            AV_SEARCH_RE,
            ACTOR_SEARCH_RE,
            TG_COMMAND_RE,
            ADMIN_CANCEL_RE,
        )
    )

