import type {
  AuthStatus,
  BlacklistItem,
  CandleTimeframe,
  HudState,
  OhlcvResponse,
  PositionsResponse,
  RadarResponse,
  RejectedItem,
  SmartWalletDetailResponse,
  SmartWalletItem,
  StrategiesResponse,
  StrategyPreset,
  TokenDetailResponse,
  TradingMode,
  TutorialConfig,
  WalletStatusResponse,
  WithdrawalWallet,
  XFeedResponse,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore non-json error body
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  login: (pin: string) =>
    request<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ pin }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),

  hud: () => request<HudState>("/api/hud"),
  radar: () => request<RadarResponse>("/api/radar"),
  positions: (mode?: TradingMode) =>
    request<PositionsResponse>(mode ? `/api/positions?mode=${mode}` : "/api/positions"),
  rejected: () => request<{ items: RejectedItem[] }>("/api/rejected"),
  blacklist: () => request<{ items: BlacklistItem[] }>("/api/blacklist"),
  addBlacklist: (dev_wallet: string, reason: string, mint_address?: string) =>
    request<{ ok: boolean; item: BlacklistItem }>("/api/blacklist", {
      method: "POST",
      body: JSON.stringify({
        dev_wallet,
        reason,
        mint_address: mint_address || null,
      }),
    }),
  removeBlacklist: (wallet: string) =>
    request<{ ok: boolean }>(`/api/blacklist/${encodeURIComponent(wallet)}`, {
      method: "DELETE",
    }),

  smartWallets: () => request<{ items: SmartWalletItem[] }>("/api/smart-wallets"),
  smartWalletDetail: (address: string) =>
    request<SmartWalletDetailResponse>(`/api/smart-wallets/${encodeURIComponent(address)}`),
  addSmartWallet: (address: string, label: string) =>
    request<{ ok: boolean; items: SmartWalletItem[] }>("/api/smart-wallets", {
      method: "POST",
      body: JSON.stringify({ address, label }),
    }),
  removeSmartWallet: (address: string) =>
    request<{ ok: boolean; items: SmartWalletItem[] }>(
      `/api/smart-wallets/${encodeURIComponent(address)}`,
      { method: "DELETE" },
    ),

  xFeed: () => request<XFeedResponse>("/api/x-feed"),
  tokenDetail: (mint: string) =>
    request<TokenDetailResponse>(`/api/token/${encodeURIComponent(mint)}`),
  tokenOhlcv: (mint: string, tf: CandleTimeframe) =>
    request<OhlcvResponse>(`/api/token/${encodeURIComponent(mint)}/ohlcv?tf=${tf}`),

  togglePause: (paused?: boolean) =>
    request<{ ok: boolean; is_paused: boolean; breaker_reset?: boolean }>("/api/control/pause", {
      method: "POST",
      body: JSON.stringify(paused === undefined ? {} : { paused }),
    }),
  panicCloseAll: () =>
    request<{
      ok: boolean;
      closed: Array<{ id: number; symbol: string; pnl_sol: number }>;
      errors: Array<{ id: number; symbol: string; error: string }>;
      is_paused: boolean;
    }>("/api/control/panic-close-all", { method: "POST" }),
  approveLead: (leadId: number) =>
    request<{ ok: boolean; trade_id: number }>(`/api/control/approve/${leadId}`, {
      method: "POST",
    }),
  rejectLead: (leadId: number) =>
    request<{ ok: boolean }>(`/api/control/reject/${leadId}`, {
      method: "POST",
    }),
  exitTrade: (tradeId: number) =>
    request<{ ok: boolean; trade: { id: number; pnl_sol: number; pnl_usd: number } }>(
      `/api/control/exit-trade/${tradeId}`,
      { method: "POST" },
    ),

  updateTutorialConfig: (patch: Partial<TutorialConfig>) =>
    request<{ ok: boolean }>("/api/tutorial/config", {
      method: "POST",
      body: JSON.stringify(patch),
    }),
  resetTutorial: (initial_sol?: number) =>
    request<{ ok: boolean; tutorial_available_sol: number }>("/api/tutorial/reset", {
      method: "POST",
      body: JSON.stringify(initial_sol ? { initial_sol } : {}),
    }),
  switchMode: (mode: TradingMode) =>
    request<{ ok: boolean; mode: TradingMode }>("/api/mode/switch", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  strategies: () => request<StrategiesResponse>("/api/strategies"),
  applyStrategy: (id: string) =>
    request<{ ok: boolean; active_id: string; strategy: StrategyPreset; current_params: Record<string, number | boolean | string> }>(
      "/api/strategies/apply",
      { method: "POST", body: JSON.stringify({ id }) },
    ),
  saveStrategy: (body: { id?: string | null; name: string; description?: string; params: Record<string, number | boolean | string> }) =>
    request<{ ok: boolean; active_id: string; strategy: StrategyPreset; strategies: StrategyPreset[]; current_params: Record<string, number | boolean | string> }>(
      "/api/strategies/save",
      { method: "POST", body: JSON.stringify(body) },
    ),
  deleteStrategy: (id: string) =>
    request<{ ok: boolean; active_id: string; strategies: StrategyPreset[] }>(
      `/api/strategies/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),

  walletStatus: () => request<WalletStatusResponse>("/api/wallet/status"),
  configureWallet: (body: { private_key?: string; enable_live_execution?: boolean }) =>
    request<{ ok: boolean; configured: boolean; public_address: string | null; enable_live_execution: boolean }>(
      "/api/wallet/configure",
      { method: "POST", body: JSON.stringify(body) },
    ),
  generateWallet: () =>
    request<{ ok: boolean; configured: boolean; public_address: string; solscan_url: string }>(
      "/api/wallet/generate",
      { method: "POST" },
    ),
  addWithdrawalWallet: (address: string, label: string) =>
    request<{ ok: boolean; items: WithdrawalWallet[] }>("/api/wallet/withdraw-whitelist", {
      method: "POST",
      body: JSON.stringify({ address, label }),
    }),
  removeWithdrawalWallet: (address: string) =>
    request<{ ok: boolean; items: WithdrawalWallet[] }>(
      `/api/wallet/withdraw-whitelist/${encodeURIComponent(address)}`,
      { method: "DELETE" },
    ),
  withdrawSol: (destination: string, amount_sol: number) =>
    request<{ ok: boolean; signature: string; amount_sol: number; destination: string; destination_label: string; solscan_url: string }>(
      "/api/wallet/withdraw",
      { method: "POST", body: JSON.stringify({ destination, amount_sol }) },
    ),
};
