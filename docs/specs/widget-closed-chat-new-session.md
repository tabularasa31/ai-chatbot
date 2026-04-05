# Widget UX Spec: Closed Chat and Start New Chat

## Purpose

Define the widget behavior after a chat session is explicitly closed by backend logic and the user wants to ask a different question.

This spec covers only the widget UX and frontend behavior for starting a new conversation after `chat_ended = true`.

## Problem

Today, when the backend returns `chat_ended = true`, the widget moves into a closed state:

- input becomes disabled
- placeholder changes to `Chat closed`
- the user is not given an explicit in-widget action to start over

This creates a dead-end UX. If the user has a different question, they need a new session, but the widget does not currently expose that flow clearly.

## Product decision

When a chat is closed, the old session must not be reused.

If the user wants to ask another question, they should start a new chat from the widget UI.

## Trigger

The widget enters the closed-chat UX when the backend response contains:

```json
{
  "chat_ended": true
}
```

## Closed state UX

When `chat_ended = true`:

- keep existing transcript visible
- show closed-state UI below the conversation
- disable message input for the old session

Suggested copy:

- Title: `This conversation is closed.`
- Body: `You can start a new chat for another question.`
- Button: `Start new chat`

Shorter acceptable variant:

- `This conversation is closed.`
- button `Start new chat`

## Start new chat behavior

When the user clicks `Start new chat`, the widget should:

1. Clear visible conversation messages.
2. Reset `chatClosed` to `false`.
3. Reset `activeTicket` to `null`.
4. Clear the current `sessionId`.
5. Remove any persisted session storage for this bot.
6. Focus the input.

The widget should **not** call a dedicated “new chat” API endpoint.

Instead, the next user message should be sent **without** the old `session_id`, which naturally creates a new session in the current backend flow.

## Storage behavior

If the widget persists session continuity in browser storage, then when `chat_ended = true` it must clear:

- `chat9:${botId}:session`
- `chat9:${botId}:session_updated_at`

This guarantees that a closed chat is never resumed later.

## Backend assumptions

This UX depends on the current backend behavior:

- closed chats are represented by `Chat.ended_at != null`
- sending a new message to a closed session returns a closed-chat response instead of normal RAG

Therefore:

- closed sessions are terminal
- new user questions require a new session

## Accepted limitations

For MVP:

- the widget does not preserve the old transcript after `Start new chat` is clicked
- no “conversation picker” is introduced
- no multi-tab coordination is added

If the same user has two tabs open, this spec does not attempt to coordinate them.

## Acceptance criteria

1. If backend returns `chat_ended = true`, the widget clearly shows that the conversation is closed.
2. The user sees a visible `Start new chat` action.
3. Clicking `Start new chat` clears the old session state.
4. The next message starts a fresh backend session.
5. A closed session is never resumed from browser storage.
