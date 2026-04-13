#!/usr/bin/env python3
# Harness: PDF text extraction -- Azure OpenAI GPT-4o version
"""
aoai_s06_context_compact_pdf_improvement.py
  - Compact + Skills + Subagents + TodoWrite + PDF Text Extraction (Azure OpenAI Version)

PDF reading strategy (inspired by claw-code text extraction approach):

    LLM acts like a reader who can flip pages:
    1. get_pdf_info("doc.pdf")        → know total pages, decide strategy
    2. read_pdf_pages("doc.pdf", "1-3")  → read table of contents as text
    3. read_pdf_pages("doc.pdf", "42-55") → jump to relevant section
    4. repeat until question can be answered

    Uses pdfplumber for text extraction — no images, no base64, no vision tokens.
    Works for PDFs with a text layer (not scanned image-only PDFs).

Prerequisites:
    pip install pdfplumber

Key insight: "Text is small. Let the LLM decide which pages to read."
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from openai import AzureOpenAI
from dotenv import load_dotenv

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

load_dotenv(override=True)

client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
)

WORKDIR = Path.cwd()
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
SKILLS_DIR = WORKDIR / "skills"

THRESHOLD = 50000
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
KEEP_RECENT = 3


# ── SkillLoader ────────────────────────────────────────────────────────────────
class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = {}
        self._load_all()

    def _load_all(self):
        if not self.skills_dir.exists():
            return
        for f in sorted(self.skills_dir.rglob("SKILL.md")):
            text = f.read_text()
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body}

    def _parse_frontmatter(self, text: str) -> tuple:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        import yaml
        try:
            meta = yaml.safe_load(match.group(1)) or {}
            if not isinstance(meta, dict):
                meta = {}
        except yaml.YAMLError:
            meta = {}
        return meta, match.group(2).strip()

    def get_descriptions(self) -> str:
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "No description")
            tags = skill["meta"].get("tags", "")
            line = f"  - {name}: {desc}"
            if tags:
                line += f" [{tags}]"
            lines.append(line)
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"


# ── TodoManager ────────────────────────────────────────────────────────────────
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


SKILL_LOADER = SkillLoader(SKILLS_DIR)
TODO = TodoManager()

SYSTEM = f"""You are a coding agent at {WORKDIR}.

PDF reading strategy:
  1. Call get_pdf_info first to know total pages.
  2. Call read_pdf_pages with a small range (e.g. "1-5") to see table of contents.
  3. Jump directly to relevant sections — do NOT read every page sequentially.
  4. Repeat read_pdf_pages on specific pages as needed until you can answer.
  Note: read_pdf_pages returns extracted text, not images.

PAGE OFFSET WARNING (critical):
  After reading any page, ALWAYS check the printed page number in the header or footer
  of the returned text. It may differ from the physical page number you requested.
  Example: you request page 151, but the footer shows "- 141 -" or "141".
  This means offset = 151 - 141 = 10. To reach printed page N, use physical page N + offset.
  If you detect a mismatch, call load_skill("pdf-page-offset") for the full procedure.
  DO NOT assume the page you read is the printed page you wanted — always verify.

Use the todo tool to plan multi-step tasks. Mark in_progress before starting, completed when done.
IMPORTANT: Every time you call the todo tool, you MUST include ALL tasks in the list, not just the current one.
Use load_skill to access specialized knowledge before tackling unfamiliar topics.
Use the task tool to delegate subtasks to subagents with fresh context.
Before executing commands, briefly explain what you plan to do and why.
Think step by step and communicate your reasoning.

Skills available:
{SKILL_LOADER.get_descriptions()}"""

SUBAGENT_SYSTEM = f"""You are a coding subagent at {WORKDIR}. Complete the given task, then summarize your findings.
Before executing commands, briefly explain what you plan to do and why.
Think step by step and communicate your reasoning."""


# ── Context compression ─────────────────────────────────────────────────────────
def estimate_tokens(messages: list) -> int:
    """Estimate tokens from text content only."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", "")) // 4
    return total


def micro_compact(messages: list) -> list:
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= KEEP_RECENT:
        return messages
    to_clear = tool_indices[:-KEEP_RECENT]
    cleared = 0
    for idx in to_clear:
        msg = messages[idx]
        if isinstance(msg.get("content"), str) and len(msg["content"]) > 100:
            messages[idx]["content"] = f"[Previous: used {msg.get('name', 'unknown')}]"
            cleared += 1
    if cleared:
        print(f"[micro_compact: cleared {cleared} old tool results]")
    return messages


