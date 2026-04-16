---
name: cc-delegate
description: "Spawn a new cmux tab to either run a shell command directly (raw mode), or open a sub Claude Code session with the given prompt, then read output when the user says it's done"
argument-hint: "<prompt for sub cc session, or shell command for raw mode>"
---

Spawn a sub Claude Code session in a new cmux surface (tab) via the user's `cc` alias, paste the user's prompt, hit Enter, then — on a later turn, when the user says the sub Claude Code session is done — read the scrollback, present the output, and offer to close the tab.

The prompt text is the skill's invocation argument.

Phase 1 and Phase 2 are each a single Bash call, wrapping cmux orchestration in the three scripts at `~/.claude/skills/cc-delegate/bin/`:

- `cc-delegate-start` — opens tab, launches cc, pastes prompt, hits Enter
- `cc-delegate-read`  — reads scrollback; optional `--wait-for NONCE` poll and `--close`
- `cc-delegate-send`  — pastes a follow-up prompt into an existing sub-session

## Phase 1 — Open tab, start the sub Claude Code session, paste prompt (run on skill invocation)

Run **one** Bash call. The prompt goes in via stdin heredoc so quoting, `$`, backticks, and newlines don't break:

```bash
~/.claude/skills/cc-delegate/bin/cc-delegate-start [--cwd /path/to/dir] <<'CC_DELEGATE_PROMPT_EOF'
<<<PASTE THE SKILL ARGUMENT HERE, VERBATIM>>>
CC_DELEGATE_PROMPT_EOF
```

The script:
1. Sanity-checks cmux is running (fails loudly if not).
2. Finds the currently-selected workspace.
3. Creates a new surface (tab) in that workspace.
4. Optionally `cd`s into `--cwd` if passed (with a 2-second settle).
5. Launches `cc` and waits 3 seconds for the TUI.
6. Generates a random nonce `CC_DELEGATE_DONE_<hex>` and appends instructions to the prompt: *"When you have finished answering, print exactly this string on its own final line: `<NONCE>`"*.
7. Pastes the combined prompt and hits Enter (with `return` fallback).
8. Prints three lines to stdout:
   ```
   ws_ref=workspace:N
   surface_ref=surface:M
   nonce=CC_DELEGATE_DONE_<hex>
   ```

**Capture all three values** from the Bash tool output — they're needed for Phase 2.

**Report back to the user.** Include the refs (and mention the nonce exists) so the conversation context retains them. Example:

> Pasted the prompt into a new cmux tab (`workspace: workspace:3`, `surface: surface:12`). Tell me when the sub Claude Code session finishes and I'll read the output.

Do not poll by default. Stop and wait for the user's next message.

For raw shell commands (no `cc`), pass `--raw` to `cc-delegate-start` — see the script header for details and caveats.

## Phase 2 — Read output when user says the sub Claude Code session is done

Triggered when the user reports completion ("it's done", "cc finished", "ok read the output"). Use the `ws_ref`, `surface_ref` from Phase 1.

**Default (human-triggered read).** One Bash call:

```bash
~/.claude/skills/cc-delegate/bin/cc-delegate-read <ws_ref> <surface_ref>
```

Extract the relevant section from the printed scrollback — typically the last assistant message. Skip TUI chrome (box-drawing, input box). If long, summarize rather than dumping the whole buffer.

**Asking about closing the tab.** Ask:

> Close the tab?

If yes, and you have **not yet read** the scrollback — one call does read + close together:

```bash
~/.claude/skills/cc-delegate/bin/cc-delegate-read <ws_ref> <surface_ref> --close
```

If yes, and you **already read** the scrollback — close directly without re-reading (avoids re-dumping a long scrollback into your context). Send Ctrl+C twice to gracefully exit cc before destroying the tab:

```bash
cmux send-key --workspace <ws_ref> --surface <surface_ref> ctrl+c
sleep 1
cmux send-key --workspace <ws_ref> --surface <surface_ref> ctrl+c
sleep 1
cmux close-surface --workspace <ws_ref> --surface <surface_ref>
```

## Phase 3 — Follow-up prompt (optional, on user request)

If the user wants to push another prompt into the same sub-session:

```bash
~/.claude/skills/cc-delegate/bin/cc-delegate-send <ws_ref> <surface_ref> <<'CC_DELEGATE_PROMPT_EOF'
<<<PASTE THE NEW PROMPT HERE, VERBATIM>>>
CC_DELEGATE_PROMPT_EOF
```

Prints a new `nonce=...` on stdout. Wait for the user to say it's done, then use Phase 2 to read.

## Opt-in: wait-for-completion mode

Only use this when the user explicitly asks you to block until the sub-session finishes (e.g. "kick it off and wait for it"). Default is human-in-the-loop.

```bash
~/.claude/skills/cc-delegate/bin/cc-delegate-read <ws_ref> <surface_ref> --wait-for <nonce>
```

Polls every 5 seconds for the nonce to appear ≥2 times in the scrollback (once from the pasted prompt, once from sub-cc's reply). Times out at ~9 minutes (just under the Bash tool's 10-min cap) with exit code 2 and prints whatever scrollback exists so far. **Prefer foreground Bash for this** — `run_in_background: true` could surprise-interrupt the user if the nonce fires while they're mid-conversation with you.

## Known limitations

- **Permission prompts in the sub-session** (y/n dialogs) will hang it forever; the nonce never prints. If using `--wait-for`, expect the 9-minute timeout. Default human-in-the-loop mode avoids this — the user can jump into the tab and answer the prompt themselves.
- **Embedded newlines in a pasted prompt** may submit prematurely if `cmux send` doesn't trigger the TUI's bracketed-paste handling. If this breaks a specific prompt, the workaround is the same as before: strip/replace `\n` before piping in, or split into multiple `cc-delegate-send` calls.
- **Refs live only in this conversation's context.** If the conversation is compacted aggressively, Phase 2 may lose them — re-derive from `cmux list-workspaces` / `cmux list-pane-surfaces` if needed, or ask the user.
- **Now script-handled** (previously fragile or manual): sanity check that cmux is up, parsing workspace/surface refs from cmux output, quoting of prompts with `$` / backticks / quotes (stdin), Enter → Return fallback.
