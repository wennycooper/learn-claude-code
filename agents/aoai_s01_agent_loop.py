#!/usr/bin/env python3
# Harness: the loop -- Azure OpenAI GPT-4o version
"""
aoai_s01_agent_loop.py - The Agent Loop (Azure OpenAI Version)

The entire secret of an AI coding agent in one pattern:

    while stop_reason == "tool_calls":
        response = LLM(messages, tools)
        execute tools
        append results

    +----------+      +-------+      +---------+
    |   User   | ---> |  LLM  | ---> |  Tool   |
    |  prompt  |      |       |      | execute |
    +----------+      +---+---+      +----+----+
                          ^               |
                          |   tool_result |
                          +---------------+
                          (loop continues)

This is the core loop: feed tool results back to the model
until the model decides to stop. Production agents layer
policy, hooks, and lifecycle controls on top.
"""

import os
import subprocess
import json

from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

# Initialize Azure OpenAI client
client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
)

MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

SYSTEM = f"""You are a coding agent at {os.getcwd()}. Use bash to solve tasks.
Before executing commands, briefly explain what you plan to do and why.
Think step by step and communicate your reasoning."""

# Azure OpenAI uses the same function calling format as OpenAI
TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute"
                }
            },
            "required": ["command"],
        },
    }
}]


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


# -- The core pattern: a while loop that calls tools until the model stops --
def agent_loop(messages: list):
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tools=TOOLS,
            max_tokens=4000,
        )
        # Append assistant turn
        messages.append({
            "role": "assistant",
            "content": response.choices[0].message.content,
            "tool_calls": response.choices[0].message.tool_calls
        })
        # Print assistant's explanation if present
        if response.choices[0].message.content:
            print("===== message.content =====")
            print(f"\033[32m{response.choices[0].message.content}\033[0m")
            print("===========================")
        # If the model didn't call a tool, we're done
        if not response.choices[0].message.tool_calls:
            return
        # Execute each tool call, collect results
        results = []
        for block in response.choices[0].message.tool_calls:
            function_args = json.loads(block.function.arguments)
            print(f"\033[33m$ {function_args['command']}\033[0m")
            output = run_bash(function_args["command"])
            print(output[:200])
            results.append({
                "role": "tool",
                "tool_call_id": block.id,
                "name": block.function.name,
                "content": output
            })
        # Append all results as separate messages
        for result in results:
            messages.append(result)


if __name__ == "__main__":
    history = []
    print("\033[32mAzure OpenAI GPT-4o Agent Loop\033[0m")
    print("Type 'q', 'exit', or press Ctrl+C to quit\n")

    while True:
        try:
            query = input("\033[36maoai >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append({"role": "user", "content": query})
        agent_loop(history)
        print()
