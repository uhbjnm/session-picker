<#
.SYNOPSIS
    Claude Code Session Manager (cs) - 一键安装脚本
.DESCRIPTION
    安装 cs 命令到当前用户环境，用于快速浏览和恢复 Claude Code 会话。
    - cs     : 交互式 TUI 选择并恢复会话
    - csl    : 文本列表模式查看所有会话
.NOTES
    要求: Python 3.10+, Claude Code CLI
    用法: irm https://your-url/install-cs.ps1 | iex
    或者: powershell -ExecutionPolicy Bypass -File install-cs.ps1
#>

$ErrorActionPreference = "Stop"

# ── 检查依赖 ──────────────────────────────────────────────────────────────────

Write-Host "`n  Claude Code Session Manager - Installer" -ForegroundColor Cyan
Write-Host "  ========================================`n" -ForegroundColor DarkGray

# ── 检查 Python (要求 3.10+) ──

function Test-Python {
    param([string]$PythonCmd)
    try {
        $ver = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $ver) { return $null }
        $parts = $ver.Split(".")
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        if ($major -ge 3 -and $minor -ge 10) {
            return @{ Version = $ver; Command = $PythonCmd }
        }
        Write-Host "  [x] $PythonCmd is version $ver, need 3.10+" -ForegroundColor Red
        return $null
    } catch {
        return $null
    }
}

# 依次尝试: python, python3, py -3
$pythonExe = $null
foreach ($candidate in @("python", "python3", "py")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $result = if ($candidate -eq "py") { Test-Python "py -3" } else { Test-Python $candidate }
        if ($result) {
            $pythonExe = $result.Command
            Write-Host "  [ok] Python $($result.Version) ($pythonExe)" -ForegroundColor Green
            break
        }
    }
}

# 未找到或版本不够，让用户手动指定
while (-not $pythonExe) {
    Write-Host ""
    Write-Host "  Python 3.10+ not found in PATH." -ForegroundColor Yellow
    $customPath = Read-Host "  Enter Python install directory (or 'q' to quit)"
    if ($customPath -eq 'q' -or $customPath -eq 'Q') { exit 1 }

    $customPath = $customPath.Trim('"', "'", ' ')
    # 支持输入目录或直接输入 python.exe 路径
    if (Test-Path (Join-Path $customPath "python.exe")) {
        $candidate = Join-Path $customPath "python.exe"
    } elseif (Test-Path $customPath -PathType Leaf) {
        $candidate = $customPath
    } else {
        Write-Host "  [x] python.exe not found in: $customPath" -ForegroundColor Red
        continue
    }

    $result = Test-Python $candidate
    if ($result) {
        $pythonExe = $result.Command
        Write-Host "  [ok] Python $($result.Version) ($pythonExe)" -ForegroundColor Green
    }
}

# 检查 Claude Code
$cc = Get-Command claude -ErrorAction SilentlyContinue
if ($cc) {
    Write-Host "  [ok] Claude Code CLI found" -ForegroundColor Green
} else {
    Write-Host "  [!!] Claude Code CLI not found (cs will install but resume won't work)" -ForegroundColor Yellow
}

# ── 写入 Python 脚本 ─────────────────────────────────────────────────────────

$scriptDir = Join-Path $HOME ".claude" "scripts"
$scriptPath = Join-Path $scriptDir "claude-sessions.py"

New-Item -ItemType Directory -Force -Path $scriptDir | Out-Null

$pyScript = @'
#!/usr/bin/env python3
"""Claude Code Session Manager TUI"""

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

_tui_out = sys.stdout

def tui_write(s):
    _tui_out.write(s)

def tui_flush():
    _tui_out.flush()

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
            return text[:i] + "\u2026"
        w += cw
    return text

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

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SESSIONS_DIR = CLAUDE_DIR / "sessions"

