import test from "node:test";
import assert from "node:assert/strict";
import {
  buildPrivacyLogCsv,
  getOriginalContentBadgeClassName,
  getOriginalContentStatus,
  getOriginalContentStatusFromFlags,
  getOriginalContentTextClassName,
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

test("getOriginalContentStatus treats empty strings as shown when original content is present", () => {
  assert.equal(
    getOriginalContentStatus({
      original: "",
      originalAvailable: true,
    }),
    "shown"
  );
});

test("getOriginalContentStatusFromFlags maps visible and available states without text sentinels", () => {
  assert.equal(getOriginalContentStatusFromFlags(true, true), "shown");
  assert.equal(getOriginalContentStatusFromFlags(false, true), "available");
  assert.equal(getOriginalContentStatusFromFlags(false, false), "removed");
});

test("original content class helpers map text and badge styles", () => {
  assert.equal(getOriginalContentTextClassName("shown"), "text-emerald-700");
  assert.equal(getOriginalContentBadgeClassName("available"), "bg-amber-50 text-amber-800 border-amber-200");
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

  assert.match(csv, /^"created_at_iso","direction","entity_type","count","client_id","actor_user_id","action_path","chat_id","message_id"/m);
  assert.match(csv, /"2026-03-27T09:00:00Z","original_view","EMAIL","2","cl_123","usr_123","\/chat\/logs\/session\/""abc""","chat_123","msg_123"/);
  assert.match(csv, /\r\n$/);
});

test("buildPrivacyLogCsv keeps a trailing CRLF and handles empty datasets", () => {
  assert.equal(
    buildPrivacyLogCsv([]),
    "\"created_at_iso\",\"direction\",\"entity_type\",\"count\",\"client_id\",\"actor_user_id\",\"action_path\",\"chat_id\",\"message_id\"\r\n"
  );
});

test("getOriginalContentStatus handles undefined original values", () => {
  assert.equal(
    getOriginalContentStatus({
      original: undefined,
      originalAvailable: false,
    }),
    "removed"
  );
});

test("getPrivacyLogExportFilename reflects current filter", () => {
  assert.equal(getPrivacyLogExportFilename("original_view", "30"), "privacy-log-original_view-30d.csv");
  assert.equal(getPrivacyLogExportFilename("", "7"), "privacy-log-all-7d.csv");
  assert.equal(getPrivacyLogExportFilename("foo/bar:baz", "7"), "privacy-log-foo_bar_baz-7d.csv");
  assert.equal(getPrivacyLogExportFilename("original_view", "07days"), "privacy-log-original_view-7d.csv");
});
