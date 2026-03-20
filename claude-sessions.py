#!/usr/bin/env python3
"""Claude Code Session Manager TUI - 会话管理器

用法: python claude-sessions.py [--select] [--project <path>] [--limit <n>] [--list]

--select 模式: TUI 渲染到 stderr，选中的会话信息输出到 stdout（供 shell 包装函数使用）
默认模式: 直接运行，选中后在当前进程内启动 claude
"""

import json
import os
import sys
import re
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

# ── 输出流：TUI 渲染目标，--select 时用 stderr，否则用 stdout ─────────────────

_tui_out = sys.stdout  # 会在 main() 中根据 --select 切换

def tui_write(s):
    _tui_out.write(s)

def tui_flush():
    _tui_out.flush()

# ── ANSI helpers ──────────────────────────────────────────────────────────────

ESC = "\033"
CSI = f"{ESC}["

def term_size():
    # 优先用 stderr 获取终端尺寸（--select 模式下 stdout 被管道捕获会失败）
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

RESET   = f"{CSI}0m"
BOLD    = f"{CSI}1m"
DIM     = f"{CSI}2m"
FG_RED     = f"{CSI}31m"
FG_GREEN   = f"{CSI}32m"
FG_YELLOW  = f"{CSI}33m"
FG_CYAN    = f"{CSI}36m"
FG_WHITE   = f"{CSI}37m"
FG_GRAY    = f"{CSI}90m"
BG_SELECT  = f"{CSI}48;5;236m"
BG_HEADER  = f"{CSI}48;5;17m"

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

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SESSIONS_DIR = CLAUDE_DIR / "sessions"

def decode_project_path(encoded: str) -> str:
    m = re.match(r"^([A-Za-z])--(.*)$", encoded)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2)
        root = f"{drive}:{os.sep}"
    else:
        rest = encoded
        root = os.sep

    # rest 中的 "-" 可能是路径分隔符、空格、下划线或原始连字符
    # 策略：逐个 "-" 尝试各种解释，优先匹配文件系统中实际存在的路径
    segments = rest.split("-")
    if not segments:
        return root

    def _resolve(base: str, idx: int) -> str | None:
        if idx >= len(segments):
            return base
        # 从最长的合并段开始，尝试用不同字符连接
        for end in range(len(segments), idx, -1):
            joiners = (" ", "_", "-") if end > idx + 1 else (" ",)
            for joiner in joiners:
                name = joiner.join(segments[idx:end])
                candidate = os.path.join(base, name)
                if os.path.exists(candidate):
                    result = _resolve(candidate, end)
                    if result is not None:
                        return result
        # 单段作为路径分隔符处理（不检查存在性，作为 fallback）
        return _resolve(os.path.join(base, segments[idx]), idx + 1)

    result = _resolve(root, 0)
    return result if result else root

def get_active_pids() -> dict[str, int]:
    active = {}
    if SESSIONS_DIR.exists():
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("sessionId", "")
                pid = data.get("pid", 0)
                if sid and pid:
                    active[sid] = pid
            except Exception:
                pass
    return active

def is_real_user_message(content: str) -> bool:
    if not content:
        return False
    stripped = content.strip()
    if stripped.startswith("<") and not stripped.startswith("<!"):
        return False
    if stripped.startswith("/") and len(stripped) < 30 and " " not in stripped.strip():
        return False
    return True

def extract_user_text(content) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                break
            elif isinstance(block, str):
                text = block.strip()
                break
    else:
        return ""
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<local-command-caveat>.*?</local-command-caveat>", "", text, flags=re.DOTALL).strip()
    return text

