import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { deriveStorageUserId } from "./utils.ts";

describe("deriveStorageUserId", () => {
  test("an email-only visitor and a user_id equal to that email get distinct storage ids", () => {
    assert.notEqual(deriveStorageUserId({ email: "a@x.io" }), deriveStorageUserId({ user_id: "a@x.io" }));
  });

  test("blank hints yield no storage id", () => {
    assert.equal(deriveStorageUserId({ user_id: " ", email: "" }), null);
    assert.equal(deriveStorageUserId(null), null);
  });
});
