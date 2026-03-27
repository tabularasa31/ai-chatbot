import test from "node:test";
import assert from "node:assert/strict";
import {
  buildPrivacyLogCsv,
  getOriginalContentLabel,
  getOriginalContentStatus,
  getPrivacyLogExportFilename,
} from "../lib/privacy-ui";

test("getOriginalContentStatus returns shown when original text is present", () => {
  assert.equal(
    getOriginalContentStatus({
      original: "secret@example.com",
      originalAvailable: true,
    }),
    "shown"
  );
});

test("getOriginalContentStatus returns available when original is hidden but still stored", () => {
  assert.equal(
    getOriginalContentStatus({
      original: null,
      originalAvailable: true,
    }),
    "available"
  );
});

test("getOriginalContentStatus returns removed when original is gone", () => {
  assert.equal(
    getOriginalContentStatus({
      original: null,
      originalAvailable: false,
    }),
    "removed"
  );
});

test("getOriginalContentLabel maps labels for session lifecycle badges", () => {
  assert.equal(
    getOriginalContentLabel("shown", {
      shown: "Original content visible",
      available: "Original content available",
      removed: "Original content removed",
    }),
    "Original content visible"
  );
});

test("buildPrivacyLogCsv emits escaped CSV rows", () => {
  const csv = buildPrivacyLogCsv([
    {
      client_id: "cl_123",
      chat_id: "chat_123",
      message_id: "msg_123",
      actor_user_id: "usr_123",
      direction: "original_view",
      entity_type: "EMAIL",
      count: 2,
      action_path: '/chat/logs/session/"abc"',
      created_at: "2026-03-27T09:00:00Z",
    },
  ]);

  assert.match(csv, /^time,direction,entity_type,count,client_id,actor_user_id,action_path,chat_id,message_id/m);
  assert.match(csv, /"2026-03-27T09:00:00Z","original_view","EMAIL","2","cl_123","usr_123","\/chat\/logs\/session\/""abc""","chat_123","msg_123"/);
});

test("getPrivacyLogExportFilename reflects current filter", () => {
  assert.equal(getPrivacyLogExportFilename("original_view", "30"), "privacy-log-original_view-30d.csv");
  assert.equal(getPrivacyLogExportFilename("", "7"), "privacy-log-all-7d.csv");
});