def parse_session_file(jsonl_path: Path, project_key: str) -> Session | None:
    session_id = jsonl_path.stem
    fallback_path = decode_project_path(project_key)
    session = Session(session_id=session_id, project_path=fallback_path, project_key=project_key)
    cwd_found = False

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

                if not cwd_found:
                    cwd = entry.get("cwd", "")
                    if cwd:
                        session.project_path = cwd
                        cwd_found = True

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

                if msg_type == "user":
                    session.user_message_count += 1
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if not session.first_message:
                        text = extract_user_text(content)
                        if is_real_user_message(text):
                            session.first_message = text
                    if not session.version:
                        session.version = entry.get("version", "")
                    if not session.git_branch:
                        session.git_branch = entry.get("gitBranch", "")

                elif msg_type == "assistant":
                    session.assistant_message_count += 1
                    msg = entry.get("message", {})
                    usage = msg.get("usage", {})
                    session.total_input_tokens += usage.get("input_tokens", 0)
                    session.total_output_tokens += usage.get("output_tokens", 0)
                    if not session.model:
                        session.model = msg.get("model", "")
    except Exception:
        return None

    if session.message_count == 0:
        return None
    return session

def scan_sessions(project_filter: str | None = None, limit: int = 200) -> list[Session]:
    if not PROJECTS_DIR.exists():
        return []
    active_pids = get_active_pids()
    sessions: list[Session] = []
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        project_key = proj_dir.name
        for jsonl_file in proj_dir.glob("*.jsonl"):
            s = parse_session_file(jsonl_file, project_key)
            if s:
                s.is_active = s.session_id in active_pids
                if project_filter and project_filter.lower() not in s.project_path.lower():
                    continue
                sessions.append(s)
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
        """当前终端下可显示的会话行数"""
        _, rows = term_size()
        # 占用行: 1 header + 1 help + 1 col-header + 1 separator + 1 gap + 1 footer = 6
        return max(1, rows - 6)

    def _clamp_scroll(self):
        """确保 cursor 和 scroll_offset 在合法范围内"""
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

        title = " ◆ Claude Code 会话管理器"
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
        msg_w = max(0, cols - 2 - COL_TIME - COL_GAP - COL_PROJECT - COL_GAP)

        if cols < 50:
            return

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
            f"  {FG_CYAN}Git 分支:{RESET}   {s.git_branch or 'N/A'}",
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
        """用户按下 Enter 选中会话"""
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
                        self.search_query = ""
                        self.filter_sessions()
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

    import argparse
    parser = argparse.ArgumentParser(description="Claude Code 会话管理器")
    parser.add_argument("--project", "-p", help="按项目路径过滤")
    parser.add_argument("--limit", "-l", type=int, default=200, help="最大加载会话数")
    parser.add_argument("--list", action="store_true", help="仅列出会话（非交互）")
    parser.add_argument("--select", "-s", action="store_true",
                        help="选择模式：TUI 渲染到 stderr，选中结果输出到 stdout")
    args = parser.parse_args()

    # --select 模式: TUI 画面输出到 stderr，让 stdout 只给结果
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
        loading_out.write(f"{DIM}请确认 ~/.claude/projects/ 目录存在且包含会话文件。{RESET}\n")
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
        print(f"\n{DIM}使用 claude --resume <session-id> 恢复会话{RESET}")
        return

    tui = TUI(sessions, select_mode=args.select)
    selected = tui.run()

    if not selected:
        return

    if args.select:
        # --select 模式: 输出到 stdout 供 shell 包装函数解析
        print(selected.session_id)
        print(selected.project_path)
    else:
        # 直接模式: 在当前进程启动 claude
        project_dir = selected.project_path
        if os.path.isdir(project_dir):
            os.chdir(project_dir)
        else:
            sys.stderr.write(f"项目目录不存在: {project_dir}\n将在当前目录尝试恢复...\n")

        cmd = ["claude", "--resume", selected.session_id]
        try:
            sys.exit(subprocess.run(cmd, shell=(sys.platform == "win32")).returncode)
        except Exception as e:
            print(f"无法启动 claude: {e}")


if __name__ == "__main__":
    main()
