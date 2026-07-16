import { test } from "node:test";

import { fetchLinkPreview } from "../../src/lib/linkPreview";

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `Expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`,
    );
  }
}

test("link preview requests share the same in-flight promise", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (() => {
    calls += 1;
    return Promise.resolve(
      new Response(
        JSON.stringify({
          url: "https://example.com/cache-test",
          title: "Cached",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
  }) as typeof fetch;

  try {
    const first = fetchLinkPreview("https://example.com/cache-test");
    const second = fetchLinkPreview("https://example.com/cache-test");
    if (first !== second) {
      throw new Error("Expected the cached in-flight promise");
    }
    const payload = await first;
    assertEquals(payload.title, "Cached");
    assertEquals(calls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("link preview failures become renderable error payloads", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (() =>
    Promise.resolve(new Response("", { status: 503 }))) as typeof fetch;

  try {
    const payload = await fetchLinkPreview(
      "https://example.com/failure-test-unique",
    );
    assertEquals(payload.url, "https://example.com/failure-test-unique");
    assertEquals(payload.error, "HTTP 503");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
