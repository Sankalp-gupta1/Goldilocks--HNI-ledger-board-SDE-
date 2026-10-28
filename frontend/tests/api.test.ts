// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, query } from "../src/api";
afterEach(() => vi.unstubAllGlobals());
describe("API boundary", () => {
  it("keeps uploads multipart while sending the required same-origin header", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ staged: 2 }), { status: 201 }),
      );
    vi.stubGlobal("fetch", fetch);
    const form = new FormData();
    form.append("file", new Blob(["fictional"]), "file.csv");
    await api("/api/import", { method: "POST", body: form });
    const options = fetch.mock.calls[0][1];
    expect(options.credentials).toBe("same-origin");
    expect(options.headers.get("X-Goldilocks")).toBe("1");
    expect(options.headers.has("Content-Type")).toBe(false);
  });
  it("announces expired sessions without swallowing the server error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ error: "Sign in again" }), {
            status: 401,
          }),
        ),
    );
    const expired = vi.fn();
    window.addEventListener("session-expired", expired);
    await expect(api("/api/ledger")).rejects.toThrow("Sign in again");
    expect(expired).toHaveBeenCalledOnce();
    window.removeEventListener("session-expired", expired);
  });
  it("encodes account filters and omits empty dates", () => {
    expect(
      query({ from: "", to: "2026-10-31", account: "HDFC & Savings" }),
    ).toBe("?to=2026-10-31&account=HDFC+%26+Savings");
  });
});