def auto_compact(messages: list) -> list:
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    print(f"[transcript saved: {transcript_path}]")
    conversation_text = json.dumps(messages, default=str)[:80000]
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content":
            "Summarize this conversation for continuity. Include: "
            "1) What was accomplished, 2) Current state, 3) Key decisions made, "
            "4) Which PDF pages were read and what was found, including exact file paths. "
            "Be concise but preserve critical details.\n\n" + conversation_text}],
        max_tokens=2000,
    )
    summary = response.choices[0].message.content
    return [
        {"role": "user", "content": f"[Conversation compressed. Transcript: {transcript_path}]\n\n{summary}"},
        {"role": "assistant", "content": "Understood. I have the context from the summary. Continuing."},
    ]


# ── PDF tools ───────────────────────────────────────────────────────────────────
def parse_pages(pages_str: str) -> list[int]:
    """Parse '1-5', '1,3,5', '1-3,10,15-20' → sorted list of ints."""
    result = []
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            result.extend(range(int(start), int(end) + 1))
        elif part:
            result.append(int(part))
    return sorted(set(result))


def run_get_pdf_info(path: str) -> str:
    if not PDFPLUMBER_AVAILABLE:
        return "Error: pdfplumber not installed. Run: pip install pdfplumber"
    fp = safe_path(path)
    if not fp.exists():
        return f"Error: File not found: {path}"
    try:
        with pdfplumber.open(fp) as pdf:
            pages = len(pdf.pages)
        size_mb = fp.stat().st_size / (1024 * 1024)
        return f"pages: {pages}, size: {size_mb:.1f} MB"
    except Exception as e:
        return f"Error reading PDF: {e}"


def run_read_pdf_pages(path: str, pages: str) -> str:
    if not PDFPLUMBER_AVAILABLE:
        return "Error: pdfplumber not installed. Run: pip install pdfplumber"
    fp = safe_path(path)
    if not fp.exists():
        return f"Error: File not found: {path}"

    page_list = parse_pages(pages)
    if not page_list:
        return "Error: No valid pages specified."
    if len(page_list) > 30:
        return "Error: Max 30 pages per call."

    try:
        results = []
        with pdfplumber.open(fp) as pdf:
            total = len(pdf.pages)
            for page_num in page_list:
                if page_num < 1 or page_num > total:
                    results.append(f"[Page {page_num}: out of range (total: {total})]")
                    continue
                page = pdf.pages[page_num - 1]
                text = page.extract_text() or ""
                if text.strip():
                    results.append(f"--- Page {page_num} ---\n{text.strip()}")
                else:
                    results.append(f"--- Page {page_num} ---\n[No extractable text on this page]")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error reading PDF pages: {e}"


# ── Filesystem tools ─────────────────────────────────────────────────────────────
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


TOOL_HANDLERS = {
    "bash":             lambda **kw: run_bash(kw["command"]),
    "read_file":        lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":        lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo":             lambda **kw: TODO.update(kw["items"]),
    "load_skill":       lambda **kw: SKILL_LOADER.get_content(kw["name"]),
    "get_pdf_info":     lambda **kw: run_get_pdf_info(kw["path"]),
    "read_pdf_pages":   lambda **kw: run_read_pdf_pages(kw["path"], kw["pages"]),
    "compact":          lambda **kw: "Manual compression requested.",
}

# ── Tool definitions ─────────────────────────────────────────────────────────────
CHILD_TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Run a shell command.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read file contents.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write content to file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}
    }},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace exact text in file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}
    }},
    {"type": "function", "function": {
        "name": "load_skill", "description": "Load specialized knowledge by name.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    }},
    {"type": "function", "function": {
        "name": "get_pdf_info",
        "description": "Get PDF metadata: total page count and file size. Call this first before reading any PDF.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the PDF file"}
        }, "required": ["path"]}
    }},
    {"type": "function", "function": {
        "name": "read_pdf_pages",
        "description": (
            "Extract text from PDF pages. Returns the text content of specified pages. "
            "Supports ranges like '1-5', lists like '1,3,10', or single pages like '42'. "
            "Max 30 pages per call. Call get_pdf_info first to know total pages. "
            "Note: only works for PDFs with a text layer (not scanned image-only PDFs)."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the PDF file"},
            "pages": {"type": "string", "description": "Pages to read, e.g. '1-5', '1,3,10', '42'"}
        }, "required": ["path", "pages"]}
    }},
]

