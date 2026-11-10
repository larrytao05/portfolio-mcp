# Trading safety

## Sub-features

- `execution-readiness`: provider health, capabilities, monitoring-only and blocked accounts.
- `owner-safeguards`: blocked default state and explicit authority-change confirmation.
- `trade-review`: validation, draft details, expiry and quote context.
- `fake-confirmation`: guarded rejection/accepted/unknown fixture outcomes.

## How to get to it (user POV)

Click **Settings** or **Trade** in **Primary navigation**. Both open **Trading & settings**. The helper constructs fixture portfolio/quotes and the app's default fake execution provider, using a new database.

## Driving it with browser automation

1. Run doctor before changes to settings. Inspect **Execution status** and **Trading safeguards**. Confirm **Enable live trading** is unchecked, **Kill switch active** is checked, and **Trading blocked** is visible. Inspect monitoring-only/blocked reasons without changing controls.
2. Under Trade, choose **Trade account**, search **Trade instrument search** for `VTI`, click **Search trade instruments**, and select the VTI result. Exercise **Trade side**, **Trade type**, **Trade quantity**, and **Limit price** if shown. Invalid input must not create an executable draft.
3. With the safe baseline still blocked, confirm an attempted **Review fake order** reports a guard refusal. For draft/confirmation changes, use fixture-only API/component tests for successful review, confirmation, final safeguard rejection, expiry, unknown outcomes and reconciliation blocking. Record those as tests rather than browser proof.
4. Do not enable **Enable live trading** or clear the kill switch in this browser smoke run. The tests explicitly exercise settings and fake confirmation on isolated test databases. A separately requested fake end-to-end browser trade needs its own clear fixture-only setup and teardown; it does not authorize a real brokerage action.
5. Confirm settings and order actions still flow through the shared backend guard/services. For trading code changes, run the complete backend suite plus dashboard checks, and record relevant failure-boundary results.

## Gotchas

- Doctor expects blocked safeguards; a modified safeguard state should fail doctor until the run is discarded and relaunched.
- SnapTrade monitoring and market-data health do not imply execution capability. Never call a provider directly to bypass a blocked UI/API action.
- Fake rejection/unknown results are meaningful outcomes. Never retry an unknown confirmation as a new order.
- This workflow supplies no live brokerage credentials and authorizes no real order, cancellation or trading enablement.
