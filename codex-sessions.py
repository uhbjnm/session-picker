#!/usr/bin/env python3
"""Codex Session Manager TUI - 会话管理器

用法: python codex-session-picker-tui.py [--select] [--project <path>] [--limit <n>] [--list]

--select 模式: TUI 渲染到 stderr，选中的会话信息输出到 stdout（供 shell 包装函数使用）
默认模式: 直接运行，选中后在当前进程内启动 codex resume
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ── 输出流：TUI 渲染目标，--select 时用 stderr，否则用 stdout ─────────────────

_tui_out = sys.stdout


def tui_write(s):
    _tui_out.write(s)


def tui_flush():
    _tui_out.flush()


# ── ANSI helpers ──────────────────────────────────────────────────────────────

ESC = "\033"
CSI = f"{ESC}["


def term_size():
    for fd in (sys.stderr.fileno(), sys.stdout.fileno()):
        try:
            return os.get_terminal_size(fd)
        except (OSError, ValueError):
            continue
    return 120, 40


def move(r, c):       return f"{CSI}{r};{c}H"
def clear_screen():   tui_write(f"{CSI}2J{CSI}H"); tui_flush()
def clear_line():     return f"{CSI}2K"
def hide_cursor():    tui_write(f"{CSI}?25l"); tui_flush()
def show_cursor():    tui_write(f"{CSI}?25h"); tui_flush()


RESET = f"{CSI}0m"
BOLD = f"{CSI}1m"
DIM = f"{CSI}2m"
FG_RED = f"{CSI}31m"
FG_GREEN = f"{CSI}32m"
FG_YELLOW = f"{CSI}33m"
FG_CYAN = f"{CSI}36m"
FG_WHITE = f"{CSI}37m"
FG_GRAY = f"{CSI}90m"
BG_SELECT = f"{CSI}48;5;236m"
BG_HEADER = f"{CSI}48;5;17m"


# ── Display width helpers (CJK-aware) ────────────────────────────────────────

def display_width(s: str) -> int:
    w = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def pad_right(s: str, width: int) -> str:
    dw = display_width(s)
    if dw >= width:
        return s
    return s + " " * (width - dw)


def truncate_to_width(text: str, maxw: int) -> str:
    text = text.replace("\n", " ").replace("\r", "").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    w = 0
    for i, ch in enumerate(text):
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > maxw - 1:
            return text[:i] + "…"
        w += cw
    return text


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Session:
    session_id: str
    project_path: str
    project_key: str
    first_message: str = ""
    last_timestamp: float = 0
    first_timestamp: float = 0
    message_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    model: str = ""
    version: str = ""
    git_branch: str = ""
    is_active: bool = False


# ── Session scanner ───────────────────────────────────────────────────────────

CODEX_DIR = Path.home() / ".codex"
SESSIONS_DIR = CODEX_DIR / "sessions"
SESSION_INDEX = CODEX_DIR / "session_index.jsonl"
HISTORY_FILE = CODEX_DIR / "history.jsonl"

MARKERS = (
    "## User's Current IDE Context",
    "### `",
    "```",
    "<environment_context>",
    "# AGENTS.md instructions",
)


def decode_project_path(encoded: str) -> str:
    # Codex 的 project_key 是日期路径 (2026/03/20)，不是项目路径
    # 真实项目路径来自 session_meta/turn_context 的 cwd 字段
    return ""


def get_active_pids() -> dict[str, int]:
    # Codex 的本地会话目录里没有 Claude 那种 pid 文件；先保留同样接口。
    return {}


def is_real_user_message(content: str) -> bool:
    if not content:
        return False
    stripped = content.strip()
    if stripped.startswith("<") and not stripped.startswith("<!"):
        return False
    if stripped.startswith("/") and len(stripped) < 30 and " " not in stripped.strip():
        return False
    return True


def normalize_user_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    lowered = text.lower()
    cut = None
    for marker in MARKERS:
        idx = lowered.find(marker.lower())
        if idx >= 0:
            cut = idx if cut is None else min(cut, idx)
    if cut is not None:
        text = text[:cut].strip()
    if text.startswith("# AGENTS.md instructions") or text.startswith("<environment_context>"):
        return ""
    return text


def extract_user_text(content) -> str:
    if isinstance(content, str):
        return normalize_user_text(content)

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block.strip())
                continue
            if isinstance(block, dict):
                for key in ("text", "content"):
                    value = block.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
                        break
        return normalize_user_text(" ".join(parts))

    return ""


def load_session_index() -> dict[str, str]:
    """从 session_index.jsonl 加载 {session_id: thread_name}"""
    index: dict[str, str] = {}
    if not SESSION_INDEX.exists():
        return index
    try:
        with open(SESSION_INDEX, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = entry.get("id", "")
                name = entry.get("thread_name", "")
                if sid and name:
                    index[sid] = name
    except Exception:
        pass
    return index


def load_history_index() -> dict[str, str]:
    history: dict[str, str] = {}
    if not HISTORY_FILE.exists():
        return history

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = entry.get("session_id", "")
                text = normalize_user_text(entry.get("text", "")) if isinstance(entry.get("text"), str) else ""
                if sid and text and sid not in history:
                    history[sid] = text
    except Exception:
        pass

    return history


def _extract_uuid_from_filename(stem: str) -> str:
    """从 rollout-2026-03-20T12-03-54-019d0969-ff65-7c53-b5ff-89939071c60c 提取 UUID"""
    # 匹配文件名末尾的 UUIDv7（5段，8-4-4-4-12 格式）
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", stem, re.I)
    if m:
        return m.group(1)
    return stem


def parse_session_file(jsonl_path: Path, project_key: str, history_index: dict[str, str]) -> Session | None:
    session_id = _extract_uuid_from_filename(jsonl_path.stem)
    project_path = decode_project_path(project_key)
    session = Session(session_id=session_id, project_path=project_path, project_key=project_key)

    try:
        with open(jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = entry.get("type", "")
                ts_raw = entry.get("timestamp")
                ts = 0.0
                if isinstance(ts_raw, str):
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        pass
                elif isinstance(ts_raw, (int, float)):
                    ts = ts_raw / 1000.0 if ts_raw > 1e12 else ts_raw

                if ts > 0:
                    if session.first_timestamp == 0 or ts < session.first_timestamp:
                        session.first_timestamp = ts
                    if ts > session.last_timestamp:
                        session.last_timestamp = ts

                session.message_count += 1

                payload = entry.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}

                if msg_type == "session_meta":
                    if isinstance(payload.get("id"), str):
                        session.session_id = payload["id"]
                    if isinstance(payload.get("cwd"), str):
                        session.project_path = payload["cwd"]
                    if not session.version:
                        session.version = payload.get("cli_version", "") if isinstance(payload.get("cli_version"), str) else ""
                    if not session.git_branch:
                        provider = payload.get("model_provider", "") if isinstance(payload.get("model_provider"), str) else ""
                        source = payload.get("source", "") if isinstance(payload.get("source"), str) else ""
                        session.git_branch = provider or source

                elif msg_type == "turn_context":
                    if isinstance(payload.get("cwd"), str):
                        session.project_path = payload["cwd"]
                    if not session.model and isinstance(payload.get("model"), str):
                        session.model = payload["model"]

                elif msg_type == "event_msg":
                    event_type = payload.get("type", "")
                    if event_type == "user_message":
                        text = normalize_user_text(payload.get("message", "")) if isinstance(payload.get("message"), str) else ""
                        if is_real_user_message(text):
                            session.user_message_count += 1
                            if not session.first_message:
                                session.first_message = text
                    elif event_type == "token_count":
                        info = payload.get("info", {})
                        if isinstance(info, dict):
                            usage = info.get("total_token_usage", {})
                            if isinstance(usage, dict):
                                session.total_input_tokens = int(usage.get("input_tokens", session.total_input_tokens) or 0)
                                session.total_output_tokens = int(usage.get("output_tokens", session.total_output_tokens) or 0)

                elif msg_type == "response_item":
                    role = payload.get("role", "")
                    text = extract_user_text(payload.get("content", []))
                    if payload.get("type") == "message" and role == "user":
                        if is_real_user_message(text):
                            session.user_message_count += 1
                            if not session.first_message:
                                session.first_message = text
                    elif payload.get("type") == "message" and role == "assistant":
                        session.assistant_message_count += 1
                        if not session.model and isinstance(payload.get("model"), str):
                            session.model = payload["model"]
    except Exception:
        return None

    if not session.first_message:
        session.first_message = history_index.get(session.session_id, "")

    if session.message_count == 0:
        return None
    return session


def scan_sessions(project_filter: str | None = None, limit: int = 200) -> list[Session]:
    if not SESSIONS_DIR.exists():
        return []

    active_pids = get_active_pids()
    history_index = load_history_index()
    session_names = load_session_index()
    sessions_by_id: dict[str, Session] = {}

    for jsonl_file in SESSIONS_DIR.rglob("*.jsonl"):
        if jsonl_file.stat().st_size == 0:
            continue
        rel = jsonl_file.parent.relative_to(SESSIONS_DIR)
        project_key = str(rel).replace("\\", "/")
        s = parse_session_file(jsonl_file, project_key, history_index)
        if not s:
            continue
        s.is_active = s.session_id in active_pids
        # 用 session_index 的 thread_name 作为首条消息的补充
        if not s.first_message and s.session_id in session_names:
            s.first_message = session_names[s.session_id]
        if project_filter and project_filter.lower() not in s.project_path.lower():
            continue
        prev = sessions_by_id.get(s.session_id)
        if prev is None or s.last_timestamp > prev.last_timestamp:
            sessions_by_id[s.session_id] = s

    sessions = list(sessions_by_id.values())
    sessions.sort(key=lambda s: s.last_timestamp, reverse=True)
    return sessions[:limit]


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_time(ts: float) -> str:
    if ts == 0:
        return "N/A"
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    diff = now.timestamp() - ts
    if diff < 60:
        return "刚刚"
    elif diff < 3600:
        return f"{int(diff // 60)} 分钟前"
    elif diff < 86400:
        return f"{int(diff // 3600)} 小时前"
    elif diff < 86400 * 2:
        return f"昨天 {dt.strftime('%H:%M')}"
    elif diff < 86400 * 7:
        return f"{int(diff // 86400)} 天前"
    elif dt.year == now.year:
        return dt.strftime("%m-%d %H:%M")
    else:
        return dt.strftime("%Y-%m-%d")


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def shorten_path(path: str, maxw: int) -> str:
    if display_width(path) <= maxw:
        return path
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 2:
        return truncate_to_width(path, maxw)
    tail = "/".join(parts[-2:])
    result = parts[0] + "/…/" + tail
    if display_width(result) > maxw:
        result = truncate_to_width(result, maxw)
    return result


# ── TUI Engine ────────────────────────────────────────────────────────────────

class TUI:
    def __init__(self, sessions: list[Session], select_mode: bool = False):
        self.all_sessions = sessions
        self.sessions = sessions[:]
        self.cursor = 0
        self.scroll_offset = 0
        self.search_query = ""
        self.searching = False
        self.detail_mode = False
        self.running = True
        self.selected: Session | None = None
        self.select_mode = select_mode

    def _visible_count(self) -> int:
        _, rows = term_size()
        # 占用行: 1 header + 1 help + 1 col-header + 1 separator + 1 gap + 1 footer = 6
        return max(1, rows - 6)

    def _clamp_scroll(self):
        total = len(self.sessions)
        if total == 0:
            self.cursor = 0
            self.scroll_offset = 0
            return
        self.cursor = max(0, min(self.cursor, total - 1))
        vc = self._visible_count()
        if self.cursor < self.scroll_offset:
            self.scroll_offset = self.cursor
        if self.cursor >= self.scroll_offset + vc:
            self.scroll_offset = self.cursor - vc + 1
        self.scroll_offset = max(0, min(self.scroll_offset, total - vc))

    def filter_sessions(self):
        if not self.search_query:
            self.sessions = self.all_sessions[:]
        else:
            q = self.search_query.lower()
            self.sessions = [
                s for s in self.all_sessions
                if q in s.first_message.lower()
                or q in s.project_path.lower()
                or q in s.session_id.lower()
            ]
        self.sessions.sort(key=lambda s: s.last_timestamp, reverse=True)
        self.cursor = min(self.cursor, max(0, len(self.sessions) - 1))
        self.scroll_offset = 0

    def render(self):
        self._clamp_scroll()
        cols, rows = term_size()
        buf = []

        title = " ◆ Codex 会话管理器"
        count = f"{len(self.sessions)}/{len(self.all_sessions)} 会话 "
        title_w = display_width(title)
        count_w = display_width(count)
        pad = cols - title_w - count_w
        buf.append(
            move(1, 1) + BG_HEADER + FG_WHITE + BOLD
            + title + " " * max(0, pad) + count
            + RESET
        )

        if self.searching:
            buf.append(
                move(2, 1) + clear_line()
                + FG_YELLOW + BOLD + " / " + RESET
                + self.search_query + "█"
            )
        else:
            buf.append(
                move(2, 1) + clear_line()
                + DIM + " ↑↓/jk:选择  Enter:恢复  /:搜索  d:详情  q:退出" + RESET
            )

        content_start = 3
        content_rows = rows - content_start - 1

        if self.detail_mode and self.sessions:
            self._render_detail(buf, content_start, content_rows, cols)
        else:
            self._render_list(buf, content_start, content_rows, cols)

        footer_row = rows
        if self.sessions and not self.detail_mode:
            s = self.sessions[self.cursor] if self.cursor < len(self.sessions) else None
            if s:
                footer = f" ID: {s.session_id[:20]}…  模型: {s.model or 'N/A'}  版本: {s.version or 'N/A'}"
                if s.is_active:
                    footer += f"  {FG_GREEN}● 运行中{RESET}{DIM}"
                buf.append(move(footer_row, 1) + clear_line() + DIM + footer + RESET)
        elif self.detail_mode:
            buf.append(
                move(footer_row, 1) + clear_line()
                + DIM + " Enter:恢复  d/Esc:返回列表" + RESET
            )

        tui_write("".join(buf))
        tui_flush()

    def _render_list(self, buf, start_row, max_rows, cols):
        if not self.sessions:
            buf.append(
                move(start_row + 2, 1) + clear_line()
                + FG_YELLOW + "  没有找到匹配的会话。" + RESET
            )
            for r in range(start_row, start_row + max_rows):
                buf.append(move(r + 1, 1) + clear_line())
            return

        COL_TIME = 12
        COL_GAP = 2
        COL_PROJECT = 30
        msg_w = cols - 2 - COL_TIME - COL_GAP - COL_PROJECT - COL_GAP

        hdr = (
            "  "
            + pad_right("时间", COL_TIME)
            + " " * COL_GAP
            + pad_right("项目", COL_PROJECT)
            + " " * COL_GAP
            + "首条消息"
        )
        buf.append(move(start_row, 1) + clear_line() + FG_CYAN + BOLD + hdr + RESET)
        buf.append(move(start_row + 1, 1) + clear_line() + DIM + "─" * cols + RESET)

        visible_start = start_row + 2
        for i in range(max_rows - 2):
            idx = self.scroll_offset + i
            row = visible_start + i
            buf.append(move(row, 1) + clear_line())
            if idx >= len(self.sessions):
                continue

            s = self.sessions[idx]
            is_selected = idx == self.cursor

            time_str = format_time(s.last_timestamp)
            path_str = shorten_path(s.project_path, COL_PROJECT)
            summary = truncate_to_width(s.first_message or "(无消息)", max(10, msg_w))

            prefix = "▸ " if is_selected else "  "
            line = (
                prefix
                + pad_right(time_str, COL_TIME)
                + " " * COL_GAP
                + pad_right(path_str, COL_PROJECT)
                + " " * COL_GAP
                + summary
            )

            active_marker = f" {FG_GREEN}●{RESET}" if s.is_active else ""

            if is_selected:
                buf.append(BG_SELECT + FG_WHITE + BOLD + line + active_marker + RESET)
            else:
                age = time.time() - s.last_timestamp if s.last_timestamp else 999999
                if age > 86400 * 7:
                    buf.append(DIM + line + active_marker + RESET)
                else:
                    buf.append(line + active_marker)

    def _render_detail(self, buf, start_row, max_rows, cols):
        s = self.sessions[self.cursor]
        sep = "  " + "─" * 50
        status = "● 运行中" if s.is_active else "○ 已结束"
        start_t = datetime.fromtimestamp(s.first_timestamp).strftime("%Y-%m-%d %H:%M:%S") if s.first_timestamp else "N/A"
        last_t = datetime.fromtimestamp(s.last_timestamp).strftime("%Y-%m-%d %H:%M:%S") if s.last_timestamp else "N/A"
        dur = self._format_duration(s.last_timestamp - s.first_timestamp) if s.first_timestamp and s.last_timestamp else "N/A"
        lines = [
            "",
            f"  {BOLD}会话详情{RESET}",
            sep,
            f"  {FG_CYAN}会话 ID:{RESET}    {s.session_id}",
            f"  {FG_CYAN}项目路径:{RESET}   {s.project_path}",
            f"  {FG_CYAN}提供方:{RESET}     {s.git_branch or 'N/A'}",
            f"  {FG_CYAN}模型:{RESET}       {s.model or 'N/A'}",
            f"  {FG_CYAN}版本:{RESET}       {s.version or 'N/A'}",
            f"  {FG_CYAN}状态:{RESET}       {status}",
            sep,
            f"  {FG_CYAN}开始时间:{RESET}   {start_t}",
            f"  {FG_CYAN}最后活动:{RESET}   {last_t} ({format_time(s.last_timestamp)})",
            f"  {FG_CYAN}持续时长:{RESET}   {dur}",
            sep,
            f"  {FG_CYAN}总消息数:{RESET}   {s.message_count}  (用户: {s.user_message_count}, 助手: {s.assistant_message_count})",
            f"  {FG_CYAN}输入Token:{RESET}  {s.total_input_tokens:,}  ({format_tokens(s.total_input_tokens)})",
            f"  {FG_CYAN}输出Token:{RESET}  {s.total_output_tokens:,}  ({format_tokens(s.total_output_tokens)})",
            sep,
            f"  {FG_CYAN}首条消息:{RESET}",
        ]
        msg = s.first_message or "(无消息)"
        wrap_w = cols - 6
        for i in range(0, len(msg), wrap_w):
            lines.append(f"    {msg[i:i+wrap_w]}")

        for i, line_text in enumerate(lines):
            if i >= max_rows:
                break
            buf.append(move(start_row + i, 1) + clear_line() + line_text)
        for r in range(start_row + len(lines), start_row + max_rows):
            buf.append(move(r, 1) + clear_line())

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 0:
            return "N/A"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}时{m}分{s}秒"
        elif m > 0:
            return f"{m}分{s}秒"
        return f"{s}秒"

    def on_select(self, session: Session):
        self.selected = session
        self.running = False

    def run(self) -> Session | None:
        if sys.platform == "win32":
            os.system("")
            import msvcrt

            def read_key():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    ch2 = msvcrt.getwch()
                    mapping = {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                               "G": "HOME", "O": "END", "I": "PGUP", "Q": "PGDN"}
                    return mapping.get(ch2, "")
                return ch
        else:
            import tty, termios
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin)

            def read_key():
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    ch2 = sys.stdin.read(1)
                    if ch2 == "[":
                        ch3 = sys.stdin.read(1)
                        mapping = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT",
                                   "H": "HOME", "F": "END", "5": "PGUP", "6": "PGDN"}
                        if ch3 in ("5", "6"):
                            sys.stdin.read(1)
                        return mapping.get(ch3, "ESC")
                    return "ESC"
                return ch

        hide_cursor()
        clear_screen()

        try:
            while self.running:
                self.render()
                key = read_key()

                if self.searching:
                    if key == "ESC" or key == "\x1b":
                        self.searching = False
                    elif key in ("\r", "\n"):
                        self.searching = False
                    elif key in ("\x08", "\x7f"):
                        self.search_query = self.search_query[:-1]
                        self.filter_sessions()
                    elif isinstance(key, str) and len(key) == 1 and key.isprintable():
                        self.search_query += key
                        self.filter_sessions()
                    continue

                if key in ("q", "\x03", "\x1b"):
                    if self.detail_mode:
                        self.detail_mode = False
                    else:
                        self.running = False
                elif key in ("UP", "k"):
                    self.cursor = max(0, self.cursor - 1)
                elif key in ("DOWN", "j"):
                    self.cursor = min(len(self.sessions) - 1, self.cursor + 1)
                elif key == "PGUP":
                    _, rows = term_size()
                    self.cursor = max(0, self.cursor - (rows - 5))
                    self.scroll_offset = max(0, self.scroll_offset - (rows - 5))
                elif key == "PGDN":
                    _, rows = term_size()
                    self.cursor = min(len(self.sessions) - 1, self.cursor + (rows - 5))
                elif key == "HOME":
                    self.cursor = 0
                    self.scroll_offset = 0
                elif key == "END":
                    self.cursor = max(0, len(self.sessions) - 1)
                elif key == "/":
                    self.searching = True
                elif key == "d":
                    if self.detail_mode:
                        self.detail_mode = False
                    elif self.sessions:
                        self.detail_mode = True
                elif key in ("\r", "\n"):
                    if self.sessions:
                        self.on_select(self.sessions[self.cursor])
        except KeyboardInterrupt:
            pass
        finally:
            show_cursor()
            clear_screen()
            if sys.platform != "win32":
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        return self.selected


def main():
    global _tui_out

    parser = argparse.ArgumentParser(description="Codex 会话管理器")
    parser.add_argument("--project", "-p", help="按项目路径过滤")
    parser.add_argument("--limit", "-l", type=int, default=200, help="最大加载会话数")
    parser.add_argument("--list", action="store_true", help="仅列出会话（非交互）")
    parser.add_argument("--select", "-s", action="store_true",
                        help="选择模式：TUI 渲染到 stderr，选中结果输出到 stdout")
    args = parser.parse_args()

    if args.select:
        _tui_out = sys.stderr

    loading_out = _tui_out
    loading_out.write(f"{FG_CYAN}正在扫描会话...{RESET}")
    loading_out.flush()
    sessions = scan_sessions(project_filter=args.project, limit=args.limit)
    loading_out.write(f"\r{clear_line()}")
    loading_out.flush()

    if not sessions:
        loading_out.write(f"{FG_YELLOW}未找到任何会话记录。{RESET}\n")
        loading_out.write(f"{DIM}请确认 ~/.codex/sessions/ 目录存在且包含会话文件。{RESET}\n")
        loading_out.flush()
        return

    if args.list:
        print(f"{BOLD}找到 {len(sessions)} 个会话:{RESET}\n")
        for i, s in enumerate(sessions):
            active = f" {FG_GREEN}[运行中]{RESET}" if s.is_active else ""
            t = format_time(s.last_timestamp)
            p = shorten_path(s.project_path, 30)
            m = truncate_to_width(s.first_message or "(无消息)", 50)
            print(
                f"  {FG_CYAN}{i+1:3}.{RESET} "
                f"{pad_right(t, 12)}  "
                f"{pad_right(p, 30)}  "
                f"{m}{active}"
            )
        print(f"\n{DIM}使用 codex resume <session-id> 恢复会话{RESET}")
        return

    tui = TUI(sessions, select_mode=args.select)
    selected = tui.run()

    if not selected:
        return

    if args.select:
        print(selected.session_id)
        print(selected.project_path)
    else:
        project_dir = selected.project_path
        if os.path.isdir(project_dir):
            os.chdir(project_dir)
        else:
            sys.stderr.write(f"项目目录不存在: {project_dir}\n将在当前目录尝试恢复...\n")

        cmd = ["codex", "resume", selected.session_id]
        try:
            sys.exit(subprocess.run(cmd, shell=(sys.platform == "win32")).returncode)
        except Exception as e:
            print(f"无法启动 codex: {e}")


if __name__ == "__main__":
    main()
