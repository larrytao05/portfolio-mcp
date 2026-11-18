# Connections

Use a fresh fixture run. **Refresh portfolio** discovers its fictional accounts;
**Connections** in Primary navigation opens the account controls. Live provider
setup and portal authentication require separate evidence, never real credentials
in this workflow.

1. Confirm each account has its own label, connection state, inclusion state and
   trading permission. Inspect configured/unconfigured provider messages.
2. **Hide account** on one fixture account. Confirm current overview and positions
   exclude it, then reload. **Include account** must restore visibility without
   restoring trading permission. Stored historical records must remain intact.
3. **Disconnect provider** opens an inline confirmation naming every affected
   account. **Cancel** leaves the connection intact. **Confirm disconnect** hides
   the affected accounts and disables their permissions; verify after reload.
4. **Reconnect** restores fixture access. Refresh and verify totals and holdings
   return, with trading permission still disabled. For SnapTrade, reconnecting at
   the provider and **Verify and restore connection** are separate required steps;
   fixture-only browser runs cannot prove the external portal journey.
5. Collapse Connections and click its navigation link again; verify reopening.
   Inspect desktop/mobile wrapping and browser errors. Record screenshots.

Version conflicts, remote authorization revocation, malformed/cross-account
provider responses and failed refresh retention also have API tests. Report that
evidence separately from browser actions. Local disconnect does not revoke
provider access or cancel outstanding orders.
