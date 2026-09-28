import { useEffect, useState } from "react";

import {
  type CreateMcpAuthorizationResult,
  type OrderDraft,
  getOrderDraft,
  issueMcpAuthorization,
} from "../api/client";

export type OwnerReviewProps =
  | {
      action: "submit";
      draftId: string;
      onClose: () => void;
    }
  | {
      action: "cancel";
      requestId: string;
      onClose: () => void;
    };

export function OwnerReview(props: OwnerReviewProps) {
  const targetId = props.action === "submit" ? props.draftId : props.requestId;
  return <OwnerReviewSession key={`${props.action}:${targetId}`} {...props} />;
}

function OwnerReviewSession(props: OwnerReviewProps) {
  const { action, onClose } = props;
  const targetId = action === "submit" ? props.draftId : props.requestId;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<OrderDraft | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [issuedCode, setIssuedCode] =
    useState<CreateMcpAuthorizationResult | null>(null);

  useEffect(() => {
    const closeOnNavigation = () => onClose();
    window.addEventListener("hashchange", closeOnNavigation);
    return () => window.removeEventListener("hashchange", closeOnNavigation);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setDraft(null);
    setConfirmed(false);
    setIssuedCode(null);

    if (action === "submit") {
      getOrderDraft(targetId)
        .then((res) => {
          if (active) {
            setDraft(res.draft);
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          if (active) {
            setError(
              err instanceof Error
                ? err.message
                : "Unable to load draft review.",
            );
            setLoading(false);
          }
        });
    } else {
      // Cancellation review is handled in #81
      setLoading(false);
    }

    return () => {
      active = false;
    };
  }, [action, targetId]);

  async function handleIssueCode() {
    if (!draft || !confirmed) return;
    setIssuing(true);
    setError(null);
    try {
      const result = await issueMcpAuthorization(
        draft.id,
        draft.fingerprint,
        true,
      );
      setIssuedCode(result);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to issue authorization code.",
      );
    } finally {
      setIssuing(false);
    }
  }

  return (
    <article
      className="quote-card owner-review-card"
      aria-label="Owner review"
    >
      <div className="section-heading">
        <div>
          <h3>
            {action === "submit"
              ? "Owner Review: MCP Order Submission"
              : "Owner Review: MCP Order Cancellation"}
          </h3>
          <span className="muted">ID: {targetId}</span>
        </div>
        <button
          type="button"
          className="close-button"
          onClick={onClose}
          aria-label="Close review"
        >
          ✕
        </button>
      </div>

      {loading && <p className="muted">Loading review details…</p>}

      {error && (
        <p className="inline-alert" role="alert">
          {error}
        </p>
      )}

      {action === "submit" && draft && (
        <div>
          <p>
            {draft.account.label || draft.account.id} ·{" "}
            {draft.instruction.side.toUpperCase()}{" "}
            {draft.instruction.quantity} {draft.instruction.type.toUpperCase()}{" "}
            ·{" "}
            {draft.instruction.limit_price
              ? `$${draft.instruction.limit_price}`
              : "market price"}
          </p>

          <dl>
            <div>
              <dt>Instrument</dt>
              <dd>
                {draft.instrument.name} ({draft.instrument.symbol})
              </dd>
            </div>
            <div>
              <dt>Estimated Notional</dt>
              <dd>{draft.safety.estimated_notional ?? "unavailable"}</dd>
            </div>
            <div>
              <dt>Quote</dt>
              <dd>
                Last {draft.quote.last_price ?? "unavailable"} · Bid{" "}
                {draft.quote.bid_price ?? "unavailable"} · Ask{" "}
                {draft.quote.ask_price ?? "unavailable"}
              </dd>
            </div>
            <div>
              <dt>Expires</dt>
              <dd>{new Date(draft.expires_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Fingerprint</dt>
              <dd>{draft.fingerprint}</dd>
            </div>
          </dl>

          {draft.warnings.length > 0 && (
            <p className="warning-note">
              Warnings: {draft.warnings.join(", ")}
            </p>
          )}

          {issuedCode ? (
            <div className="auth-code-box" role="status">
              <p>
                <strong>One-Time Authorization Code:</strong>
              </p>
              <p className="auth-code-display">{issuedCode.code}</p>
              <p className="muted">
                Expires at:{" "}
                {new Date(issuedCode.expires_at).toLocaleTimeString()}
              </p>
              <p className="muted">
                Provide this 8-digit code to the MCP client to authorize submission.
                The code can only be used once.
              </p>
              {onClose && (
                <button type="button" onClick={onClose}>
                  Done
                </button>
              )}
            </div>
          ) : (
            <div className="owner-review-actions">
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                I confirm this order instruction
              </label>
              <button
                type="button"
                className="button-primary"
                disabled={!confirmed || issuing}
                onClick={handleIssueCode}
              >
                {issuing ? "Issuing code…" : "Issue code"}
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
