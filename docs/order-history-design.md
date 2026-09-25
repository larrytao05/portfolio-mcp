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

The issue #54 review required the promised repository-boundary reassessment. Reconciliation claim, state update, and result-event append share a transaction with the existing order rows and event table. Extracting only reconciliation would introduce a second repository over the same tables and a forwarding seam, without moving transaction ownership or making the interface deeper. Extracting the complete order lifecycle would move draft, authorization, submission, recovery, reconciliation, and event persistence together; cancellation persistence is scheduled for #44 and belongs in that same boundary. Defer that broader extraction until #44, when the lifecycle boundary includes cancellation. Keep transaction-owning writes in `PortfolioRepository` through this ticket; do not add a second repository or generic event writer.

The issue #54 reconciliation service has no production caller in this PR. The Orders API and dashboard integration are assigned to #41, and the production Schwab read adapter is assigned to #47. The service has an injectable `OrderReadProvider` contract and is exercised against deterministic fake providers; it can be wired by #41 without changing reconciliation or persistence semantics. This sequencing keeps endpoint policy and provider translation in their planned tickets. The reconciliation path is not considered end-to-end until those integrations are complete.

Every `OrderReadProvider` implementation exposes client-ID lookup in its type, even if it does not support that query. `supports_client_order_id_lookup` decides whether the service calls it; unsupported providers use the bounded time-window query. A provider that reports no client-ID support can keep the method as an unused fail-closed stub. This keeps the base protocol narrow in method count, although a separate capability protocol could remove that stub requirement if more providers make it burdensome.

The review also found the event detail allowlist repeated required and allowed fields in parallel maps. That is resolved by one schema entry per event type, with an exhaustive coverage check.

A second review found that provider outcomes reused the pre-call timestamp. Submission results and unknown outcomes now use the clock value observed after the provider returns or raises, and the repository clamps event time to the order's prior update time. A clock-advancing test covers both a successful result and a timeout.

## Next step

The repository implementation, migration, behavior tests, and static checks are complete. The remaining delivery step is the owner-approved commit and reviewable PR for issue #48.
