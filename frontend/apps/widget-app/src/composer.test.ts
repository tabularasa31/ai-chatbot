import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { composerCounter, maxCharsFromDetail, resolveLimit, restoreDraft } from "./composerLimit.ts";
import {
  NO_FAILURES,
  classifySendFailure,
  clearSendFailure,
  dismissBanner,
  markSendFailed,
  showBanner,
} from "./sendFailures.ts";

describe("composerCounter", () => {
  test("nothing below 900, muted remaining count from 900 through the limit", () => {
    assert.deepEqual(composerCounter("a".repeat(899), 1000), { state: "hidden" });
    assert.deepEqual(composerCounter("a".repeat(900), 1000), { state: "left", left: 100 });
    assert.deepEqual(composerCounter("a".repeat(1000), 1000), { state: "left", left: 0 });
  });

  test("over the limit reports how far over, without truncating", () => {
    assert.deepEqual(composerCounter("a".repeat(1500), 1000), { state: "over", over: 500 });
  });

  test("counts code points of the trimmed text, so emoji and padding do not distort the limit", () => {
    assert.deepEqual(composerCounter(`  ${"😀".repeat(1000)}  `, 1000), { state: "left", left: 0 });
    assert.deepEqual(composerCounter("😀".repeat(1001), 1000), { state: "over", over: 1 });
  });
});

describe("limit source", () => {
  test("server 413 value beats the config value, which beats the default", () => {
    assert.equal(resolveLimit(500, 2000), 500);
    assert.equal(resolveLimit(null, 2000), 2000);
    assert.equal(resolveLimit(null, undefined), 1000);
    assert.equal(resolveLimit(null, "2000"), 1000);
  });

  test("max_chars is read from the 413 detail only when it is a positive integer", () => {
    assert.equal(maxCharsFromDetail({ code: "message_too_long", max_chars: 800 }), 800);
    assert.equal(maxCharsFromDetail({ code: "message_too_long" }), null);
    assert.equal(maxCharsFromDetail("too long"), null);
  });
});

describe("restoreDraft", () => {
  test("restored text goes first, a newer draft follows after a blank line", () => {
    assert.equal(restoreDraft("long text", ""), "long text");
    assert.equal(restoreDraft("long text", "  "), "long text");
    assert.equal(restoreDraft("long text", "new draft"), "long text\n\nnew draft");
  });
});

describe("send failures", () => {
  test("status codes map to too long, rate limit or generic", () => {
    assert.equal(classifySendFailure(413), "too_long");
    assert.equal(classifySendFailure(429), "rate_limit");
    assert.equal(classifySendFailure(500), "other");
    assert.equal(classifySendFailure(null), "other");
  });

  test("repeated generic failures keep one marker on the bubble and no banner", () => {
    let state = NO_FAILURES;
    for (let i = 0; i < 3; i += 1) {
      state = markSendFailed(clearSendFailure(state, "m1"), "m1", "other");
    }
    assert.deepEqual(state, { failedIds: ["m1"], banner: null });
  });

  test("a new banner reason replaces the old one in the single slot", () => {
    let state = markSendFailed(NO_FAILURES, "m1", "rate_limit");
    assert.deepEqual(state.banner, { kind: "rate_limit" });
    state = showBanner(state, { kind: "history" });
    assert.deepEqual(state.banner, { kind: "history" });
    assert.deepEqual(state.failedIds, ["m1"]);
  });

  test("retrying or editing clears the banner; retrying also clears that bubble's marker", () => {
    const failed = markSendFailed(markSendFailed(NO_FAILURES, "m1", "other"), "m2", "rate_limit");
    assert.deepEqual(dismissBanner(failed), { failedIds: ["m1", "m2"], banner: null });
    assert.deepEqual(clearSendFailure(failed, "m2"), { failedIds: ["m1"], banner: null });
  });
});
