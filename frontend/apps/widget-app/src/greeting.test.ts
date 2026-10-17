import assert from "node:assert/strict";
import { beforeEach, describe, mock, test } from "node:test";
import { greetOnce, greetingLocale, hasBeenGreeted } from "./greeting.ts";

class MemoryStorage {
  readonly items = new Map<string, string>();
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
}

const storage = new MemoryStorage();
Object.assign(globalThis, { window: { localStorage: storage } });

const greetingResponse = () => Response.json({ text: "Hello, how can I help?" });
const params = { apiBase: "https://api.example.com", botId: "ch_bot", locale: "fr-FR" };

beforeEach(() => storage.items.clear());

describe("greetOnce", () => {
  test("first visit shows the greeting through a read-only request and remembers it", async () => {
    const fetchImpl = mock.fn(async () => greetingResponse());

    const text = await greetOnce({ ...params, fetchImpl });

    assert.equal(text, "Hello, how can I help?");
    assert.equal(fetchImpl.mock.callCount(), 1);
    const [url, init] = fetchImpl.mock.calls[0].arguments as unknown as [string, RequestInit | undefined];
    assert.equal(url, "https://api.example.com/widget/greeting?bot_id=ch_bot&locale=fr-FR");
    assert.equal(init, undefined);
    assert.deepEqual([...storage.items.keys()], ["chat9:ch_bot:greeted"]);
  });

  test("a browser that was already greeted sends no request and shows nothing", async () => {
    storage.setItem("chat9:ch_bot:greeted", "1");
    const fetchImpl = mock.fn(async () => greetingResponse());

    assert.equal(await greetOnce({ ...params, fetchImpl }), null);
    assert.equal(fetchImpl.mock.callCount(), 0);
  });

  test("the marker is per identity: another visitor in the same browser is still greeted", async () => {
    await greetOnce({ ...params, userId: "alice", fetchImpl: async () => greetingResponse() });

    assert.equal(hasBeenGreeted("ch_bot", "alice"), true);
    assert.equal(hasBeenGreeted("ch_bot", "bob"), false);
    assert.equal(hasBeenGreeted("ch_bot"), false);
  });

  test("a failed greeting shows nothing and leaves the marker unset for the next visit", async () => {
    mock.method(console, "error", () => {});
    const rejected = await greetOnce({ ...params, fetchImpl: async () => Response.json({}, { status: 404 }) });
    const offline = await greetOnce({
      ...params,
      fetchImpl: async () => {
        throw new TypeError("offline");
      },
    });

    assert.equal(rejected, null);
    assert.equal(offline, null);
    assert.equal(storage.items.size, 0);
  });
});

describe("greetingLocale", () => {
  test("the tenant hint outranks the browser locale; blank hints fall back", () => {
    assert.equal(greetingLocale("de-DE", "fr-FR"), "de-DE");
    assert.equal(greetingLocale("  ", "fr-FR"), "fr-FR");
    assert.equal(greetingLocale(undefined, undefined), undefined);
  });
});
