---
name: cc-delegate
description: "Spawn a new cmux tab to either run a shell command directly (raw mode), or open a sub Claude Code or Codex CLI session with the given prompt, then read output when the user says it's done"
argument-hint: "<prompt for sub cc session, or shell command for raw mode>"
---

Spawn a sub Claude Code or Codex CLI session (or a raw shell command) in a new cmux surface (tab), paste the prompt, hit Enter. On a later turn — when the user reports completion — read its reply, present the output, offer to close the tab.

The prompt text is the skill's invocation argument.

## Key terms

- **`cc`.** Short for Claude Code in the command names below.
- **Scrollback.** The output history of the sub-session's tab — what the user would see by scrolling up in that tab. Read it via `cc-delegate-read` and `cc-delegate-read-close`.
- **Nonce.** A random marker string the `cc-delegate-start-cc*` and `cc-delegate-start-codex` scripts append to the prompt as an instruction: *"print this string on the final line when done."* Two uses:
  - The user glances at the tab, sees the nonce, and tells you the sub-session is done.
  - Or, when the user asks you to monitor, poll with `cc-delegate-read-wait*` — it returns once the sub-session has finished answering the prompt that carried the nonce.

  No nonce in raw mode or with `--no-nonce`.

## Commands

Scripts live in `~/.claude/skills/cc-delegate/bin/` (but you shouldn't need to read them — ask the user before doing so in the rare event you need to look at them).

### Phase 1 — Start

Pick one based on what the task needs:

- **`cc-delegate-start-cc`** — launches `claude` (per-tool approval). Pick when the task is execution-ready or needs only minor upfront reading/planning.
- **`cc-delegate-start-cc-plan`** — launches `claude --permission-mode plan`. Pick when the task requires research, file reading, or planning before execution.
- **`cc-delegate-start-codex`** — launches `codex` (Codex CLI). Pick when the user asks for a Codex sub-session.
- **`cc-delegate-start-raw`** — paste a raw shell command (no sub-cc, no nonce). Fire-and-forget. Do not follow up with `cc-delegate-send`.

Run **one** Bash call. Prompt goes in via stdin heredoc so quoting, `$`, backticks, and newlines survive:

```bash
~/.claude/skills/cc-delegate/bin/cc-delegate-start-cc [--cwd /path/to/dir] [--no-nonce] [--workspace <ref|id>] [--model <name>] <<'CC_DELEGATE_PROMPT_EOF'
<<<PASTE THE SKILL ARGUMENT HERE, VERBATIM>>>
CC_DELEGATE_PROMPT_EOF
```

On success, prints to stdout:

```
ws_ref=workspace:N
surface_ref=surface:M
nonce=CC_DELEGATE_DONE_<hex>     (omitted by cc-delegate-start-raw and with --no-nonce)
```

Capture the refs (and the nonce, if present) — needed for later phases. Report the refs back so the conversation retains them:

> Pasted into a new cmux tab (`workspace:3`, `surface:12`). Tell me when the sub-session finishes and I'll read the output.

Stop. Wait for the user's next message. Do not poll unless asked.

**`--no-nonce`** (all start commands and `cc-delegate-send`) — pass it when the prompt is a slash command (e.g. `/model Fable`); the appended nonce would stop it being a command. The prompt is pasted and submitted verbatim and no `nonce=` line prints — no completion signal, so `cc-delegate-read-wait*` won't work for that submission.

**`--workspace <ref|id>`** (all start commands) — open the tab in that workspace (`workspace:N`, or the UUID from `cmux workspace list --json`). Default is the shared `Claude_cc-delegate` workspace, created if missing and placed at the top of the `Claude_*` block at the bottom of the workspace list — so tabs never land in the caller's workspace or shift the Cmd+N numbering of the user's own workspaces. `--cwd` defaults to the delegating session's current directory.

**`--model <name>`** (`cc-delegate-start-cc`, `cc-delegate-start-cc-plan`, and `cc-delegate-start-codex`) — pass a model to the spawned `claude` or `codex` (e.g. `--model opus` for `claude`). Errors under `cc-delegate-start-raw`, which launches neither.

### Phase 2 — Read / close

Don't wait on a sub-session unless asked to monitor. Once the user reports completion (or asks you to monitor), get the reply with `cc-delegate-read-wait` or `cc-delegate-read-wait-close` and the nonce: it prints the whole final message, and returns at once when the sub-session has already finished. A Claude Code sub-session counts as finished only once its reply contains the nonce: if it stopped to ask something or to show a plan, use `cc-delegate-read` instead. `cc-delegate-read` and `cc-delegate-read-close` read the tab itself, which for a Claude Code or Codex sub-session is only the visible screen: use them in raw mode, when there is no nonce, or to see what a stopped sub-session is showing. Pick one:

- **`cc-delegate-read-wait <ws_ref> <surface_ref> <nonce>`** — block until the sub-session has printed the nonce (polls its transcript every 5s, times out at ~9 min with exit 2), then print ONLY its final message — the text after its last tool call, nonce line removed. Matched by the nonce in the pasted prompt, so no other chat can answer. Works for Claude Code and Codex sub-sessions; a Codex sub-session's message is printed as soon as its turn ends, even if it never printed the nonce. Leaves the tab open: use when a follow-up via `cc-delegate-send` is likely.
- **`cc-delegate-read-wait-close <ws_ref> <surface_ref> <nonce>`** — block on nonce, print the final message, then close. Use when done with the sub-session.
- **`cc-delegate-read <ws_ref> <surface_ref>`** — read scrollback once, leave tab open.
- **`cc-delegate-read-close <ws_ref> <surface_ref>`** — read scrollback, then close the tab.
- **`cc-delegate-close <ws_ref> <surface_ref>`** — close without reading. Use when the output was already read in a prior turn, or when tearing down without caring about output.

Don't echo the full buffer back — the user has usually already seen it, or asked you to monitor (which warrants a synthesis, not a replay). Summarize: highlight what matters, flag anything surprising or needing attention. Skip TUI chrome (borders, input box).

Ask before closing (unless the user has already said to close it):

> Close the tab?

### Phase 3 — Follow-up prompt (optional)

Push another prompt into the same sub-session:

```bash
~/.claude/skills/cc-delegate/bin/cc-delegate-send <ws_ref> <surface_ref> <<'CC_DELEGATE_PROMPT_EOF'
<<<PASTE THE NEW PROMPT HERE, VERBATIM>>>
CC_DELEGATE_PROMPT_EOF
```

Prints a new `nonce=...` (unless `--no-nonce`). Wait for the user to report completion, then use Phase 2 to read.

`cc-delegate-send` and `cc-delegate-read-wait` also work on a Claude Code or Codex tab the user started. Find its refs with `cmux tree --all`. Never use the `-close` commands on such a tab unless the user asks.

## Polling caveat

Prefer foreground Bash when running `cc-delegate-read-wait*` — `run_in_background: true` could surprise-interrupt the user if the nonce fires mid-conversation.

## Known limitations

- **Permission prompts in the sub-session** (y/n dialogs under `cc-delegate-start-cc`, approval prompts under `cc-delegate-start-codex`) require the user to answer in the tab. If polling, the nonce won't print until the prompt is cleared — expect the timeout unless the user jumps in.
- **Sends are paced.** The prompt goes in line by line, waiting for the tab to redraw after each send (~2 s per KB), because cmux delivers newlines as Enter keystrokes and the pty drops input when the target is busy. If a send fails with "target did not redraw", the sub-session is stalled or showing a dialog — look at the tab; the partial text sits in its input box, unsubmitted.
- **Tabs become four spaces** in prompts to a sub-session. Raw mode keeps tabs.
