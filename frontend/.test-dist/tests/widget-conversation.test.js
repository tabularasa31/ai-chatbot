"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = __importDefault(require("node:test"));
const strict_1 = __importDefault(require("node:assert/strict"));
const widget_conversation_1 = require("../lib/widget-conversation");
(0, node_test_1.default)("appendSystemMarker adds a closed marker once", () => {
    const userMessage = (0, widget_conversation_1.createTextMessage)("user", "hello");
    const once = (0, widget_conversation_1.appendSystemMarker)([userMessage], "conversation_ended");
    const twice = (0, widget_conversation_1.appendSystemMarker)(once, "conversation_ended");
    strict_1.default.equal(once.length, 2);
    strict_1.default.equal(twice.length, 2);
    strict_1.default.equal(twice[1].type, "system");
    strict_1.default.equal(twice[1].subtype, "conversation_ended");
});
(0, node_test_1.default)("appendSystemMarker keeps prior history when starting a new conversation", () => {
    const messages = [
        (0, widget_conversation_1.createTextMessage)("user", "old question"),
        (0, widget_conversation_1.createTextMessage)("assistant", "old answer"),
        (0, widget_conversation_1.createSystemMessage)("conversation_ended"),
    ];
    const next = (0, widget_conversation_1.appendSystemMarker)(messages, "new_conversation");
    strict_1.default.equal(next.length, 4);
    strict_1.default.equal(next[0].type, "user");
    strict_1.default.equal(next[1].type, "assistant");
    strict_1.default.equal(next[2].subtype, "conversation_ended");
    strict_1.default.equal(next[3].subtype, "new_conversation");
});
(0, node_test_1.default)("getLastEndedMarkerIndex returns the latest ended marker", () => {
    const messages = [
        (0, widget_conversation_1.createTextMessage)("user", "first"),
        (0, widget_conversation_1.createSystemMessage)("conversation_ended"),
        (0, widget_conversation_1.createSystemMessage)("new_conversation"),
        (0, widget_conversation_1.createTextMessage)("assistant", "second"),
        (0, widget_conversation_1.createSystemMessage)("conversation_ended"),
    ];
    strict_1.default.equal((0, widget_conversation_1.getLastEndedMarkerIndex)(messages), 4);
});
