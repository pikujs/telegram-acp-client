# ACP Backend Variations

This document tracks known variations and implementation details for different Agent Client Protocol (ACP) backends. While the protocol defines strict standards, different implementations (like `gemini-cli` or `opencode`) may use varying field names or structures, especially in `raw_input` or early protocol versions.

## 1. Tool Call Content & Diffs

When an agent requests a file modification, it should ideally use the `diff` content type. However, many backends provide the parameters via `raw_input` before the final `content` is settled.

### Known Field Names for Diffs (in `raw_input`)
Backends often use different keys for "old text" and "new text". Our client handles the following variations:

*   **Old Content:** `oldText`, `old_str`, `old_string`, `old_text`, `oldString`
*   **New Content:** `newText`, `new_str`, `new_string`, `new_text`, `newString`
*   **File Path:** `path`, `file_path`, `filePath`, `filepath`

### Content Block Types
*   **Standard ACP:** Uses `oldText` and `newText`.
*   **Legacy/Specific Backends:** May use `old_text` and `new_text`.

## 2. Session Updates

The protocol uses JSON-RPC notifications for updates. Some backends wrap these updates in a `sessionUpdate` field.

*   **Standard:** The message type is inferred from the object structure or class name (e.g., `AgentMessageChunk`).
*   **Wrapped (Opencode):** A raw dictionary with `{"sessionUpdate": "agentMessageChunk", "content": ...}`. Our `acp_service.py` handles this by mapping `camelCase` session updates to their respective handlers.

## 3. Tool Call Kinds

The `kind` field in a `ToolCall` helps the client display the correct icon.
*   **Supported Kinds:** `read`, `edit`, `delete`, `move`, `search`, `execute`, `think`, `fetch`, `other`.
*   **Fallback:** If `kind` is missing or unrecognized, the client defaults to `🔧` (other).

## 4. Permission Requests

### Permission Options
The protocol defines `kind` for options (`allow_once`, `allow_always`, etc.), but some backends may only provide a `name`.
*   **Heuristic Matching:** If `kind` is missing, the client uses `is_approval_option` (checking for keywords like "Allow", "Accept", "Yes") to decide which emoji (✅ or ❌) to show on the button.

### Multi-Modal Logic
When a backend requests permission for a tool involving a file (like `edit`), it may or may not include the `path` in the `title`. Our client attempts to extract the path from `raw_input` and append it to the display title for better user context.

## 5. Implementation-Specific Updates

### AvailableCommandsUpdate
Observed in `opencode` backends. This update provides a list of high-level commands available in the current context (e.g., `init`, `review`, `compact`).
- **Handling:** The client logs these and stores them in the `ActiveSession` for future use.

