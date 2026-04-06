"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.createTextMessage = createTextMessage;
exports.createSystemMessage = createSystemMessage;
exports.appendSystemMarker = appendSystemMarker;
exports.getLastEndedMarkerIndex = getLastEndedMarkerIndex;
function createMessageId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }
    return `msg_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
function createTextMessage(type, text) {
    return {
        id: createMessageId(),
        type,
        text,
    };
}
function createSystemMessage(subtype) {
    return {
        id: createMessageId(),
        type: "system",
        subtype,
    };
}
function appendSystemMarker(messages, subtype) {
    if (subtype === "conversation_ended") {
        const last = messages[messages.length - 1];
        if ((last === null || last === void 0 ? void 0 : last.type) === "system" && last.subtype === "conversation_ended") {
            return messages;
        }
    }
    return [...messages, createSystemMessage(subtype)];
}
function getLastEndedMarkerIndex(messages) {
    return messages.reduce((lastIndex, item, index) => {
        if (item.type === "system" && item.subtype === "conversation_ended") {
            return index;
        }
        return lastIndex;
    }, -1);
}
