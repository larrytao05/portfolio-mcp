import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { connectProvider, disconnectProvider, getConnections, getSnapTradePortal, reactivateConnection, updateAccountConnection, type ConnectionAccount } from "../../api/client";

export function ConnectionsSection() {
  const client = useQueryClient();
  const connections = useQuery({ queryKey: ["connections"], queryFn: getConnections });
  const [portal, setPortal] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);
  const refresh = async () => { await client.invalidateQueries(); };
  const connect = useMutation({ mutationFn: connectProvider, onSuccess: refresh });
  const reactivate = useMutation({ mutationFn: reactivateConnection, onSuccess: refresh, onError: refresh });
  const disconnect = useMutation({ mutationFn: disconnectProvider, onSuccess: async () => { setDisconnecting(null); await refresh(); } });
  const update = useMutation({ mutationFn: ({ account, change }: { account: ConnectionAccount; change: { included: boolean } | { trade_enabled: boolean } }) => updateAccountConnection(account, change), onSuccess: refresh, onError: refresh });
  const portalRequest = useMutation({ mutationFn: getSnapTradePortal, onSuccess: (result) => setPortal(result.url) });
  const configured = connections.data?.configured;
  return <section id="connections" aria-labelledby="connections-heading">
    <p className="kicker">Your accounts</p><h2 id="connections-heading">Connections</h2>
    <p>Connect existing accounts. Credentials stay with your provider or on this server.</p>
    {connections.isError && <p role="alert">Connections are unavailable.</p>}
    <div>
      <h3>Alpaca</h3><p>{configured?.alpaca ? "Server credentials configured" : "Not configured"}</p>
      <button type="button" disabled={!configured?.alpaca || connect.isPending} onClick={() => connect.mutate("alpaca")}>Connect Alpaca</button>
      <h3>Schwab and Fidelity</h3><p>{configured?.snaptrade ? "SnapTrade configured" : "SnapTrade not configured"}</p>
      <button type="button" disabled={!configured?.snaptrade || portalRequest.isPending} onClick={() => portalRequest.mutate(undefined)}>Open connection portal</button>
      {portal && <p><a href={portal} target="_blank" rel="noreferrer">Continue to SnapTrade</a></p>}
      {configured?.snaptrade && <button type="button" disabled={connect.isPending} onClick={() => connect.mutate("snaptrade")}>Find connected accounts</button>}
      {configured?.fixture && <button type="button" disabled={connect.isPending} onClick={() => connect.mutate("fixture")}>Connect fixture accounts</button>}
    </div>
    {(connect.isError || reactivate.isError || disconnect.isError || update.isError || portalRequest.isError) && <p role="alert">Connection action failed. Check your configuration and try again.</p>}
    {connections.data?.accounts.map((account) => <article key={account.account_id}>
      <h3>{account.label}</h3>
      <p>{account.active ? "Connected" : "Disconnected"} · {account.included ? "Included" : "Hidden"} · {account.trade_enabled ? "Account trading permission enabled" : "Account trading permission disabled"}</p>
      {account.active && <>
        <button type="button" disabled={update.isPending || disconnect.isPending} onClick={() => update.mutate({ account, change: { included: !account.included } })}>{account.included ? "Hide account" : "Include account"}</button>
        <button type="button" disabled={update.isPending || disconnect.isPending} onClick={() => update.mutate({ account, change: { trade_enabled: !account.trade_enabled } })}>{account.trade_enabled ? "Disable trading" : "Enable trading"}</button>
        <button type="button" disabled={update.isPending || disconnect.isPending} onClick={() => setDisconnecting(account.connection_id)}>Disconnect provider</button>
      </>}
      {!account.active && (account.provider_route === "snaptrade" ? <><button type="button" onClick={() => portalRequest.mutate(account.connection_id.replace(/^snaptrade:/, ""))}>Reconnect in SnapTrade</button><button type="button" disabled={reactivate.isPending} onClick={() => reactivate.mutate(account.connection_id)}>Verify and restore connection</button></> : <button type="button" onClick={() => connect.mutate(account.provider_route)}>Reconnect</button>)}
    </article>)}
    {disconnecting && <div role="group" aria-label="Confirm disconnect">
      <p>Disconnect this connection? These accounts will be hidden and their trading permissions disabled:</p>
      <ul>{connections.data?.accounts.filter((account) => account.connection_id === disconnecting).map((account) => <li key={account.account_id}>{account.label}</li>)}</ul>
      <p>Local access stops. This does not revoke provider access or cancel outstanding orders.</p>
      <button type="button" disabled={disconnect.isPending} onClick={() => disconnect.mutate(disconnecting)}>Confirm disconnect</button>
      <button type="button" disabled={disconnect.isPending} onClick={() => setDisconnecting(null)}>Cancel</button>
    </div>}
    <p>Disconnecting disables local access; it does not revoke access at the provider or cancel orders.</p>
  </section>;
}
