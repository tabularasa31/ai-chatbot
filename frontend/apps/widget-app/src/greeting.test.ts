import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, mock, test } from "node:test";
import { greetOnce, greetingLocale, hasBeenGreeted } from "./greeting.ts";

class MemoryStorage {
  readonly items = new Map<string, string>();
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
}

const storage = new MemoryStorage();
Object.assign(globalThis, { window: { localStorage: storage } });

const params = { apiBase: "https://api.example.com", botId: "ch_bot", locale: "fr-FR", isCurrent: () => true };

function stubFetch(respond: () => Promise<Response>) {
  return mock.method(globalThis, "fetch", (_input: string | URL | Request, _init?: RequestInit) => respond());
}

const greeting = () => Promise.resolve(Response.json({ text: "Hello, how can I help?" }));

beforeEach(() => {
  storage.items.clear();
  mock.method(console, "error", () => {});
});
afterEach(() => mock.restoreAll());

describe("greetOnce", () => {
  test("asks with a plain GET carrying bot and locale, returns the text and sets the marker", async () => {
    const fetchMock = stubFetch(greeting);

    const text = await greetOnce(params);

    assert.equal(text, "Hello, how can I help?");
    assert.equal(fetchMock.mock.callCount(), 1);
    const [url, init] = fetchMock.mock.calls[0].arguments;
    assert.equal(url, "https://api.example.com/widget/greeting?bot_id=ch_bot&locale=fr-Latn-FR");
    assert.equal(init, undefined);
    assert.deepEqual([...storage.items.keys()], ["chat9:ch_bot:greeted"]);
  });

  test("a zh-TW browser is greeted in Traditional Chinese because the request carries the script", async () => {
    const fetchMock = stubFetch(greeting);

    await greetOnce({ ...params, locale: "zh-TW" });

    const [url] = fetchMock.mock.calls[0].arguments;
    assert.equal(new URL(String(url)).searchParams.get("locale"), "zh-Hant-TW");
  });

  test("a tenant hint written with an underscore (zh_TW) also gets the script", async () => {
    const fetchMock = stubFetch(greeting);

    await greetOnce({ ...params, locale: "zh_TW" });

    const [url] = fetchMock.mock.calls[0].arguments;
    assert.equal(new URL(String(url)).searchParams.get("locale"), "zh-Hant-TW");
  });

  test("a tag Intl.Locale rejects is sent unchanged", async () => {
    const fetchMock = stubFetch(greeting);

    await greetOnce({ ...params, locale: "not a locale!" });

    const [url] = fetchMock.mock.calls[0].arguments;
    assert.equal(new URL(String(url)).searchParams.get("locale"), "not a locale!");
  });

  test("a browser without Intl.Locale sends the tag unchanged", async () => {
    const fetchMock = stubFetch(greeting);
    const original = Intl.Locale;
    Object.assign(Intl, { Locale: undefined });
    try {
      await greetOnce({ ...params, locale: "zh-TW" });
    } finally {
      Object.assign(Intl, { Locale: original });
    }

    const [url] = fetchMock.mock.calls[0].arguments;
    assert.equal(new URL(String(url)).searchParams.get("locale"), "zh-TW");
  });

  test("makes no request once the marker is set", async () => {
    storage.setItem("chat9:ch_bot:greeted", "1");
    const fetchMock = stubFetch(greeting);

    assert.equal(await greetOnce(params), null);
    assert.equal(fetchMock.mock.callCount(), 0);
  });

  test("a second tab opened while the first request is in flight does not greet again", async () => {
    const fetchMock = stubFetch(greeting);

    const [first, second] = await Promise.all([greetOnce(params), greetOnce(params)]);

    assert.equal(first, "Hello, how can I help?");
    assert.equal(second, null);
    assert.equal(fetchMock.mock.callCount(), 1);
  });

  test("a response that arrives after the identity changed is dropped and leaves the identity ungreeted", async () => {
    stubFetch(greeting);

    assert.equal(await greetOnce({ ...params, userId: "alice", isCurrent: () => false }), null);
    assert.equal(hasBeenGreeted("ch_bot", "alice"), false);
  });

  test("the marker is scoped to the identity", async () => {
    stubFetch(greeting);
    await greetOnce({ ...params, userId: "alice" });

    assert.equal(hasBeenGreeted("ch_bot", "alice"), true);
    assert.equal(hasBeenGreeted("ch_bot", "bob"), false);
    assert.equal(hasBeenGreeted("ch_bot"), false);
  });

  test("an HTTP error, a network error or a body without text yields null and clears the marker", async () => {
    const failures = [
      () => Promise.resolve(Response.json({}, { status: 404 })),
      () => Promise.reject(new TypeError("offline")),
      () => Promise.resolve(Response.json({ text: "  " })),
    ];
    for (const failure of failures) {
      const fetchMock = stubFetch(failure);
      assert.equal(await greetOnce(params), null);
      assert.equal(storage.items.size, 0);
      fetchMock.mock.restore();
    }
  });
});

describe("greetingLocale", () => {
  test("the tenant hint outranks the browser locale; blank hints fall back", () => {
    assert.equal(greetingLocale("de-DE", "fr-FR"), "de-DE");
    assert.equal(greetingLocale("  ", "fr-FR"), "fr-FR");
    assert.equal(greetingLocale(undefined, undefined), undefined);
  });
});
