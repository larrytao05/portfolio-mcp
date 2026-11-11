import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { readErrorMessage, retryProviderRead } from "./providerErrors";

describe("provider read errors", () => {
  it.each([
    "provider_configuration_error",
    "provider_authentication_failed",
    "provider_tls_verification_failed",
    "provider_tls_configuration_error",
    "schwab_reauthorization_required",
    "schwab_authorization_code_rejected",
    "schwab_client_authentication_failed",
    "provider_authorization_failed",
  ])("does not retry terminal error %s", (code) => {
    expect(retryProviderRead(0, new ApiError(503, "Safe explanation", code))).toBe(false);
  });

  it.each(["provider_rate_limited", "provider_unavailable", "provider_response_error", "provider_error", "unexpected_code"])(
    "bounds retries for read error %s",
    (code) => {
      const error = new ApiError(503, "Safe explanation", code);
      expect(retryProviderRead(0, error)).toBe(true);
      expect(retryProviderRead(2, error)).toBe(true);
      expect(retryProviderRead(3, error)).toBe(false);
    },
  );

  it("uses safe API messages and a fallback for unknown errors", () => {
    expect(readErrorMessage(new ApiError(503, "Repair backend trust."), "Fallback")).toBe("Repair backend trust.");
    expect(readErrorMessage(new ApiError(503, ""), "Fallback")).toBe("Fallback");
    expect(readErrorMessage(new Error("raw secret"), "Fallback")).toBe("Fallback");
  });
});
