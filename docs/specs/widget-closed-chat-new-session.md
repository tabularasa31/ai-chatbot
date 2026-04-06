# Widget UX Spec: Closed Conversation UX v2

## Purpose

Improve the closed-chat experience in the widget without changing backend lifecycle rules or adding new backend APIs.

This spec covers only frontend behavior after the backend returns `chat_ended = true`.

## Product decision

When a chat is closed:

- the old `session_id` becomes terminal and must not be reused
- the already visible transcript stays on screen
- the widget shows an explicit `Start new chat` CTA
- starting a new chat does **not** erase old messages
- the next conversation starts visually **below** the old one

The backend still creates the next chat naturally on the first message sent without `session_id`.

## Non-goals

This spec does **not** introduce:

- a conversation list or picker
- backend conversation grouping
- reopen / resume for closed chats
- server-side history restore after reload
- multi-tab coordination

## Source of truth

Backend remains the source of truth for terminal state:

- `chat_ended = true` means the current `session_id` is no longer usable
- closed chats are represented by `Chat.ended_at != null`

Frontend is responsible only for:

- local widget state
- visual separation between finished and fresh conversations
- cleanup of persisted `session_id`

## State model

The widget keeps a flat `messages[]` list and adds lightweight system markers inside that list.

Message model:

```ts
type ChatWidgetMessage =
  | {
      id: string;
      type: "user" | "assistant" | "error";
      text: string;
    }
  | {
      id: string;
      type: "system";
      subtype: "conversation_ended" | "new_conversation";
    };
```

Runtime state:

- `sessionId: string | null`
- `chatClosed: boolean`
- `activeTicket: string | null`
- `input: string`

## Main states

### Active conversation

Conditions:

- `chatClosed === false`

Behavior:

- input enabled
- sends continue in current session if `sessionId` exists
- if `sessionId === null`, the first new message creates a fresh backend session

### Closed conversation

Conditions:

- `chatClosed === true`

Behavior:

- input disabled
- widget does not send anything to the old `sessionId`
- persisted session storage is already cleared
- the last `conversation_ended` system marker shows the CTA

### Fresh conversation prepared

Conditions:

- user clicked `Start new chat`
- `chatClosed === false`
- `sessionId === null`
- `messages` already contains `new_conversation`

Behavior:

- input enabled
- no backend session exists yet
- the next user message starts a fresh session

## Events and transitions

### Event A: normal successful response

Conditions:

- chat response succeeded
- `chat_ended !== true`

Transition:

- remain in active conversation
- keep or save returned `session_id`
- append normal user / assistant messages

### Event B: backend returned `chat_ended = true`

Transition:

- `chatClosed = true`
- `sessionId = null`
- append `system/conversation_ended`
- clear:
  - `chat9:${botId}:session`
  - `chat9:${botId}:session_updated_at`

Result:

- widget moves into closed conversation state

### Event C: user clicks `Start new chat`

Transition:

- `chatClosed = false`
- `sessionId = null`
- `activeTicket = null`
- `input = ""`
- append `system/new_conversation`
- clear storage again (idempotent)
- focus input

Result:

- old history stays visible
- new conversation is only prepared, not yet created on backend

### Event D: first message after `Start new chat`

Conditions:

- `chatClosed === false`
- `sessionId === null`

Transition:

- frontend sends the next message without `session_id`
- backend creates a new chat
- frontend saves the returned new `session_id`

Result:

- widget returns to active conversation

## Rendering rules

Messages render in chronological order inside one flat list.

### User / assistant / error messages

- same rendering as before

### System marker: `conversation_ended`

Render as a lightweight informational block in the message list.

Recommended copy:

- `This conversation has ended.`

CTA:

- `Start new chat`

Important:

- only the **latest** `conversation_ended` marker should show an active CTA when `chatClosed === true`
- historical closed markers from older cycles should remain visible but without an active button

### System marker: `new_conversation`

Render as a compact separator with no action.

Recommended copy:

- `New conversation`

## Input behavior

When `chatClosed === true`:

- input disabled
- send button disabled
- placeholder:
  - `Start a new chat to ask another question`

When `chatClosed === false`:

- normal input behavior

## Storage behavior

Persist session only for active usable chats.

Keys:

- `chat9:${botId}:session`
- `chat9:${botId}:session_updated_at`

On `chat_ended = true`:

- remove both keys immediately

On `Start new chat`:

- remove both keys again safely

## Session handling rules

1. Frontend must never send a new message into a closed chat.
2. After `chat_ended = true`, the current session is terminal.
3. A new chat always starts only without `session_id`.
4. A new `session_id` appears only after the first successful response of the new conversation.

## Active ticket behavior

When chat closes:

- `activeTicket` may stay visible until the user starts a new chat

When the user clicks `Start new chat`:

- `activeTicket = null`

This prevents escalation UI from leaking into the next conversation.

## Edge cases

### Repeated `chat_ended = true`

- do not append identical closed markers forever
- if the widget is already closed and the last marker is `conversation_ended`, do not add another one

### Reload after closed chat

- closed session must not be restored from browser storage
- losing on-screen history after reload is acceptable in this version

### User clicks `Start new chat` but sends nothing

- no backend chat is created yet
- this is valid behavior

### Multiple cycles in one UI

Allowed timeline:

- old messages
- `conversation_ended`
- `new_conversation`
- new messages
- `conversation_ended`
- `new_conversation`
- newer messages

## Acceptance criteria

1. If backend returns `chat_ended = true`, the widget:
   - shows a closed-conversation message
   - shows `Start new chat`
   - disables input
   - clears persisted `session_id`
2. Clicking `Start new chat`:
   - keeps old history visible
   - inserts a visual `new_conversation` separator
   - re-enables input
   - clears `sessionId`
   - clears `activeTicket`
3. The first next message:
   - is sent without `session_id`
   - creates a new backend session
   - continues as a new conversation below the separator
4. Closed sessions are never reused from browser storage or from follow-up sends.
