from __future__ import annotations

from pathlib import Path


CHAT_WIDGET_PATH = (
    Path(__file__).resolve().parents[1] / "frontend" / "components" / "ChatWidget.tsx"
)


def _source() -> str:
    return CHAT_WIDGET_PATH.read_text(encoding="utf-8")


def test_widget_uses_system_markers_for_closed_and_new_conversations() -> None:
    source = _source()

    assert 'type: "system"' in source
    assert 'subtype: "conversation_ended" | "new_conversation"' in source
    assert 'appendSystemMessage("conversation_ended")' in source
    assert 'appendSystemMessage("new_conversation")' in source


def test_handle_chat_ended_marks_session_closed_and_clears_storage() -> None:
    source = _source()
    start = source.index("const handleChatEnded = () => {")
    end = source.index("const applyAssistantMessage = (raw: string, ended?: boolean) => {", start)
    block = source[start:end]

    assert "setChatClosed(true);" in block
    assert "setSessionId(null);" in block
    assert 'appendSystemMessage("conversation_ended");' in block
    assert "clearStoredSession(botId);" in block


def test_start_new_chat_preserves_history_and_prepares_fresh_conversation() -> None:
    source = _source()
    start = source.index("const handleStartNewChat = () => {")
    end = source.index("const handleSend = async () => {", start)
    block = source[start:end]

    assert "setMessages([]);" not in block
    assert 'setInput("");' in block
    assert "setSessionId(null);" in block
    assert "setChatClosed(false);" in block
    assert "setActiveTicket(null);" in block
    assert 'appendSystemMessage("new_conversation");' in block
    assert "clearStoredSession(botId);" in block
    assert "inputRef.current?.focus();" in block


def test_closed_and_new_conversation_ui_copy_is_rendered_inside_message_list() -> None:
    source = _source()

    messages_block_idx = source.index("{messages.length === 0 && !loading ? (")
    ended_copy_idx = source.index("This conversation has ended.")
    new_copy_idx = source.index("New conversation")
    cta_idx = source.index("Start new chat")

    assert ended_copy_idx > messages_block_idx
    assert new_copy_idx > messages_block_idx
    assert cta_idx > ended_copy_idx


def test_closed_state_input_and_session_rules_match_v2_spec() -> None:
    source = _source()

    assert 'placeholder={chatClosed ? "Start a new chat to ask another question" : "Type a message..."}' in source
    assert 'disabled={loading || chatClosed}' in source
    assert "if (attemptSessionId) params.set(\"session_id\", attemptSessionId);" in source
    assert "setSessionId(data.session_id);" in source
