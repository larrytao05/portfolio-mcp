# Trading safety

## Sub-features

- `execution-readiness`: provider health, capabilities, monitoring-only and blocked accounts.
- `owner-safeguards`: blocked default state and explicit authority-change confirmation.
- `trade-review`: validation, draft details, expiry and quote context.
- `fake-confirmation`: guarded rejection/accepted/unknown fixture outcomes.
- `cancellation-recovery`: explicit confirmation, durable pending/unknown state and reconciliation after reload.

## How to get to it (user POV)

Click **Settings** or **Trade** in **Primary navigation**. Both open **Trading & settings**. The helper constructs fixture portfolio/quotes and the app's default fake execution provider, using a new database.

## Driving it with browser automation

1. Run doctor before changes to settings. Inspect **Execution status** and **Trading safeguards**. Confirm **Enable live trading** is unchecked, **Kill switch active** is checked, and **Trading blocked** is visible. Inspect monitoring-only/blocked reasons without changing controls.
2. Under Trade, choose **Trade account**, search **Trade instrument search** for `VTI`, click **Search trade instruments**, and select the VTI result. Exercise **Trade side**, **Trade type**, **Trade quantity**, and **Limit price** if shown. Invalid input must not create an executable draft.
3. With the safe baseline still blocked, confirm an attempted **Review fake order** reports a guard refusal. For draft/confirmation changes, use fixture-only API/component tests for successful review, confirmation, final safeguard rejection, expiry, unknown outcomes and reconciliation blocking. Record those as tests rather than browser proof.
4. Do not enable **Enable live trading** or clear the kill switch in this browser smoke run. The tests explicitly exercise settings and fake confirmation on isolated test databases. A separately requested fake end-to-end browser trade needs its own clear fixture-only setup and teardown; it does not authorize a real brokerage action.
5. Confirm settings and order actions still flow through the shared backend guard/services. For trading code changes, run the complete backend suite plus dashboard checks, and record relevant failure-boundary results.

## Separate fixture execution scenario

For requested execution changes, use a separate isolated fixture run after the
blocked smoke test. Record every synthetic setup change: the historical default
capability observation may be stale, so successful fake execution needs a current
fictional capability, account permission and fixture-only safeguard settings.
Setup is a precondition, not browser proof. Keep every provider fictional and
discard this database afterward; do not apply these changes to the user's app.

1. Through Trade, review a whole-share DAY limit order. Inspect account, side,
   quantity, price, quote context, expiry and **Confirm fake order**. Confirm once
   and observe the resulting order state. With a separately labeled UNKNOWN
   fixture scenario, reload and inspect **Unresolved orders for ACCOUNT**. Use
   **Reconcile order**; an absent/failed broker read must retain UNKNOWN. A failed
   saved-order request must show an error and tell the user not to resubmit.
2. In Market data, select VTI and **Orders**, then **Open Orders**. Use
   **Review cancellation**; confirm account/order identity. **Keep order** must
   leave it intact. **Confirm cancellation** must show the actual returned state.
3. Pending/unknown cancellation must remain under **Unresolved cancellations**
   after reload. Use **Reconcile cancellation** and inspect the result. Repeated
   requests must not issue another cancel. A terminal broker read resolves the
   block; an open/missing/failed read does not prove cancellation succeeded.
4. Capture desktop/mobile states and browser errors. API/fake-HTTP tests must
   separately prove live Alpaca account binding, fresh IEX execution quotes,
   dispatch-time expiry/safeguard checks, duplicate/concurrent protection and
   uncertain outcomes. A fake UI result does not prove live connectivity.

Live order reviews identify **LIVE Alpaca**, a masked account and limited IEX
quote coverage. Delayed SIP display data does not satisfy the market-order
freshness requirement. Never submit or cancel a real order in this workflow.

## Gotchas

- Doctor expects blocked safeguards; a modified safeguard state should fail doctor until the run is discarded and relaunched.
- SnapTrade monitoring and market-data health do not imply execution capability. Never call a provider directly to bypass a blocked UI/API action.
- Fake rejection/unknown results are meaningful outcomes. Never retry an unknown confirmation as a new order.
- This workflow supplies no live brokerage credentials and authorizes no real order, cancellation or trading enablement.
