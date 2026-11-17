"""Tests for cc-delegate/lib/cc-delegate-last-message.

Each test writes Claude Code transcripts and Codex session files into a temp
folder, then runs the script as a subprocess pointed at that folder.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "cc-delegate" / "lib" / "cc-delegate-last-message"
SEND_SCRIPT = REPO_ROOT / "cc-delegate" / "bin" / "cc-delegate-send"
NONCE = "CC_DELEGATE_DONE_0a1b2c3d4e5f"
OTHER_NONCE = "CC_DELEGATE_DONE_ffeeddccbbaa"


def instruction_appended_by_send(nonce: str) -> str:
    """The instruction line exactly as cc-delegate-send writes it, read from
    that script with its ${NONCE} filled in. The lookup recognises the prompt
    by this wording, so the two scripts must change together."""
    lines = [
        line
        for line in SEND_SCRIPT.read_text(encoding="utf-8").splitlines()
        if "${NONCE}" in line
    ]
    if len(lines) != 1:
        raise AssertionError(
            f"expected one line with ${{NONCE}} in {SEND_SCRIPT.name}, found {len(lines)}"
        )
    return lines[0].removesuffix('"').replace("${NONCE}", nonce)


def prompt_with(nonce: str, body: str = "Summarise the README.") -> str:
    return (
        f"{body}\n\n"
        "When you have finished answering, print exactly this string on its "
        f"own final line (nothing else on that line): {nonce}"
    )


def claude_prompt(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def claude_text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def claude_tool_call(command: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": command}}
            ]
        },
    }


def claude_tool_result(output: str) -> dict:
    return {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": output}]
        },
    }


def codex_environment_context() -> dict:
    """The first user row of every Codex chat: AGENTS.md and environment text,
    not something the user typed."""
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "<environment_context>...</environment_context>"}],
        },
    }


def codex_turn_start(turn_id: str, prompt: str) -> list[dict]:
    return [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": turn_id,
                "item": {
                    "type": "UserMessage",
                    "content": [{"type": "text", "text": prompt, "text_elements": []}],
                },
            },
        },
    ]


def codex_turn_end(turn_id: str, reply: str) -> list[dict]:
    return [
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": turn_id,
                "item": {
                    "type": "AgentMessage",
                    "phase": "final_answer",
                    "content": [{"type": "Text", "text": reply}],
                },
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": reply}],
            },
        },
        codex_task_complete(turn_id, reply),
    ]


def codex_task_complete(turn_id: str, last_agent_message: str | None) -> dict:
    return {
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": turn_id,
            "last_agent_message": last_agent_message,
        },
    }


def codex_finished_turn(turn_id: str, prompt: str, reply: str) -> list[dict]:
    return codex_turn_start(turn_id, prompt) + codex_turn_end(turn_id, reply)


def codex_command_output(turn_id: str, output: str) -> list[dict]:
    return [
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": turn_id,
                "item": {
                    "type": "CommandExecution",
                    "command": "cc-delegate-start-cc ...",
                    "aggregated_output": output,
                },
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "c1",
                "output": [{"type": "text", "text": output}],
            },
        },
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def set_modified_time(path: Path, seconds_ago: float) -> None:
    then = time.time() - seconds_ago
    os.utime(path, (then, then))


class LastMessageTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.claude_projects = self.root / "claude"
        self.codex_home = self.root / "codex"
        self.claude_projects.mkdir()
        self.codex_home.mkdir()

    def write_claude_transcript(self, rows: list[dict], name: str = "session.jsonl") -> Path:
        path = self.claude_projects / "-Users-someone-repo" / name
        write_jsonl(path, rows)
        return path

    def write_codex_session(
        self,
        rows: list[dict],
        name: str = "rollout-2026-11-17T10-00-00-abc.jsonl",
        codex_home: Path | None = None,
    ) -> Path:
        path = (codex_home or self.codex_home) / "sessions" / "2026" / "11" / "17" / name
        write_jsonl(path, rows)
        return path

    def run_last_message(
        self, nonce: str = NONCE, set_codex_home: bool = True
    ) -> subprocess.CompletedProcess:
        # HOME also points at the temp folder, so a default of ~/.codex or
        # ~/.claude can never reach a real folder.
        env = {**os.environ, "CODEX_HOME": str(self.codex_home), "HOME": str(self.root)}
        if not set_codex_home:
            del env["CODEX_HOME"]
        return subprocess.run(
            [sys.executable, str(SCRIPT), nonce, "--projects", str(self.claude_projects)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )


class CodexTests(LastMessageTestCase):
    def test_codex_reply_is_printed_without_the_nonce_line(self):
        self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn(
                "T1", prompt_with(NONCE), f"Here is the answer.\nIt has two lines.\n{NONCE}"
            )
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Here is the answer.\nIt has two lines.\n")

    def test_codex_reply_is_printed_when_the_turn_ends_without_the_nonce(self):
        self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", prompt_with(NONCE), "Here is the answer.")
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Here is the answer.\n")

    def test_codex_chat_still_working_on_the_prompt_is_reported_as_not_finished(self):
        self.write_codex_session(
            [codex_environment_context()] + codex_turn_start("T1", prompt_with(NONCE))
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_codex_reply_comes_from_the_turn_that_received_the_prompt(self):
        self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", "What is in this folder?", "Three files.")
            + codex_finished_turn("T2", prompt_with(NONCE), "The middle reply.")
            + codex_finished_turn("T3", "Thanks. Now list the tests.", "There are no tests.")
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "The middle reply.\n")

    def test_later_codex_reply_is_not_returned_for_a_prompt_whose_turn_never_finished(self):
        self.write_codex_session(
            [codex_environment_context()]
            + codex_turn_start("T1", prompt_with(NONCE))
            + codex_finished_turn("T2", "Never mind, just say hi.", "Hi.")
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_nonce_seen_only_in_codex_command_output_is_not_treated_as_a_prompt(self):
        self.write_codex_session(
            [codex_environment_context()]
            + codex_turn_start("T9", "Start a sub-session that summarises the README.")
            + codex_command_output("T9", f"workspace:1 surface:2\nnonce={NONCE}\n")
            + codex_turn_end("T9", "Started the sub-session.")
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_codex_turn_that_ends_with_no_message_prints_an_empty_reply(self):
        self.write_codex_session(
            [codex_environment_context()]
            + codex_turn_start("T1", prompt_with(NONCE))
            + [codex_task_complete("T1", None)]
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "\n")

    def test_codex_sessions_are_found_in_the_home_folder_when_codex_home_is_not_set(self):
        self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", prompt_with(NONCE), "Here is the answer."),
            codex_home=self.root / ".codex",
        )

        result = self.run_last_message(set_codex_home=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Here is the answer.\n")

    def test_codex_chat_older_than_the_age_limit_is_ignored(self):
        path = self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", prompt_with(NONCE), "Here is the answer.")
        )
        ten_days_ago = time.time() - 10 * 86400
        os.utime(path, (ten_days_ago, ten_days_ago))

        result = self.run_last_message()

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(result.stdout, "")


class ClaudeCodeTests(LastMessageTestCase):
    def test_claude_reply_is_printed_without_the_nonce_line(self):
        self.write_claude_transcript(
            [
                claude_prompt(prompt_with(NONCE)),
                claude_text("Let me read the README first."),
                claude_tool_call("cat README.md"),
                claude_tool_result("# claude-skills\n"),
                claude_text(f"The README is one heading.\n{NONCE}"),
            ]
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "The README is one heading.\n")

    def test_claude_chat_that_has_not_printed_the_nonce_is_reported_as_not_finished(self):
        self.write_claude_transcript(
            [
                claude_prompt(prompt_with(NONCE)),
                claude_text("Let me read the README first."),
            ]
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_nonce_seen_only_in_a_claude_tool_result_is_not_treated_as_a_prompt(self):
        self.write_claude_transcript(
            [
                claude_prompt("Start a sub-session that summarises the README."),
                claude_tool_call("cc-delegate-start-cc ..."),
                claude_tool_result(f"workspace:1 surface:2\nnonce={NONCE}\n"),
                claude_text("Started the sub-session."),
            ]
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(result.stdout, "")


class ClaudeAndCodexTests(LastMessageTestCase):
    def test_codex_reply_is_found_when_the_parent_chat_holds_the_nonce_in_a_tool_result(self):
        self.write_claude_transcript(
            [
                claude_prompt("Start a Codex sub-session that summarises the README."),
                claude_tool_call("cc-delegate-start-codex ..."),
                claude_tool_result(f"workspace:1 surface:2\nnonce={NONCE}\n"),
                claude_text("Started the sub-session."),
            ]
        )
        self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", prompt_with(NONCE), "The README is one heading.")
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "The README is one heading.\n")

    def test_unfinished_claude_chat_is_reported_even_when_a_codex_chat_finished_with_the_same_nonce(self):
        self.write_claude_transcript(
            [
                claude_prompt(prompt_with(NONCE)),
                claude_text("Let me read the README first."),
            ]
        )
        self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", prompt_with(NONCE), "The README is one heading.")
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")


class CallingChatTests(LastMessageTestCase):
    """The user may tell the calling chat the nonce once the sub-session
    prints it, which puts the bare nonce in a plain user message there."""

    def write_calling_chat(self) -> None:
        path = self.write_claude_transcript(
            [
                claude_prompt("Start a sub-session that summarises the README."),
                claude_tool_call("cc-delegate-start-cc ..."),
                claude_tool_result(f"workspace:1 surface:2\nnonce={NONCE}\n"),
                claude_text("Started the sub-session."),
                claude_prompt(f"done, it printed {NONCE}"),
            ],
            name="calling-chat.jsonl",
        )
        set_modified_time(path, seconds_ago=60)

    def test_nonce_the_user_typed_to_the_calling_chat_does_not_hide_a_claude_sub_session(self):
        sub_session = self.write_claude_transcript(
            [
                claude_prompt(prompt_with(NONCE)),
                claude_text(f"The README is one heading.\n{NONCE}"),
            ],
            name="sub-session.jsonl",
        )
        set_modified_time(sub_session, seconds_ago=120)
        self.write_calling_chat()

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "The README is one heading.\n")

    def test_nonce_the_user_typed_to_the_calling_chat_does_not_hide_a_codex_sub_session(self):
        sub_session = self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", prompt_with(NONCE), "The README is one heading.")
        )
        set_modified_time(sub_session, seconds_ago=120)
        self.write_calling_chat()

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "The README is one heading.\n")


class SendScriptWordingTests(LastMessageTestCase):
    def test_the_instruction_cc_delegate_send_appends_is_recognised_as_the_prompt(self):
        self.write_claude_transcript(
            [
                claude_prompt(f"Summarise the README.\n\n{instruction_appended_by_send(NONCE)}"),
                claude_text(f"The README is one heading.\n{NONCE}"),
            ]
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "The README is one heading.\n")


class NoChatTests(LastMessageTestCase):
    def test_unknown_nonce_reports_that_no_chat_carries_it(self):
        self.write_claude_transcript(
            [claude_prompt(prompt_with(OTHER_NONCE)), claude_text(f"Done.\n{OTHER_NONCE}")]
        )
        self.write_codex_session(
            [codex_environment_context()]
            + codex_finished_turn("T1", prompt_with(OTHER_NONCE), f"Done.\n{OTHER_NONCE}")
        )

        result = self.run_last_message()

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
