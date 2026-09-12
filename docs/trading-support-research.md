# Trading support research

Researched 2026-09-09. This note covers what the current public SnapTrade
documentation says about programmatic order execution; it is not investment,
tax, or legal advice.

## Bottom line for the current connections

### Schwab

**Yes, but the existing read-only connection cannot simply start placing
orders.** SnapTrade lists a separate **Schwab Trading** integration. It says
that live trading requires the customer's own Schwab *Commercial* API keys,
that every account which will trade must have Thinkorswim enabled, and that
the required Schwab products are Accounts and Trading—Commercial Production
and Market Data—Commercial Production. The required callback URL is
`https://connect.snaptrade.com/oauth/callback`. SnapTrade also says that trade
preview/order impact is unavailable for this integration and its order-history
endpoint must not be queried more frequently than every 30 seconds.

Sources: [Schwab Trading integration](https://support.snaptrade.com/Schwab-Trading-942feaa69a1c830fac4401fb22fbbfc3?pvs=21), [Schwab Trader API Commercial application](https://developer.schwab.com/products/trader-api--commercial), and [Schwab read-only integration](https://support.snaptrade.com/Schwab-Read-Only-4a4feaa69a1c831b9dda0190145c6dd9?pvs=21).

Practical implication: keep the present Schwab connection read-only until the
commercial approval and broker prerequisites are complete. Then create or
re-authorize a trading-capable connection and verify the account's eligibility
with a deliberately small, manually approved order. The generic SnapTrade
documentation supports Personal API-key trading where enabled, but the
broker-specific Schwab page is more restrictive, so its Commercial-key
requirement should govern this project.

### Fidelity

**No current SnapTrade execution route is documented.** Fidelity is listed as
a generally available data integration, but its institution page describes
daily data (with holdings delayed up to 24 hours) and executed-order history
only. It does not describe a trade-enabled connection or trading prerequisites.
Fidelity also does not appear in SnapTrade's current Equity Trading Support
table, which names the integrations for which SnapTrade publishes execution
capabilities.

Sources: [Fidelity integration](https://support.snaptrade.com/Fidelity-ad8feaa69a1c83eca04301cfac6e5071?pvs=21) and [SnapTrade institution and equity-trading support matrix](https://support.snaptrade.com/brokerages).

This is evidence about SnapTrade support, not a claim that Fidelity can never
offer an API by some other arrangement. Before designing around Fidelity
automation, request written confirmation from SnapTrade/Fidelity; until then,
treat this account as monitoring-only.

## How SnapTrade access works

A Personal API key can trade only the owner's accounts and only when the key,
brokerage, connection, and account have trading enabled. OAuth sharing is
currently read-only. A trading connection must be made with
`connectionType=trade-if-available` rather than the default `read`. Commercial
integrations can also trade where enabled, but need a production key and
broker/account eligibility.

Sources: [Getting started](https://docs.snaptrade.com/docs/getting-started), [Personal vs. Commercial](https://docs.snaptrade.com/docs/personal-vs-commercial), and [authentication methods](https://docs.snaptrade.com/docs/authentication-methods).

For equity orders, the safer normal sequence is: check order impact, present a
confirmation to the user, then place the resulting checked order. A checked
trade expires after five minutes. Some broker integrations, including Schwab,
do not support the preview step, so an executor must explicitly model that
exception rather than silently force an order.

Sources: [check equity order impact](https://docs.snaptrade.com/reference/Trading/Trading_getOrderImpact) and [place checked equity order](https://docs.snaptrade.com/reference/Trading/Trading_placeOrder).

## SnapTrade integrations with published equity execution support

SnapTrade's current support matrix explicitly lists these as supporting equity
trading: **Alpaca (and Alpaca Paper), E*Trade, Moomoo, Public, Schwab Trading,
tastytrade, TradeStation (and TradeStation Paper), Tradier, Trading 212 (and
Trading 212 Practice), Wealthsimple, Webull, and Webull Canada.** The matrix
also gives the order types, sizing, sessions, preview support, and whether an
integration supports idempotent orders. It is the source of truth because these
capabilities vary by broker and can change.

Source: [SnapTrade equity-trading support matrix](https://support.snaptrade.com/brokerages).

For a US-based, local project that needs a taxable account and Roth IRA, the
most practical **test** candidate is Alpaca: SnapTrade lists its live and Paper
integrations, real-time data, Roth and Traditional IRAs, fractional/dollar
orders, extended-hours support, order preview, and idempotent orders. It
requires the user's own Alpaca key; SnapTrade says copy-trading apps are not
eligible unless RIA licensed. This is an implementation-fit inference from
the published capabilities, not a recommendation to open an account.

Sources: [Alpaca integration](https://support.snaptrade.com/Alpaca-09ffeaa69a1c8354a7f681bf20cf3e46?pvs=21) and [SnapTrade equity-trading support matrix](https://support.snaptrade.com/brokerages).

Other relevant constraints visible in the broker pages:

- TradeStation and Tradier require the user's own broker API key; their pages
  say production enablement involves broker/API-key approval. Tradier's page
  currently states a one-time $500 charge.
- Webull supports taxable and IRA account types but excludes cash accounts from
  trading; its page also documents fractional/notional constraints.
- Moomoo has a 30-day connection lifetime, requiring periodic re-login.
- E*Trade supports Roth and Traditional IRAs, but its API has per-connection
  throttling and an immediate-execution caveat for limit-order webhooks.

Sources: [TradeStation](https://support.snaptrade.com/TradeStation-bd1feaa69a1c82d3829801a316d8d98d?pvs=21), [Tradier](https://support.snaptrade.com/Tradier-e2efeaa69a1c83458a498141b6b4bfd9?pvs=21), [Webull](https://support.snaptrade.com/Webull-e1afeaa69a1c839ba37c0134f951baa2?pvs=21), [Moomoo](https://support.snaptrade.com/Moomoo-e33feaa69a1c836f891381bb17911594?pvs=21), and [E*Trade](https://support.snaptrade.com/E-Trade-b9afeaa69a1c82dfb1d881827c0d0e52?pvs=21).

## Recommended decision path

1. Keep Fidelity in the read-only adapter.
2. Ask Schwab/SnapTrade whether an individual, local Personal project can
   obtain the required Commercial Schwab credentials and have its Roth account
   enrolled in Thinkorswim. Do not change the live connection before that is
   confirmed.
3. Add a separate `ExecutionProvider` contract instead of adding write methods
   to the existing read-only `PortfolioProvider`. Begin with a dry-run and
   persistent approval/audit record.
4. Use SnapTrade Sandbox only for read-path and error-handling tests. It is
   available on non-production Personal and Commercial test keys, but is
   explicitly read-only and cannot place or cancel trades.
5. Exercise an execution flow with a broker-specific paper integration, such
   as Alpaca Paper, before considering a live order path. If Schwab approval
   is impractical, evaluate Alpaca Paper as the first
   execution test target. Only after durable idempotency, limits, confirmations,
   reconciliation, and kill-switch behavior are tested should any live broker
   be enabled.

Source for the sandbox: [SnapTrade Sandbox](https://docs.snaptrade.com/docs/sandbox).
