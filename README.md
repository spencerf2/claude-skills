# claude-skills

Shared [Claude Code](https://docs.claude.com/en/docs/claude-code) skills.

## Skills

### `cc-delegate`

Spawn a sub Claude Code session in a new [cmux](https://github.com/manaflow-ai/cmux) tab, paste a prompt, hit Enter, and — when you say it's done — read the output. Also supports raw shell commands (fire-and-forget) via `--raw`. The sub-session can also be a Codex CLI session.

See [`cc-delegate/SKILL.md`](cc-delegate/SKILL.md) for the full skill definition.

**Requirements:** `cmux`, `openssl`, `awk`, Python 3.10 or newer, and a `cc` alias for `claude` in your shell.

## Install

Pick whichever you prefer. Claude Code looks for skills at `~/.claude/skills/<skill-name>/`.

### Option A: symlink (recommended — `git pull` to update)

```bash
git clone git@github.com:spencerf2/claude-skills.git ~/repos/claude-skills
ln -s ~/repos/claude-skills/cc-delegate ~/.claude/skills/cc-delegate
```

### Option B: copy

```bash
git clone git@github.com:spencerf2/claude-skills.git /tmp/claude-skills
cp -r /tmp/claude-skills/cc-delegate ~/.claude/skills/cc-delegate
```
