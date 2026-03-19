# Agent Client Protocol (ACP) Reference

This document summarizes the internal structure and behaviors of the Agent Client Protocol (ACP) as discovered during the development of this project.

## 🏗 RPC Structure

The protocol uses JSON-RPC 2.0. Key methods include:
- `session/prompt`: Sends a list of content blocks to the agent.
- `session/update`: (Notification) Sent from the agent to the client for streaming text, thoughts, plans, and tool status.
- `session/new`: Creates a new workspace session.

## 📦 Session Updates (`session/update`)

Session updates are sent as `SessionNotification` objects. The `update` field is a discriminated union based on the `session_update` key.

### Common Update Types

| `session_update` Type | Class | Description |
| :--- | :--- | :--- |
| `agent_message_chunk` | `AgentMessageChunk` | A chunk of the agent's final message to the user. |
| `agent_thought_chunk` | `AgentThoughtChunk` | A chunk of the agent's internal reasoning (Chain of Thought). |
| `tool_call` | `ToolCallStart` | Notification that the agent is starting a tool execution. |
| `tool_call_update` | `ToolCallProgress` | Progress updates for a running tool (e.g., status, edited files). |
| `plan` | `AgentPlanUpdate` | A complete list of planned tasks (`PlanEntry`). |
| `usage_update` | `UsageUpdate` | Token usage statistics. |

## 🧩 Content Blocks

Chunks (Message/Thought) and prompts use `ContentBlock` objects. Key types:
- `text`: Plain text string.
- `image`: Base64 `data` and `mimeType`. Used for photos.
- `audio`: Base64 `data` and `mimeType`. Used for voice messages.
- `resource`: Inline file content with `uri` and `text`.
- `resource_link`: Reference to a file with `uri` and `name`.

### 💡 Implementation Note: Robust Extraction
While the schema defines `content` as a specific block object, some agent implementations may send content as a dictionary or a raw string. A robust client should check for:
1. `obj.type` / `obj.get("type")` to identify the block kind.
2. `obj.text` (Attribute) or `obj.get("text")` (Dict key).
3. Fallbacks for non-text types (e.g. `🖼️ [Image: image/jpeg]`).

## 🔄 Streaming & Finality

- **Stream-First:** ACP is a streaming-first protocol.
- **Stop Reason:** The final `PromptResponse` indicates why the turn ended. Common reasons:
    - `end_turn`: Normal completion.
    - `max_tokens`: Context window full.
    - `cancelled`: Turn was aborted by client.
    - `refusal`: Agent refused to perform the task.

## 🛠 Tool Lifecycle

1. **`ToolCallStart`:** Agent requests to run a tool. Includes `tool_call_id`, `title`, and `kind`.
2. **Permission Check:** The client may respond with `AllowedOutcome` or `DeniedOutcome`.
3. **`ToolCallProgress`:** Agent sends updates with a `status`.
4. **`ToolCallUpdate`:** (Completion) Sent when the tool finishes, containing final `content`.

### 💡 Implementation Note: State Tracking
Different backends send tool details (like the shell command or the file path) at different stages. A robust client should **merge** updates for each `tool_call_id`:
- Store initial `kind` and `title` from `ToolCallStart`.
- Update `raw_input` and `status` from `ToolCallProgress`.
- Display the accumulated state during the permission request.

## 📋 Planning

The `AgentPlanUpdate` sends the entire plan every time it changes.
- `PlanEntry`: Contains `content` (description), `status` (e.g., `todo`, `done`, `failed`), and `priority`.
- The client should replace the entire local plan view with each update.

## ⚡ Transport & Stability

### 1. Buffer Limits
By default, Python's `asyncio.StreamReader` has a 64KB limit. Large tool outputs (like reading a 200KB file or a long shell log) will trigger a `LimitOverrunError`, crashing the connection. 
- **Fix:** Manually increase `proc.stdout._limit` to at least **1MB** when spawning the agent subprocess.

### 2. Timeouts
ACP does not define a protocol-level timeout for `session/prompt`. Agent turns can be long-running (minutes or even hours depending on the task). 
- **Strategy:** Avoid hard timeouts on prompts to allow the agent to finish complex tasks. Rely on manual cancellation (`session/cancel`) if the user decides the turn has hung.

### 3. Reliability & Health Checks
Connections can drop if the underlying process crashes or hits a fatal error.
- **Health Check:** Monitor `proc.returncode` and the connection's internal `_closed` state.
- **Auto-Recovery:** Detect dead sessions on the next message attempt and trigger a fresh `start_agent_service` automatically.
