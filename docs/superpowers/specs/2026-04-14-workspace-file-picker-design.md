# Workspace File Picker Design

## Goal
Add a folder icon button to the right of the chat input tool selector. When clicked, it opens a file-picker modal showing the current workspace directory tree. Users can navigate into subdirectories, select a file or folder, and click **Insert** to add its absolute path to the chat input wrapped as `<file>/absolute/path</file>`. The inserted path is shown as a blue chip above the input box.

## Context
- Chat page: `web-ui/src/pages/ChatPage.tsx`
- Tool selector row sits below the input textarea (`chat-tool-status-row`)
- Current workspace path is available via `GET /api/v1/system/status` → `status.web.workspace`
- Only image attachments are supported today (`pendingImages` + hidden file input)

## Decision
**Approach A — Lazy Tree + File Chips**

A new backend API lists directory entries on demand. The frontend renders a lazy-loading `Tree` inside an Ant Design `Modal`. Selected paths are inserted into the textarea as `<file>/path</file>` and displayed as blue `Tag` chips above the input.

## Architecture

### New Backend API
**Endpoint:** `GET /api/v1/workspace/tree?path=<absolute_or_relative_path>`

**Behavior:**
- If `path` is omitted, returns the current workspace root entries.
- Returns only direct children (not recursive).
- Blocks path traversal; resolved path must be within the workspace root.

**Response:**
```json
{
  "success": true,
  "data": [
    { "name": "nanobot", "type": "dir", "absolute_path": "/abs/path/nanobot", "has_children": true },
    { "name": "README.md", "type": "file", "absolute_path": "/abs/path/README.md", "has_children": false }
  ]
}
```

### Frontend Components

#### 1. `WorkspaceFilePickerModal.tsx`
- Props: `open`, `workspace`, `onClose`, `onInsert(path: string)`
- Uses Ant Design `Modal` with `okText="Insert"`, `cancelText="Cancel"`.
- `okButton` disabled until a tree node is selected.
- `Tree` with `loadData` for lazy child loading via `api.getWorkspaceTree(path)`.
- `showLine` and `showIcon` for clear file/folder distinction.
- Selected node path passed to `onInsert` when user clicks Insert.

#### 2. `ChatPage.tsx` Changes
- **New state:**
  - `pendingFiles: string[]`
  - `filePickerVisible: boolean`
- **New UI row:** `pending-files-row` above `chat-input-row`, rendered when `pendingFiles.length > 0`.
  - Blue `Tag` chips (`color="blue"`) for each path.
  - Each chip has a close icon (`x`) that removes the path from both `pendingFiles` and the textarea text.
- **New button:** Folder icon (`FolderOpenOutlined`) placed to the left of the image-upload button inside `chat-input-row`.
- **onInsert handler:** Appends `<file>/absolute/path</file>` to `input` state and pushes the raw path into `pendingFiles`.

### Data Flow
1. User clicks folder icon → `filePickerVisible = true`.
2. Modal fetches workspace root via `api.getWorkspaceTree()`.
3. User expands a directory → lazy fetch children for that node.
4. User clicks a file or folder → `selectedPath` updated.
5. User clicks **Insert** → `onInsert(selectedPath)` called.
   - Modal closes.
   - `pendingFiles` gets the path (for blue chip rendering).
   - `input` gets `<file>/absolute/path</file>` appended.
6. On send, `pendingFiles` is cleared locally; the message text already carries the `<file>` tags.

## UI/UX Details

- **Modal title:** "Select File or Folder"
- **Empty state:** "No files in this directory"
- **Error state:** "Failed to load directory" with a retry button.
- **Chip behavior:** Removing a chip also strips the matching `<file>...</file>` block from the textarea.
- **Textarea behavior:** The `<file>` tag is plain text; color highlighting is provided solely by the blue chips.

## Security

- Backend must resolve `path` strictly under the workspace root.
- Any traversal attempt (`..`, absolute path outside workspace) returns `HTTP 400`.
- Only directory **metadata** (names, types, paths) is exposed; no file content is read.

## Error Handling

- API failure in modal → show inline error message and retry button.
- Selected path disappears before Insert → `message.error("Path no longer exists")` and refresh tree.
- Missing workspace in status → disable folder button with tooltip "Workspace unavailable".

## LLM Awareness

A one-line instruction is added to the agent bootstrap / system prompt so the LLM understands:

> "When the user message contains `<file>/path</file>`, it means they explicitly selected that file or folder from the workspace picker."

## Testing Plan

1. Open chat page, click folder icon → modal opens and shows workspace root.
2. Expand a directory → children load lazily.
3. Select a file, click Insert → blue chip appears, textarea contains `<file>/abs/path</file>`.
4. Select a folder, click Insert → same behavior.
5. Close chip → chip and `<file>` block both disappear.
6. Send message → backend receives text with `<file>` tags.
7. Try path traversal in API → returns 400.

## Files to Modify / Create

| Action | Path |
|--------|------|
| Create | `web-ui/src/components/WorkspaceFilePickerModal.tsx` |
| Modify | `web-ui/src/pages/ChatPage.tsx` |
| Modify | `web-ui/src/api.ts` (add `getWorkspaceTree`) |
| Modify | `nanobot/web/api.py` (add `GET /api/v1/workspace/tree`) |
| Modify | `web-ui/src/pages/ChatPage.css` (chip row styles) |
| Modify | `nanobot/agent/context.py` — add `<file>` tag instruction to `DEFAULT_IDENTITY_CONTENT` |

## Future Extensions (out of scope)

- Multi-select in modal (insert multiple paths at once).
- Search/filter inside the modal.
- File type icons (pdf, image, code) instead of generic file icon.
