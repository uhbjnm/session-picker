# Session Picker

TUI session manager for **Claude Code** and **Codex CLI**. Browse, search and resume previous sessions interactively.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

## Features

- Interactive TUI with keyboard navigation
- CJK-aware column alignment
- Search / filter sessions by message, project path or session ID
- Detail view with token usage statistics
- Auto `cd` to project directory and resume session
- Works in PowerShell, bash and zsh

## Quick Install (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File install-cs.ps1
```

The installer will:
1. Check Python 3.10+ (prompt for custom path if not found)
2. Install `claude-sessions.py` to `~/.claude/scripts/`
3. Add `cs` / `csl` functions to your PowerShell profile

## Manual Setup

### Claude Code

```bash
# 1. Copy script
mkdir -p ~/.claude/scripts
cp claude-sessions.py ~/.claude/scripts/

# 2. Add to shell profile (bash/zsh)
echo 'alias cs="python ~/.claude/scripts/claude-sessions.py"' >> ~/.bashrc
```

### Codex CLI

```bash
mkdir -p ~/.codex/scripts
cp codex-sessions.py ~/.codex/scripts/

# Add alias
echo 'alias codexs="python ~/.codex/scripts/codex-sessions.py"' >> ~/.bashrc
```

## Usage

| Command | Description |
|---------|-------------|
| `cs` | Interactive TUI - select and resume a Claude Code session |
| `csl` | List all Claude Code sessions (non-interactive) |
| `codexs` | Interactive TUI - select and resume a Codex session |
| `codexsl` | List all Codex sessions (non-interactive) |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑` / `↓` / `j` / `k` | Navigate |
| `Enter` | Resume selected session |
| `/` | Search / filter |
| `d` | Detail view |
| `PgUp` / `PgDn` | Page scroll |
| `Home` / `End` | Jump to first / last |
| `q` / `Esc` | Quit |

### Options

```
python claude-sessions.py [--project <path>] [--limit <n>] [--list] [--select]
```

| Flag | Description |
|------|-------------|
| `--project`, `-p` | Filter by project path |
| `--limit`, `-l` | Max sessions to load (default: 200) |
| `--list` | Non-interactive text list |
| `--select`, `-s` | Output mode for shell wrappers (TUI on stderr, result on stdout) |

## Requirements

- Python 3.10+
- Claude Code CLI (`claude`) and/or Codex CLI (`codex`)

## License

MIT