PARENT_TOOLS = CHILD_TOOLS + [
    {"type": "function", "function": {
        "name": "task",
        "description": "Spawn a subagent with fresh context. Shares filesystem but not conversation history.",
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string"},
            "description": {"type": "string"}
        }, "required": ["prompt"]}
    }},
    {"type": "function", "function": {
        "name": "todo",
        "description": "Update task list. Track progress on multi-step tasks.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "description": "Complete list of ALL todo items (never partial)",
                "items": {"type": "object", "properties": {
                    "id": {"type": "string"}, "text": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}
                }, "required": ["id", "text", "status"]}
            }
        }, "required": ["items"]}
    }},
    {"type": "function", "function": {
        "name": "compact",
        "description": "Trigger manual conversation compression.",
        "parameters": {"type": "object", "properties": {"focus": {"type": "string"}}}
    }},
]


# ── Subagent ─────────────────────────────────────────────────────────────────────
def run_subagent(prompt: str) -> str:
    sub_messages = [{"role": "user", "content": prompt}]
    response = None
    for _ in range(30):
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SUBAGENT_SYSTEM}] + sub_messages,
            tools=CHILD_TOOLS,
            max_tokens=4000,
        )
        msg = response.choices[0].message
        sub_messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})
        if msg.content:
            print(f"  \033[34m[subagent] {msg.content}\033[0m")
        if not msg.tool_calls:
            break
        for block in msg.tool_calls:
            function_args = json.loads(block.function.arguments)
            if block.function.name == "bash":
                print(f"  \033[33m[subagent] $ {function_args['command']}\033[0m")
            handler = TOOL_HANDLERS.get(block.function.name)
            try:
                output = handler(**function_args) if handler else f"Unknown tool: {block.function.name}"
            except Exception as e:
                output = f"Error: {e}"
            print(f"  [subagent] > {block.function.name}: {str(output)[:200]}")
            sub_messages.append({
                "role": "tool", "tool_call_id": block.id,
                "name": block.function.name, "content": str(output)[:50000]
            })
    if response:
        return response.choices[0].message.content or "(no summary)"
    return "(no summary)"


# ── Agent loop ───────────────────────────────────────────────────────────────────
def agent_loop(messages: list):
    rounds_since_todo = 0
    while True:
        micro_compact(messages)
        if estimate_tokens(messages) > THRESHOLD:
            print("[auto_compact triggered]")
            messages[:] = auto_compact(messages)

        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tools=PARENT_TOOLS,
            max_tokens=4000,
        )
        msg = response.choices[0].message
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})
        if msg.content:
            print(f"\033[32m{msg.content}\033[0m")
            print()

        if not msg.tool_calls:
            incomplete = [t for t in TODO.items if t["status"] != "completed"]
            if incomplete:
                messages.append({
                    "role": "user",
                    "content": "<reminder>Update your todos — mark all completed tasks before finishing.</reminder>"
                })
                continue
            return

        results = []
        used_todo = False
        manual_compact = False

        for block in msg.tool_calls:
            function_args = json.loads(block.function.arguments)

            if block.function.name == "task":
                desc = function_args.get("description", "subtask")
                print(f"> task ({desc}): {function_args['prompt'][:80]}")
                output = run_subagent(function_args["prompt"])
            elif block.function.name == "compact":
                manual_compact = True
                output = "Compressing..."
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
            elif block.function.name == "read_pdf_pages":
                pages_arg = function_args.get("pages", "")
                preview = str(output)[:200]
                print(f"> read_pdf_pages ({pages_arg}): {preview}...")
            else:
                print(f"> {block.function.name}: {str(output)[:200]}")

            results.append({
                "role": "tool", "tool_call_id": block.id,
                "name": block.function.name, "content": str(output)[:50000]
            })

        # Append tool results (must immediately follow assistant tool_calls)
        for result in results:
            messages.append(result)

        # Nag reminder after tool results
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            messages.append({"role": "user", "content": "<reminder>Update your todos.</reminder>"})

        # Layer 3: manual compact (after everything)
        if manual_compact:
            print("[manual compact]")
            messages[:] = auto_compact(messages)


if __name__ == "__main__":
    if not PDFPLUMBER_AVAILABLE:
        print("\033[33m警告：pdfplumber 未安裝。請執行：pip install pdfplumber\033[0m")
    history = []
    print("\033[32mAzure OpenAI GPT-4o — PDF Text Extraction Agent\033[0m")
    print("Features: PDF text extraction, skills, subagents, todo, context compaction")
    print("Requires: pip install pdfplumber")
    print("Type 'q', 'exit', or press Ctrl+C to quit\n")

    while True:
        try:
            query = input("\033[36maoai-pdf >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        print()
