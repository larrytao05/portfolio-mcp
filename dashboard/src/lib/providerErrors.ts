import { ApiError } from "../api/client";

const terminalReadErrorCodes = new Set([
  "provider_configuration_error",
  "provider_authentication_failed",
  "provider_tls_verification_failed",
  "provider_tls_configuration_error",
  "schwab_reauthorization_required",
  "schwab_authorization_code_rejected",
  "schwab_client_authentication_failed",
  "provider_authorization_failed",
]);

export function readErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.message.trim() ? error.message : fallback;
}

export function retryProviderRead(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.code && terminalReadErrorCodes.has(error.code)) {
    return false;
  }
  return failureCount < 3;
}
