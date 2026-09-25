# Order history design

## Problem

Issue #48 adds durable order history and paged order reads while the existing `orders` rows remain authoritative for current state. The repository currently owns draft saves, order reservation, submission completion, and startup recovery in separate transactions; recovery bypasses the completion method, and provider fills are discarded. The design must record each accepted mutation atomically, include draft and authorization lifecycle events, and preserve existing orders without fabricating history.

## Caller view

Submission services continue to use repository-owned writes. The provider call remains outside the database transaction.

```python
order, created = repository.begin_order_submission(draft, now)
if not created:
    return order

result = await provider.submit_order(command)
return repository.finish_order_submission(
    order.id,
    result.state,
    observed_at,
    expected_version=order.version,
    result_code=safe_local_code(result),
    result_message=safe_message(result.state),
    fill=validated_fill(result.fill),
)
```

Issue #41 will translate HTTP parameters and cursors into repository query types:

```python
orders = repository.list_orders(query, limit=50, after=cursor)
events = repository.list_order_events(event_query, limit=100, after=event_cursor)
```

## Shape

`PortfolioRepository` owns state changes and their events in the same transaction. It exposes specific methods for draft creation, submission reservation/result, recovery, and first-observed expiry. Authorization creation and consumption are recorded in the reservation transaction; failed attempts use a dedicated method with a local attempt UUID and a finite refusal code. A private append helper serializes frozen event-specific detail types. No public generic event writer or caller-supplied transaction exists.

The event row contains a UUID event ID; nullable restrictive draft and order IDs; type; actor (`dashboard`, `mcp`, or `system`); nullable previous and next states; nullable safe code; versioned, bounded JSON details; and an aware UTC timestamp. Account scope is derived from known persisted rows and may be stored for filtering. Unknown supplied identifiers are never echoed into failure events. SQLite triggers reject event updates and deletes. Details are selected from an exhaustive finite schema, with no provider payloads, arbitrary messages, tokens, credentials, full brokerage account numbers, or stack traces.

`StoredOrder` remains the current-state read model. It gains nullable exact-decimal filled quantity and average fill price and a compact draft summary (draft ID, creation and expiry times, and reviewed instruction). It keeps its existing result code and fixed safe result message and makes their origin explicit as provider-observed or local. Existing order rows without events or fills remain readable as legacy data. Order detail joins its draft summary; state is never reconstructed by replaying events.

Repository pages use strict keyset predicates ordered by `(updated_at, order_id)` for orders and `(occurred_at, event_id)` for events. Limits are validated and capped. Cursors bind to their query filters. Event pages are deterministic for the stored event set; order pages are live views because updates can move a row across a cursor. Issue #41 owns opaque HTTP cursor encoding and refresh behavior.

The migration is linear after `20260922_0009`, adds the event table and indexes plus nullable fill columns, and backfills no events. Event types and repository contracts cover reconciliation attempts/results and cancellation request/results, but those future workflows remain out of issue #48 implementation scope.

## Synthesis decision

Candidate 1 is the base because it is the only candidate that includes nullable draft/order identity, the required actor vocabulary, draft and authorization lifecycle events, expiry, and reconciliation attempts. Its event ID is corrected from integer to UUID. Candidate 2's transition-version uniqueness is rejected because multiple events can share the submission reservation transaction and draft/auth events have no order version. Candidate 3's order-only event foreign key and omitted expiry are rejected because they contradict the ticket. Useful ideas retained from the other candidates are explicit optimistic version checks, exact-decimal fill snapshots, state/event rollback tests, and strict keyset paging.

## Tradeoffs accepted

- We accept a small set of operation-specific event writes in exchange for keeping transaction ownership inside the repository.
- We accept an event schema that is explicit and versioned in exchange for refusing arbitrary provider content.
- We accept live order pagination in exchange for keeping one authoritative current-state row; event pagination remains deterministic over a fixed stored set.
- We accept null fills and empty history for migrated orders in exchange for not inventing execution facts.

## Alternatives considered

- A separate event repository or public `append_event()` would force each service to coordinate event and state commits and could leave partial history.
- Event sourcing would make ordinary current reads depend on replay and require a fabricated baseline for legacy rows.
- A generic mapping for event details would expose data-safety policy to callers and permit accidental persistence of provider-controlled text.

## Implementation reconciliation

The live #48 acceptance criteria are the authority for event families and columns. UUID event IDs replace candidate 1's integer ID. The persisted event row has dedicated previous/next state and code fields; those values are not duplicated in JSON details. The optional account filter uses only the existing public internal account ID and never a full brokerage account number. The order read model includes the required draft summary and distinguishes provider-observed results from local validation/recovery outcomes. Event pagination uses the required `(occurred_at,event_id)` pair without claiming a frozen snapshot when a new event is inserted with an older timestamp.

## Risks and scope

Future reconciliation and cancellation code must use the finite typed event variants and commit state changes with their result events. Only their schemas and repository-level append contracts belong here. HTTP list routes, access policy, and dashboard polling belong to issue #41.

## Review follow-up

The quality review found that `database.py` grows by about 600 lines and recommended moving order persistence to a separate repository module. Keep the operations in `PortfolioRepository` for this ticket: draft/order state and audit events share its existing transaction boundary, and splitting them now would add a second repository plus delegation methods without changing that boundary. Reassess the module boundary when issue #54 adds reconciliation operations, so any extraction can follow the complete lifecycle instead of moving this slice alone.

The review also found the event detail allowlist repeated required and allowed fields in parallel maps. That is resolved by one schema entry per event type, with an exhaustive coverage check.

A second review found that provider outcomes reused the pre-call timestamp. Submission results and unknown outcomes now use the clock value observed after the provider returns or raises, and the repository clamps event time to the order's prior update time. A clock-advancing test covers both a successful result and a timeout.

## Next step

The repository implementation, migration, behavior tests, and static checks are complete. The remaining delivery step is the owner-approved commit and reviewable PR for issue #48.
