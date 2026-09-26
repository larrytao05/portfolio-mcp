import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  type StoredOrder,
  ApiError,
  confirmOrderCancellation,
  getOrder,
} from "../../api/client";

export type OrderCancellationModalProps = {
  order: StoredOrder;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (updatedOrder: StoredOrder) => void;
  onOrderUpdated?: (updatedOrder: StoredOrder) => void;
  onReconcileRequested?: (orderId: string) => void;
};

export function OrderCancellationModal({
  order,
  isOpen,
  onClose,
  onSuccess,
  onOrderUpdated,
  onReconcileRequested,
}: OrderCancellationModalProps) {
  const [currentOrder, setCurrentOrder] = useState<StoredOrder>(order);
  const [notice, setNotice] = useState<{
    kind: "conflict" | "error";
    message: string;
  } | null>(null);

  const modalRef = useRef<HTMLDivElement>(null);
  const initialFocusRef = useRef<HTMLButtonElement>(null);
  const triggerElementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setCurrentOrder(order);
    setNotice(null);
  }, [order]);

  const isUnknown = currentOrder.state === "UNKNOWN";

  const cancelMutation = useMutation({
    onMutate: () => {
      setNotice(null);
    },
    mutationFn: () =>
      confirmOrderCancellation(currentOrder.id, {
        expected_version: currentOrder.version,
        expected_state: currentOrder.state,
        confirmed: true,
      }),
    onSuccess: (data) => {
      setCurrentOrder(data.order);
      onOrderUpdated?.(data.order);
      if (data.order.state !== "UNKNOWN") {
        onSuccess(data.order);
      }
    },
    onError: async (error: unknown) => {
      const isConflict = error instanceof ApiError && error.status === 409;

      if (isConflict) {
        try {
          const fresh = await getOrder(currentOrder.id);
          setCurrentOrder(fresh.order);
          onOrderUpdated?.(fresh.order);
          if (fresh.order.can_cancel) {
            setNotice({
              kind: "conflict",
              message:
                "Order state changed before cancellation could be confirmed. The details have been updated. Please review before confirming.",
            });
          } else {
            setNotice({
              kind: "conflict",
              message: `Order can no longer be canceled (${
                fresh.order.blocking_reason || fresh.order.state
              }).`,
            });
          }
        } catch {
          setNotice({
            kind: "error",
            message: "Order state changed, but unable to reload current state.",
          });
        }
      } else {
        setNotice({
          kind: "error",
          message:
            error instanceof Error ? error.message : "Failed to cancel order.",
        });
      }
    },
  });

  // Focus management: capture active element on open, focus initial element, and restore on close
  useEffect(() => {
    if (!isOpen) return;
    triggerElementRef.current = document.activeElement as HTMLElement | null;

    const timer = setTimeout(() => {
      initialFocusRef.current?.focus();
    }, 0);

    return () => {
      clearTimeout(timer);
      triggerElementRef.current?.focus();
    };
  }, [isOpen]);

  // Keyboard navigation: Escape key listener and Tab focus trap
  useEffect(() => {
    if (!isOpen) return;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !cancelMutation.isPending) {
        onClose();
        return;
      }

      if (event.key === "Tab" && modalRef.current) {
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length > 0) {
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, cancelMutation.isPending, onClose]);

  if (!isOpen) return null;

  const filledQty = currentOrder.fill?.quantity ?? "0";
  const totalQty = currentOrder.instruction.quantity;
  const remainingQty =
    currentOrder.remaining_quantity ??
    (filledQty === "0" ? totalQty : "—");

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cancel-dialog-heading"
    >
      <div className="modal-content" ref={modalRef}>
        <div className="section-heading">
          <div>
            <h3 id="cancel-dialog-heading">Cancel Order</h3>
          </div>
          <button
            type="button"
            className="close-button"
            onClick={onClose}
            disabled={cancelMutation.isPending}
            aria-label="Close dialog"
          >
            ✕
          </button>
        </div>

        <div className="cancel-order-summary">
          <p>
            <strong>Account:</strong>{" "}
            <span>{currentOrder.account.label || currentOrder.account.id}</span>
          </p>
          <p>
            <strong>Symbol:</strong>{" "}
            <span>{currentOrder.instrument.symbol}</span>
          </p>
          <p>
            <strong>Instruction:</strong>{" "}
            <span>
              {currentOrder.instruction.side.toUpperCase()}{" "}
              {currentOrder.instruction.quantity} @{" "}
              {currentOrder.instruction.limit_price
                ? `$${currentOrder.instruction.limit_price}`
                : "MKT"}{" "}
              ({currentOrder.instruction.type.toUpperCase()})
            </span>
          </p>
          <p>
            <strong>Filled:</strong>{" "}
            <span>
              {filledQty}
              {currentOrder.fill?.average_price
                ? ` @ $${currentOrder.fill.average_price}`
                : ""}
            </span>
          </p>
          <p>
            <strong>Remaining:</strong> <span>{remainingQty}</span>
          </p>
          <p>
            <strong>Status:</strong>{" "}
            <span>
              {currentOrder.state} (Updated:{" "}
              {new Date(currentOrder.updated_at).toLocaleString()})
            </span>
          </p>
        </div>

        <div className="warning-callout" role="note">
          <strong>Warning:</strong> Filled shares cannot be undone. Canceling
          will only cancel the remaining unfilled shares.
        </div>

        {notice && (
          <p className="inline-alert" role="alert">
            {notice.message}
          </p>
        )}

        {isUnknown ? (
          <div className="unknown-outcome-section">
            <p className="inline-alert" role="alert">
              Cancellation outcome is unknown. Reconciliation is required.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="button-primary"
                ref={initialFocusRef}
                onClick={() => {
                  onReconcileRequested?.(currentOrder.id);
                  onClose();
                }}
              >
                Reconcile order
              </button>
              <button type="button" onClick={onClose}>
                Close
              </button>
            </div>
          </div>
        ) : currentOrder.can_cancel === false ? (
          <div className="modal-actions">
            <button
              type="button"
              ref={initialFocusRef}
              onClick={onClose}
            >
              Close
            </button>
          </div>
        ) : (
          <div className="modal-actions">
            <button
              type="button"
              ref={initialFocusRef}
              onClick={onClose}
              disabled={cancelMutation.isPending}
            >
              Keep order
            </button>
            <button
              type="button"
              className="button-danger"
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
            >
              {cancelMutation.isPending
                ? "Canceling…"
                : "Confirm cancellation"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
