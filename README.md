# Session Picker

[English](#english) | [中文](#中文)

TUI session manager for **Claude Code** and **Codex CLI**.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

---

## English

Interactive terminal UI to browse, search and resume previous **Claude Code** / **Codex** sessions.

### Features

- Interactive TUI with keyboard navigation (arrow keys, vim-style j/k)
- CJK-aware column alignment — Chinese, Japanese, Korean characters display correctly
- Search & filter by message content, project path, or session ID
- Detail view with token usage statistics
- Auto `cd` to project directory before resuming
- Supports both **Claude Code** and **Codex CLI**
- Works in PowerShell, bash, and zsh

### Quick Install (PowerShell)

```powershell
git clone https://github.com/uhbjnm/session-picker.git
cd session-picker
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer will:

1. Detect Python 3.10+ (prompt for custom path if not found)
2. Install `claude-sessions.py` to `~/.claude/scripts/`
3. Install `codex-sessions.py` to `~/.codex/scripts/`
4. Add `cs` / `csl` / `codexs` / `codexsl` functions to your PowerShell profile

### Manual Setup

**Claude Code:**

```bash
mkdir -p ~/.claude/scripts
cp claude-sessions.py ~/.claude/scripts/
alias cs='python ~/.claude/scripts/claude-sessions.py'
alias csl='python ~/.claude/scripts/claude-sessions.py --list'
```

**Codex:**

```bash
mkdir -p ~/.codex/scripts
cp codex-sessions.py ~/.codex/scripts/
alias codexs='python ~/.codex/scripts/codex-sessions.py'
alias codexsl='python ~/.codex/scripts/codex-sessions.py --list'
```

### Commands

| Command | Description |
|---------|-------------|
| `cs` | Interactive TUI — select and resume a Claude Code session |
| `csl` | List all Claude Code sessions (text mode) |
| `codexs` | Interactive TUI — select and resume a Codex session |
| `codexsl` | List all Codex sessions (text mode) |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑` `↓` `j` `k` | Navigate up / down |
| `Enter` | Resume selected session |
| `/` | Search / filter |
| `Esc` | Exit search or quit |
| `d` | Toggle detail view |
| `PgUp` `PgDn` | Page scroll |
| `Home` `End` | Jump to first / last |
| `q` | Quit |

### CLI Options

```
python claude-sessions.py [OPTIONS]
python codex-sessions.py  [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `-p`, `--project` | Filter sessions by project path |
| `-l`, `--limit` | Max sessions to load (default: 200) |
| `--list` | Non-interactive text list |
| `-s`, `--select` | Shell wrapper mode (TUI on stderr, result on stdout) |

### Requirements

- Python 3.10+
- Claude Code CLI (`claude`) and/or Codex CLI (`codex`)

---

## 中文

交互式终端 UI，用于浏览、搜索和恢复 **Claude Code** / **Codex** 的历史会话。

### 功能特性

- 交互式 TUI，支持方向键和 vim 风格 j/k 导航
- CJK 感知的列对齐 — 中日韩字符正确显示
- 按消息内容、项目路径或会话 ID 搜索过滤
- 详情视图：Token 用量、持续时长等统计信息
- 恢复会话前自动 `cd` 到对应项目目录
- 同时支持 **Claude Code** 和 **Codex CLI**
- 兼容 PowerShell、bash、zsh

### 快速安装（PowerShell）

```powershell
git clone https://github.com/uhbjnm/session-picker.git
cd session-picker
powershell -ExecutionPolicy Bypass -File install.ps1
```

安装脚本会自动：

1. 检测 Python 3.10+（未找到时提示手动指定路径）
2. 安装 `claude-sessions.py` 到 `~/.claude/scripts/`
3. 安装 `codex-sessions.py` 到 `~/.codex/scripts/`
4. 向 PowerShell Profile 添加 `cs` / `csl` / `codexs` / `codexsl` 函数

### 手动安装

**Claude Code：**

```bash
mkdir -p ~/.claude/scripts
cp claude-sessions.py ~/.claude/scripts/
alias cs='python ~/.claude/scripts/claude-sessions.py'
alias csl='python ~/.claude/scripts/claude-sessions.py --list'
```

**Codex：**

```bash
mkdir -p ~/.codex/scripts
cp codex-sessions.py ~/.codex/scripts/
alias codexs='python ~/.codex/scripts/codex-sessions.py'
alias codexsl='python ~/.codex/scripts/codex-sessions.py --list'
```

### 命令一览

| 命令 | 说明 |
|------|------|
| `cs` | 交互式 TUI — 选择并恢复 Claude Code 会话 |
| `csl` | 列出所有 Claude Code 会话（文本模式） |
| `codexs` | 交互式 TUI — 选择并恢复 Codex 会话 |
| `codexsl` | 列出所有 Codex 会话（文本模式） |

### 快捷键

| 按键 | 功能 |
|------|------|
| `↑` `↓` `j` `k` | 上下选择 |
| `Enter` | 恢复选中会话 |
| `/` | 搜索过滤 |
| `Esc` | 退出搜索 / 退出程序 |
| `d` | 切换详情视图 |
| `PgUp` `PgDn` | 翻页 |
| `Home` `End` | 跳到首项 / 末项 |
| `q` | 退出 |

### 命令行参数

```
python claude-sessions.py [选项]
python codex-sessions.py  [选项]
```

| 参数 | 说明 |
|------|------|
| `-p`, `--project` | 按项目路径过滤 |
| `-l`, `--limit` | 最大加载会话数（默认 200） |
| `--list` | 非交互式文本列表 |
| `-s`, `--select` | Shell 包装模式（TUI 输出到 stderr，结果输出到 stdout） |

### 环境要求

- Python 3.10+
- Claude Code CLI (`claude`) 和/或 Codex CLI (`codex`)

## License

MIT
