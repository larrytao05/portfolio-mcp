import { useEffect, useState } from "react";

import {
  type OrderDraft,
  type StoredCancellationRequest,
  createCancellationMcpAuthorization,
  getCancellationRequest,
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

interface AuthCodeBoxProps {
  code: string;
  expiresAt: string;
  actionNoun: "submission" | "cancellation";
  onClose?: () => void;
}

function AuthCodeBox({
  code,
  expiresAt,
  actionNoun,
  onClose,
}: AuthCodeBoxProps) {
  return (
    <div className="auth-code-box" role="status">
      <p>
        <strong>One-Time Authorization Code:</strong>
      </p>
      <p className="auth-code-display">{code}</p>
      <p className="muted">
        Expires at: {new Date(expiresAt).toLocaleTimeString()}
      </p>
      <p className="muted">
        Provide this 8-digit code to the MCP client to authorize {actionNoun}.
        The code can only be used once.
      </p>
      {onClose && (
        <button type="button" onClick={onClose}>
          Done
        </button>
      )}
    </div>
  );
}

interface ReviewActionButtonsProps {
  confirmed: boolean;
  onConfirmChange: (checked: boolean) => void;
  issuing: boolean;
  onIssue: () => void;
  confirmLabel: string;
}

function ReviewActionButtons({
  confirmed,
  onConfirmChange,
  issuing,
  onIssue,
  confirmLabel,
}: ReviewActionButtonsProps) {
  return (
    <div className="owner-review-actions">
      <label>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => onConfirmChange(e.target.checked)}
        />
        {confirmLabel}
      </label>
      <button
        type="button"
        className="button-primary"
        disabled={!confirmed || issuing}
        onClick={onIssue}
      >
        {issuing ? "Issuing code…" : "Issue code"}
      </button>
    </div>
  );
}

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
  const [cancellationRequest, setCancellationRequest] =
    useState<StoredCancellationRequest | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [issuedCode, setIssuedCode] = useState<{
    code: string;
    expires_at: string;
  } | null>(null);

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
    setCancellationRequest(null);
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
      getCancellationRequest(targetId)
        .then((res) => {
          if (active) {
            setCancellationRequest(res.cancellation_request);
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          if (active) {
            setError(
              err instanceof Error
                ? err.message
                : "Unable to load cancellation review.",
            );
            setLoading(false);
          }
        });
    }

    return () => {
      active = false;
    };
  }, [action, targetId]);

  async function handleIssueCode() {
    if (!confirmed) return;
    setIssuing(true);
    setError(null);
    try {
      if (action === "submit") {
        if (!draft) return;
        const result = await issueMcpAuthorization(
          draft.id,
          draft.fingerprint,
          true,
        );
        setIssuedCode(result);
      } else {
        if (!cancellationRequest) return;
        const result = await createCancellationMcpAuthorization(
          cancellationRequest.id,
          cancellationRequest.fingerprint,
          true,
        );
        setCancellationRequest(result.cancellation_request);
        setIssuedCode(result);
      }
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
            <AuthCodeBox
              code={issuedCode.code}
              expiresAt={issuedCode.expires_at}
              actionNoun="submission"
              onClose={onClose}
            />
          ) : (
            <ReviewActionButtons
              confirmed={confirmed}
              onConfirmChange={setConfirmed}
              issuing={issuing}
              onIssue={handleIssueCode}
              confirmLabel="I confirm this order instruction"
            />
          )}
        </div>
      )}

      {action === "cancel" && cancellationRequest && (
        <div>
          <p>
            {cancellationRequest.account_id} · {cancellationRequest.symbol} · Order{" "}
            {cancellationRequest.order_id}
          </p>

          <dl>
            <div>
              <dt>Symbol</dt>
              <dd>{cancellationRequest.symbol}</dd>
            </div>
            <div>
              <dt>Account</dt>
              <dd>{cancellationRequest.account_id}</dd>
            </div>
            <div>
              <dt>Provider</dt>
              <dd>{cancellationRequest.provider}</dd>
            </div>
            <div>
              <dt>Order ID</dt>
              <dd>{cancellationRequest.order_id}</dd>
            </div>
            {cancellationRequest.broker_order_id && (
              <div>
                <dt>Broker Order ID</dt>
                <dd>{cancellationRequest.broker_order_id}</dd>
              </div>
            )}
            <div>
              <dt>Remaining Quantity</dt>
              <dd>{cancellationRequest.remaining_quantity}</dd>
            </div>
            <div>
              <dt>Expected State</dt>
              <dd>{cancellationRequest.expected_state}</dd>
            </div>
            <div>
              <dt>Expected Version</dt>
              <dd>{cancellationRequest.expected_version}</dd>
            </div>
            <div>
              <dt>Fingerprint</dt>
              <dd>{cancellationRequest.fingerprint}</dd>
            </div>
            <div>
              <dt>Expires</dt>
              <dd>{new Date(cancellationRequest.expires_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{cancellationRequest.status}</dd>
            </div>
          </dl>

          {cancellationRequest.status !== "pending" && (
            <p className="inline-alert" role="alert">
              This cancellation request is not pending (status:{" "}
              {cancellationRequest.status}
              {cancellationRequest.invalidation_reason
                ? `, reason: ${cancellationRequest.invalidation_reason}`
                : ""}
              ).
            </p>
          )}

          {issuedCode ? (
            <AuthCodeBox
              code={issuedCode.code}
              expiresAt={issuedCode.expires_at}
              actionNoun="cancellation"
              onClose={onClose}
            />
          ) : (
            cancellationRequest.status === "pending" && (
              <ReviewActionButtons
                confirmed={confirmed}
                onConfirmChange={setConfirmed}
                issuing={issuing}
                onIssue={handleIssueCode}
                confirmLabel="I confirm this cancellation request"
              />
            )
          )}
        </div>
      )}
    </article>
  );
}
