import { afterEach, describe, expect, it, vi } from "vitest";
import {
  assertPublicHttpUrl,
  isBlockedAddress,
  safeFetch,
} from "./network-guard";

describe("network guard address rules", () => {
  it("blocks loopback, private, link-local and metadata addresses", () => {
    for (const address of [
      "127.0.0.1",
      "10.1.2.3",
      "172.16.5.4",
      "192.168.1.10",
      "169.254.169.254",
      "100.64.0.1",
      "0.0.0.0",
      "::1",
      "fc00::1",
      "fe80::1",
      "::ffff:127.0.0.1",
    ]) {
      expect(isBlockedAddress(address), address).toBe(true);
    }
  });

  it("allows public addresses", () => {
    expect(isBlockedAddress("1.1.1.1")).toBe(false);
    expect(isBlockedAddress("8.8.8.8")).toBe(false);
  });

  it("rejects non-http schemes, localhost and literal private IPs", async () => {
    await expect(assertPublicHttpUrl("file:///etc/passwd")).rejects.toThrow();
    await expect(
      assertPublicHttpUrl("http://localhost:3000"),
    ).rejects.toThrow();
    await expect(
      assertPublicHttpUrl("http://127.0.0.1:8000"),
    ).rejects.toThrow();
    await expect(
      assertPublicHttpUrl("http://169.254.169.254/latest/meta-data"),
    ).rejects.toThrow();
  });
});

describe("redirect validation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("re-validates redirect targets and rejects localhost", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(null, {
        status: 302,
        headers: { location: "http://127.0.0.1:8000/admin" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      safeFetch("https://example.com/start", {}, { maxRedirects: 5 }),
    ).rejects.toThrow(/blocked/i);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("follows a validated redirect", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(null, {
          status: 302,
          headers: { location: "https://1.1.1.1/final" },
        }),
      )
      .mockResolvedValueOnce(new Response("ok", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await safeFetch("https://1.1.1.1/start", {});
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops after the redirect limit", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, {
        status: 302,
        headers: { location: "https://1.1.1.1/loop" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      safeFetch("https://1.1.1.1/start", {}, { maxRedirects: 2 }),
    ).rejects.toThrow(/too many redirects/i);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