def decode_project_path(encoded: str) -> str:
    m = re.match(r"^([A-Za-z])--(.*)$", encoded)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace("-", os.sep)
        return f"{drive}:{os.sep}{rest}"
    return encoded.replace("-", os.sep)

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

def scan_sessions(project_filter=None, limit=200):
    if not PROJECTS_DIR.exists():
        return []
    active_pids = get_active_pids()
    sessions = []
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

def format_time(ts: float) -> str:
    if ts == 0:
        return "N/A"
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    diff = now.timestamp() - ts
    if diff < 60:
        return "\u521a\u521a"
    elif diff < 3600:
        return f"{int(diff // 60)} \u5206\u949f\u524d"
    elif diff < 86400:
        return f"{int(diff // 3600)} \u5c0f\u65f6\u524d"
    elif diff < 86400 * 2:
        return f"\u6628\u5929 {dt.strftime('%H:%M')}"
    elif diff < 86400 * 7:
        return f"{int(diff // 86400)} \u5929\u524d"
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
    result = parts[0] + "/\u2026/" + tail
    if display_width(result) > maxw:
        result = truncate_to_width(result, maxw)
    return result

class TUI:
    def __init__(self, sessions, select_mode=False):
        self.all_sessions = sessions
        self.sessions = sessions[:]
        self.cursor = 0
        self.scroll_offset = 0
        self.search_query = ""
        self.searching = False
        self.detail_mode = False
        self.running = True
        self.selected = None
        self.select_mode = select_mode

    def _visible_count(self):
        _, rows = term_size()
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

        title = " \u25c6 Claude Code \u4f1a\u8bdd\u7ba1\u7406\u5668"
        count = f"{len(self.sessions)}/{len(self.all_sessions)} \u4f1a\u8bdd "
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
                + self.search_query + "\u2588"
            )
        else:
            buf.append(
                move(2, 1) + clear_line()
                + DIM + " \u2191\u2193/jk:\u9009\u62e9  Enter:\u6062\u590d  /:\u641c\u7d22  d:\u8be6\u60c5  q:\u9000\u51fa" + RESET
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
                footer = f" ID: {s.session_id[:20]}\u2026  \u6a21\u578b: {s.model or 'N/A'}  \u7248\u672c: {s.version or 'N/A'}"
                if s.is_active:
                    footer += f"  {FG_GREEN}\u25cf \u8fd0\u884c\u4e2d{RESET}{DIM}"
                buf.append(move(footer_row, 1) + clear_line() + DIM + footer + RESET)
        elif self.detail_mode:
            buf.append(
                move(footer_row, 1) + clear_line()
                + DIM + " Enter:\u6062\u590d  d/Esc:\u8fd4\u56de\u5217\u8868" + RESET
            )

        tui_write("".join(buf))
        tui_flush()

    def _render_list(self, buf, start_row, max_rows, cols):
        if not self.sessions:
            buf.append(
                move(start_row + 2, 1) + clear_line()
                + FG_YELLOW + "  \u6ca1\u6709\u627e\u5230\u5339\u914d\u7684\u4f1a\u8bdd\u3002" + RESET
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
            + pad_right("\u65f6\u95f4", COL_TIME)
            + " " * COL_GAP
            + pad_right("\u9879\u76ee", COL_PROJECT)
            + " " * COL_GAP
            + "\u9996\u6761\u6d88\u606f"
        )
        buf.append(move(start_row, 1) + clear_line() + FG_CYAN + BOLD + hdr + RESET)
        buf.append(move(start_row + 1, 1) + clear_line() + DIM + "\u2500" * cols + RESET)

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
            summary = truncate_to_width(s.first_message or "(\u65e0\u6d88\u606f)", max(10, msg_w))

            prefix = "\u25b8 " if is_selected else "  "
            line = (
                prefix
                + pad_right(time_str, COL_TIME)
                + " " * COL_GAP
                + pad_right(path_str, COL_PROJECT)
                + " " * COL_GAP
                + summary
            )

            active_marker = f" {FG_GREEN}\u25cf{RESET}" if s.is_active else ""

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
        sep = "  " + "\u2500" * 50
        status = "\u25cf \u8fd0\u884c\u4e2d" if s.is_active else "\u25cb \u5df2\u7ed3\u675f"
        start_t = datetime.fromtimestamp(s.first_timestamp).strftime("%Y-%m-%d %H:%M:%S") if s.first_timestamp else "N/A"
        last_t = datetime.fromtimestamp(s.last_timestamp).strftime("%Y-%m-%d %H:%M:%S") if s.last_timestamp else "N/A"
        dur = self._format_duration(s.last_timestamp - s.first_timestamp) if s.first_timestamp and s.last_timestamp else "N/A"
        lines = [
            "",
            f"  {BOLD}\u4f1a\u8bdd\u8be6\u60c5{RESET}",
            sep,
            f"  {FG_CYAN}\u4f1a\u8bdd ID:{RESET}    {s.session_id}",
            f"  {FG_CYAN}\u9879\u76ee\u8def\u5f84:{RESET}   {s.project_path}",
            f"  {FG_CYAN}Git \u5206\u652f:{RESET}   {s.git_branch or 'N/A'}",
            f"  {FG_CYAN}\u6a21\u578b:{RESET}       {s.model or 'N/A'}",
            f"  {FG_CYAN}\u7248\u672c:{RESET}       {s.version or 'N/A'}",
            f"  {FG_CYAN}\u72b6\u6001:{RESET}       {status}",
            sep,
            f"  {FG_CYAN}\u5f00\u59cb\u65f6\u95f4:{RESET}   {start_t}",
            f"  {FG_CYAN}\u6700\u540e\u6d3b\u52a8:{RESET}   {last_t} ({format_time(s.last_timestamp)})",
            f"  {FG_CYAN}\u6301\u7eed\u65f6\u957f:{RESET}   {dur}",
            sep,
            f"  {FG_CYAN}\u603b\u6d88\u606f\u6570:{RESET}   {s.message_count}  (\u7528\u6237: {s.user_message_count}, \u52a9\u624b: {s.assistant_message_count})",
            f"  {FG_CYAN}\u8f93\u5165Token:{RESET}  {s.total_input_tokens:,}  ({format_tokens(s.total_input_tokens)})",
            f"  {FG_CYAN}\u8f93\u51faToken:{RESET}  {s.total_output_tokens:,}  ({format_tokens(s.total_output_tokens)})",
            sep,
            f"  {FG_CYAN}\u9996\u6761\u6d88\u606f:{RESET}",
        ]
        msg = s.first_message or "(\u65e0\u6d88\u606f)"
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
    def _format_duration(seconds):
        if seconds < 0:
            return "N/A"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}\u65f6{m}\u5206{s}\u79d2"
        elif m > 0:
            return f"{m}\u5206{s}\u79d2"
        return f"{s}\u79d2"

    def on_select(self, session):
        self.selected = session
        self.running = False

    def run(self):
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
                    self.cursor = max(0, self.cursor - (rows - 6))
                elif key == "PGDN":
                    _, rows = term_size()
                    self.cursor = min(len(self.sessions) - 1, self.cursor + (rows - 6))
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
    parser = argparse.ArgumentParser(description="Claude Code Session Manager")
    parser.add_argument("--project", "-p", help="Filter by project path")
    parser.add_argument("--limit", "-l", type=int, default=200)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--select", "-s", action="store_true")
    args = parser.parse_args()

    if args.select:
        _tui_out = sys.stderr

    loading_out = _tui_out
    loading_out.write(f"{FG_CYAN}\u6b63\u5728\u626b\u63cf\u4f1a\u8bdd...{RESET}")
    loading_out.flush()
    sessions = scan_sessions(project_filter=args.project, limit=args.limit)
    loading_out.write(f"\r{clear_line()}")
    loading_out.flush()

    if not sessions:
        loading_out.write(f"{FG_YELLOW}\u672a\u627e\u5230\u4efb\u4f55\u4f1a\u8bdd\u8bb0\u5f55\u3002{RESET}\n")
        loading_out.flush()
        return

    if args.list:
        print(f"{BOLD}Found {len(sessions)} sessions:{RESET}\n")
        for i, s in enumerate(sessions):
            active = f" {FG_GREEN}[active]{RESET}" if s.is_active else ""
            t = format_time(s.last_timestamp)
            p = shorten_path(s.project_path, 30)
            m = truncate_to_width(s.first_message or "(-)", 50)
            print(f"  {FG_CYAN}{i+1:3}.{RESET} {pad_right(t, 12)}  {pad_right(p, 30)}  {m}{active}")
        print(f"\n{DIM}Use: claude --resume <session-id>{RESET}")
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
            sys.stderr.write(f"Project dir not found: {project_dir}\n")

        cmd = ["claude", "--resume", selected.session_id]
        try:
            sys.exit(subprocess.run(cmd, shell=True).returncode)
        except Exception as e:
            print(f"Failed to start claude: {e}")


