#!/usr/bin/env python3
# Harness: context isolation -- Azure OpenAI GPT-4o version
"""
aoai_s04_subagent.py - Subagents (Azure OpenAI Version)

Spawn a child agent with fresh messages=[]. The child works in its own
context, sharing the filesystem, then returns only a summary to the parent.

    Parent agent                     Subagent
    +------------------+             +------------------+
    | messages=[...]   |             | messages=[]      |  <-- fresh
    |                  |  dispatch   |                  |
    | tool: task       | ---------->| while tool_calls: |
    |   prompt="..."   |            |   call tools     |
    |   description="" |            |   append results |
    |                  |  summary   |                  |
    |   result = "..." | <--------- | return last text |
    +------------------+             +------------------+
              |
    Parent context stays clean.
    Subagent context is discarded.

Key insight: "Process isolation gives context isolation for free."
"""

import os
import subprocess
import json
from pathlib import Path

from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

# Initialize Azure OpenAI client
client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
)

WORKDIR = Path.cwd()
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

SYSTEM = f"""You are a coding agent at {WORKDIR}. Use the task tool to delegate exploration or subtasks.
Use the todo tool to plan multi-step tasks. Mark in_progress before starting, completed when done.
IMPORTANT: Every time you call the todo tool, you MUST include ALL tasks in the list, not just the current one. Never pass a partial list — always pass the complete set of tasks with their latest statuses.
Before executing commands, briefly explain what you plan to do and why.
Think step by step and communicate your reasoning."""

SUBAGENT_SYSTEM = f"""You are a coding subagent at {WORKDIR}. Complete the given task, then summarize your findings.
Before executing commands, briefly explain what you plan to do and why.
Think step by step and communicate your reasoning."""


# -- Tool implementations shared by parent and child --
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# -- TodoManager: structured state the LLM writes to --
class TodoManager:
    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        if len(items) > 20:
            raise ValueError("Max 20 todos allowed")
        validated = []
        in_progress_count = 0
        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i + 1)))
            if not text:
                raise ValueError(f"Item {item_id}: text required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {item_id}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"id": item_id, "text": text, "status": status})
        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = []
        for item in self.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item["status"]]
            lines.append(f"{marker} #{item['id']}: {item['text']}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()

TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo":       lambda **kw: TODO.update(kw["items"]),
}

# Child gets all base tools except task (no recursive spawning)
CHILD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The bash command to execute"}},
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "limit": {"type": "integer", "description": "Optional line limit"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "old_text": {"type": "string", "description": "Text to replace"},
                    "new_text": {"type": "string", "description": "New text"}
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
]


# -- Subagent: fresh context, filtered tools, summary-only return --
def run_subagent(prompt: str) -> str:
    sub_messages = [{"role": "user", "content": prompt}]  # fresh context
    response = None
    for _ in range(30):  # safety limit
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SUBAGENT_SYSTEM}] + sub_messages,
            tools=CHILD_TOOLS,
            max_tokens=4000,
        )
        msg = response.choices[0].message
        sub_messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": msg.tool_calls
        })
        if msg.content:
            print(f"  \033[34m[subagent] {msg.content}\033[0m")
        if not msg.tool_calls:
            break
        # Append tool results (must immediately follow assistant tool_calls)
        for block in msg.tool_calls:
            function_args = json.loads(block.function.arguments)
            handler = TOOL_HANDLERS.get(block.function.name)
            try:
                output = handler(**function_args) if handler else f"Unknown tool: {block.function.name}"
            except Exception as e:
                output = f"Error: {e}"
            if block.function.name == "bash":
                print(f"  \033[33m[subagent] $ {function_args['command']}\033[0m")
            print(f"  [subagent] > {block.function.name}: {str(output)[:200]}")
            sub_messages.append({
                "role": "tool",
                "tool_call_id": block.id,
                "name": block.function.name,
                "content": str(output)[:50000]
            })
    # Only the final text returns to the parent -- child context is discarded
    if response:
        return response.choices[0].message.content or "(no summary)"
    return "(no summary)"


# -- Parent tools: base tools + task dispatcher + todo --
PARENT_TOOLS = CHILD_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Spawn a subagent with fresh context. It shares the filesystem but not conversation history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The task for the subagent to complete"},
                    "description": {"type": "string", "description": "Short description of the task"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Update task list. Track progress on multi-step tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Complete list of ALL todo items (never partial)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique identifier for the task"},
                                "text": {"type": "string", "description": "Task description"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Task status"
                                }
                            },
                            "required": ["id", "text", "status"]
                        }
                    }
                },
                "required": ["items"]
            }
        }
    },
]


def agent_loop(messages: list):
    rounds_since_todo = 0
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tools=PARENT_TOOLS,
            max_tokens=4000,
        )
        msg = response.choices[0].message
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": msg.tool_calls
        })
        if msg.content:
            print(f"\033[32m{msg.content}\033[0m")
            print()
        if not msg.tool_calls:
            # If there are incomplete todos, force one more round to update them
            incomplete = [t for t in TODO.items if t["status"] != "completed"]
            if incomplete:
                messages.append({
                    "role": "user",
                    "content": "<reminder>Update your todos — mark all completed tasks before finishing.</reminder>"
                })
                continue
            return
        # Execute each tool call, collect results (must follow assistant tool_calls immediately)
        results = []
        used_todo = False
        for block in msg.tool_calls:
            function_args = json.loads(block.function.arguments)
            if block.function.name == "task":
                desc = function_args.get("description", "subtask")
                print(f"> task ({desc}): {function_args['prompt'][:80]}")
                output = run_subagent(function_args["prompt"])
            else:
                if block.function.name == "bash":
                    print(f"\033[33m$ {function_args['command']}\033[0m")
                handler = TOOL_HANDLERS.get(block.function.name)
                try:
                    output = handler(**function_args) if handler else f"Unknown tool: {block.function.name}"
                except Exception as e:
                    output = f"Error: {e}"
            if block.function.name == "todo":
                print(f"> todo:\n{str(output)}")
                used_todo = True
            else:
                print(f"  {str(output)[:200]}")
            results.append({
                "role": "tool",
                "tool_call_id": block.id,
                "name": block.function.name,
                "content": str(output)
            })
        # Append tool results first (must immediately follow assistant tool_calls)
        for result in results:
            messages.append(result)
        # Nag reminder after tool results
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>"
            })


if __name__ == "__main__":
    history = []
    print("\033[32mAzure OpenAI GPT-4o Agent with Subagents\033[0m")
    print("Type 'q', 'exit', or press Ctrl+C to quit\n")

    while True:
        try:
            query = input("\033[36maoai-s04 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        print()
