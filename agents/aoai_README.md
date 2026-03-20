# Azure OpenAI Agent Series (aoai_s0x)

這個系列是將 Anthropic Claude 版本的 agent 教學（`s0x_*.py`）改寫為 Azure OpenAI GPT-4o 版本。

## 改寫原則

1. **改用 Azure OpenAI**：`AzureOpenAI` client、function calling 格式、OpenAI message 結構
2. **顯示過程**：SYSTEM prompt 要求模型先說明計畫再執行，每輪印出 `message.content`

## 環境設定

複製 `.env.template` 為 `.env` 並填入你的 Azure OpenAI 資訊：

```bash
cp .env.template .env
```

```env
AZURE_OPENAI_ENDPOINT=https://YOUR_RESOURCE_NAME.openai.azure.com/
AZURE_OPENAI_API_KEY=your_api_key_here
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
```

## 系列說明

### aoai_s01_agent_loop.py — Agent Loop

最基礎的 agent 模式，只有一個 `bash` 工具。

```
while True:
    response = LLM(messages, tools)
    if no tool_calls → break
    execute bash → append results → repeat
```

**Key insight**：整個 agent 的秘密就是這個 while loop，把 tool 結果不斷餵回給 LLM。

```bash
python aoai_s01_agent_loop.py
```

---

### aoai_s02_tool_use.py — 多工具 Dispatch

Loop 完全不變，新增工具並用 dispatch map 路由。

| 工具 | 功能 |
|------|------|
| `bash` | 執行 shell 指令 |
| `read_file` | 讀取檔案 |
| `write_file` | 寫入檔案 |
| `edit_file` | 精確取代檔案內文字 |

**Key insight**：「The loop didn't change at all. I just added tools.」

```bash
python aoai_s02_tool_use.py
```

---

### aoai_s03_todo_write.py — 任務追蹤

在 s02 基礎上加入 `TodoManager` 與 nag reminder，讓模型追蹤自己的進度。

```
TodoManager 狀態：
[ ] pending
[>] in_progress  ← 同時只能一個
[x] completed

若連續 3 輪未呼叫 todo → 注入 <reminder>Update your todos.</reminder>
```

**已知問題與修正：**

| 問題 | 原因 | 修正 |
|------|------|------|
| nag reminder 插在 tool results 前導致 `BadRequestError 400` | OpenAI API 要求 `tool_calls` 後必須緊跟 `role: tool` messages | reminder 改為在 tool results **之後** append |
| todo 清單每次更新變成 1/1 | GPT-4o 只傳當前 task，`TodoManager` 全取代導致清單被清空 | SYSTEM prompt 加 IMPORTANT：每次必須傳完整清單 |

**Key insight**：「The agent can track its own progress -- and I can see it.」

```bash
python aoai_s03_todo_write.py
```

---

### aoai_s04_subagent.py — Subagent + TodoManager

在 s03 基礎上加入 subagent 機制，讓 parent agent 可以派發子任務。

```
Parent agent                     Subagent
+------------------+             +------------------+
| messages=[...]   |             | messages=[]      |  ← fresh
|                  |  dispatch   |                  |
| tool: task       | ----------> | while tool_calls:|
|   prompt="..."   |             |   call tools     |
|   description="" |             |   append results |
|                  |  summary    |                  |
|   result = "..." | <---------- | return last text |
+------------------+             +------------------+
```

**Parent 工具集**：`bash` / `read_file` / `write_file` / `edit_file` / `task` / `todo`

**Subagent 工具集**：`bash` / `read_file` / `write_file` / `edit_file`（無 `task`、無 `todo`）

- Subagent 不能再派發子任務（防止遞迴）
- Subagent 不共用 parent 的 `TodoManager` 狀態
- Parent context 保持乾淨，subagent context 執行完即丟棄
- Subagent 輸出以藍色顯示，parent 以綠色顯示

**Key insight**：「Process isolation gives context isolation for free.」

```bash
python aoai_s04_subagent.py
```

---

## Anthropic vs Azure OpenAI 格式對照

| 面向 | Anthropic | Azure OpenAI |
|------|-----------|--------------|
| Tool schema | `input_schema: {...}` | `{"type": "function", "function": {"parameters": {...}}}` |
| 判斷 tool call | `response.stop_reason != "tool_use"` | `not response.choices[0].message.tool_calls` |
| 取得參數 | `block.input` | `json.loads(block.function.arguments)` |
| Tool result | `{"type": "tool_result", "tool_use_id": ..., "content": ...}` 同一 user message | `{"role": "tool", "tool_call_id": ..., "name": ..., "content": ...}` 獨立 message |
| 最後輸出 | `b.text for b in response.content if hasattr(b, "text")` | `response.choices[0].message.content` |
| System prompt | `system=SYSTEM` 獨立參數 | 放入 `messages` 第一筆 `{"role": "system", ...}` |