if __name__ == "__main__":
    main()
'@

Set-Content -Path $scriptPath -Value $pyScript -Encoding UTF8
Write-Host "  [ok] Script installed: $scriptPath" -ForegroundColor Green

# ── 配置 PowerShell Profile ──────────────────────────────────────────────────

$profileDir = Split-Path $PROFILE -Parent
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
}
if (-not (Test-Path $PROFILE)) {
    New-Item -ItemType File -Force -Path $PROFILE | Out-Null
}

# 检查是否已安装，已安装则先移除旧版再写入新版（python 路径可能变了）
$profileContent = Get-Content $PROFILE -Raw -ErrorAction SilentlyContinue

# 构建 profile 代码块，使用检测到的 python 路径
$profileBlock = @"

# ── Claude Code Session Manager (cs) ──
function cs {
    `$result = & "$pythonExe" "`$HOME\.claude\scripts\claude-sessions.py" --select @args
    if (`$result) {
        `$lines = `$result -split "``n"
        `$sid = `$lines[0].Trim()
        `$proj = if (`$lines.Count -gt 1) { `$lines[1].Trim() } else { "" }
        if (`$sid) {
            if (`$proj -and (Test-Path `$proj)) { Set-Location `$proj }
            claude --resume `$sid
        }
    }
}
function csl { & "$pythonExe" "`$HOME\.claude\scripts\claude-sessions.py" --list @args }
"@

if ($profileContent -and $profileContent.Contains("Claude Code Session Manager (cs)")) {
    # 移除旧版块，替换为新版
    $pattern = '(?s)# ── Claude Code Session Manager \(cs\) ──.*?function csl \{[^\}]+\}'
    $newContent = [regex]::Replace($profileContent, $pattern, $profileBlock.TrimStart())
    Set-Content -Path $PROFILE -Value $newContent -Encoding UTF8
    Write-Host "  [ok] PowerShell profile updated (replaced old version)" -ForegroundColor Green
} else {
    Add-Content -Path $PROFILE -Value $profileBlock -Encoding UTF8
    Write-Host "  [ok] PowerShell profile updated: $PROFILE" -ForegroundColor Green
}

# ── 完成 ─────────────────────────────────────────────────────────────────────

Write-Host "`n  Installation complete!" -ForegroundColor Cyan
Write-Host "  ────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Commands available after restarting PowerShell:" -ForegroundColor White
Write-Host "    cs   - Interactive session picker (TUI)" -ForegroundColor White
Write-Host "    csl  - List all sessions (text mode)" -ForegroundColor White
Write-Host "`n  Or reload now:  . `$PROFILE`n" -ForegroundColor DarkGray
