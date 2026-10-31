import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { composerCounter, maxCharsFromDetail, resolveLimit, restoreDraft } from "./composerLimit.ts";
import {
  NO_FAILURES,
  clearSendFailure,
  dismissBanner,
  dismissBannerOnEdit,
  keepUnsent,
  markSendFailed,
  moveToEnd,
  showBanner,
  type Banner,
} from "./sendFailures.ts";

describe("composerCounter", () => {
  test("nothing until 100 characters remain, then the remaining count through the limit", () => {
    assert.deepEqual(composerCounter("a".repeat(899), 1000), { state: "hidden" });
    assert.deepEqual(composerCounter("a".repeat(900), 1000), { state: "left", left: 100 });
    assert.deepEqual(composerCounter("a".repeat(1000), 1000), { state: "left", left: 0 });
  });

  test("the threshold follows a server-provided limit", () => {
    assert.deepEqual(composerCounter("a".repeat(399), 500), { state: "hidden" });
    assert.deepEqual(composerCounter("a".repeat(400), 500), { state: "left", left: 100 });
    assert.deepEqual(composerCounter("a".repeat(501), 500), { state: "over", over: 1 });
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

  test("retrying clears the banner and also clears that bubble's marker", () => {
    const failed = markSendFailed(markSendFailed(NO_FAILURES, "m1", "other"), "m2", "rate_limit");
    assert.deepEqual(dismissBanner(failed), { failedIds: ["m1", "m2"], banner: null });
    assert.deepEqual(clearSendFailure(failed, "m2"), { failedIds: ["m1"], banner: null });
  });

  test("typing clears the rate limit and resend banners but keeps history and support ones", () => {
    const withBanner = (banner: Banner) => showBanner(markSendFailed(NO_FAILURES, "m1", "other"), banner);
    assert.equal(dismissBannerOnEdit(withBanner({ kind: "rate_limit" })).banner, null);
    assert.equal(dismissBannerOnEdit(withBanner({ kind: "resend", messageId: "a1" })).banner, null);
    assert.deepEqual(dismissBannerOnEdit(withBanner({ kind: "history" })).banner, { kind: "history" });
    assert.deepEqual(
      dismissBannerOnEdit(withBanner({ kind: "escalate", messageId: "a1" })).banner,
      { kind: "escalate", messageId: "a1" },
    );
    assert.deepEqual(dismissBannerOnEdit(withBanner({ kind: "history" })).failedIds, ["m1"]);
  });
});

describe("failed bubble placement", () => {
  const log = [{ id: "m1" }, { id: "a1" }, { id: "m2" }];

  test("a retried bubble moves to the end of the log and keeps its id", () => {
    assert.deepEqual(moveToEnd(log, "m1"), [{ id: "a1" }, { id: "m2" }, { id: "m1" }]);
    assert.deepEqual(moveToEnd(log, "m2"), log);
    assert.deepEqual(moveToEnd(log, "missing"), log);
  });

  test("reloading history keeps the failed bubbles that never reached the server", () => {
    const hydrated = [{ id: "s1" }, { id: "s2" }];
    assert.deepEqual(keepUnsent(hydrated, log, ["m2"]), [{ id: "s1" }, { id: "s2" }, { id: "m2" }]);
    assert.deepEqual(keepUnsent(hydrated, log, []), hydrated);
  });

  test("a failed bubble the server saved anyway is not shown twice after hydration", () => {
    const saved = [{ id: "s1", type: "user", text: "first" }, { id: "s2", type: "assistant", text: "hi" }, { id: "s3", type: "user", text: "hello there" }];
    const current = [
      { id: "m1", type: "user", text: "hello there  " },
      { id: "m2", type: "user", text: "still unsent" },
    ];
    assert.deepEqual(keepUnsent(saved, current, ["m1", "m2"]), [...saved, current[1]]);
  });
});
