import type { OrderListPage, RefreshOrderResult } from "../../api/client";

export type RefreshAttempt =
  | { kind: "observed"; result: RefreshOrderResult }
  | { kind: "busy" };

type RefreshLoopActions = {
  isActive(): boolean;
  loadPlan(): Promise<OrderListPage>;
  refresh(orderId: string): Promise<RefreshAttempt>;
  onRefresh(result: RefreshOrderResult): void;
  onError(): void;
};

type NextAction =
  | { kind: "reload"; at: number }
  | { kind: "refresh"; at: number; key: string; orderId: string };

function retryDelay(attempts: number): number {
  return Math.min(30_000, 2000 * 2 ** Math.min(attempts - 1, 4));
}

export function startOrderRefreshLoop(actions: RefreshLoopActions) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let planFailures = 0;
  let inFlight = false;
  let lastPlan: OrderListPage | undefined;
  const isStopped = () => stopped || !actions.isActive();
  const refreshFailures = new Map<string, { attempts: number; retryAt: number }>();

  function schedule(delay: number, action: () => Promise<void>) {
    if (isStopped()) return;
    timer = setTimeout(() => {
      timer = undefined;
      if (!isStopped()) void action();
    }, Math.max(1000, delay));
  }

  function schedulePlan(page: OrderListPage, receivedAt: number) {
    let next: NextAction | undefined = page.orders.some((order) => order.state === "SUBMITTING")
      ? { kind: "reload", at: receivedAt + 2000 }
      : undefined;
    const serverTime = Date.parse(page.server_time);
    for (const group of page.refresh_groups) {
      if (!group.target_order_id) continue;
      const remaining = Date.parse(group.next_refresh_at) - serverTime;
      if (!Number.isFinite(remaining)) continue;
      const key = JSON.stringify([group.provider, group.account_id]);
      const at = Math.max(
        receivedAt + Math.max(0, remaining),
        refreshFailures.get(key)?.retryAt ?? 0,
      );
      if (!next || at < next.at) {
        next = { kind: "refresh", at, key, orderId: group.target_order_id };
      }
    }
    if (!next) return;
    const selected = next;
    schedule(selected.at - performance.now(), () => selected.kind === "reload"
      ? loadAndSchedule()
      : refreshAndReload(selected));
  }

  async function loadAndSchedule(): Promise<void> {
    if (isStopped()) return;
    inFlight = true;
    let page: OrderListPage;
    try {
      page = await actions.loadPlan();
    } catch {
      if (isStopped()) return;
      inFlight = false;
      planFailures += 1;
      schedule(retryDelay(planFailures), loadAndSchedule);
      return;
    }
    if (isStopped()) return;
    inFlight = false;
    lastPlan = page;
    planFailures = 0;
    schedulePlan(page, performance.now());
  }

  async function refreshAndReload(action: Extract<NextAction, { kind: "refresh" }>): Promise<void> {
    if (isStopped()) return;
    inFlight = true;
    try {
      const attempt = await actions.refresh(action.orderId);
      if (isStopped()) return;
      if (attempt.kind === "observed") {
        refreshFailures.delete(action.key);
        actions.onRefresh(attempt.result);
      }
    } catch {
      if (isStopped()) return;
      const attempts = (refreshFailures.get(action.key)?.attempts ?? 0) + 1;
      refreshFailures.set(action.key, {
        attempts,
        retryAt: performance.now() + retryDelay(attempts),
      });
      actions.onError();
    }
    if (isStopped()) return;
    await loadAndSchedule();
  }

  void loadAndSchedule();
  return {
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
    wake(page: OrderListPage) {
      if (isStopped() || inFlight || page === lastPlan) return;
      clearTimeout(timer);
      timer = undefined;
      void loadAndSchedule();
    },
  };
}
