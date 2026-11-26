export type TradingMode = "TUTORIAL" | "LIVE";
export type StrategyProfile = "CONSERVADOR" | "AGRESIVO";

export interface AuthStatus {
  pin_required: boolean;
  authenticated: boolean;
}

export interface TutorialConfig {
  tutorial_initial_bankroll_sol: number;
  trade_size_sol: number;
  max_concurrent_trades?: number;
  sim_dex_fee_pct: number;
  sim_priority_fee_sol: number;
  sim_jito_tip_sol: number;
  sim_buy_slippage_pct: number;
  sim_tp_slippage_pct: number;
  sim_sl_slippage_pct: number;
  semi_auto_mode: boolean;
  strategy_profile?: StrategyProfile;
}

export interface HudState {
  mode: TradingMode;
  is_paused: boolean;
  semi_auto_mode: boolean;
  sol_usd: number;
  bankroll_sol: number;
  available_sol: number | null;
  available_usd: number | null;
  open_value_sol?: number;
  equity_sol?: number | null;
  equity_usd?: number | null;
  daily_pnl_sol: number;
  daily_pnl_usd: number;
  unrealized_pnl_sol: number;
  unrealized_pnl_usd: number;
  total_live_pnl_sol?: number;
  total_live_pnl_usd?: number;
  open_trades: number;
  max_concurrent_trades: number;
  daily_sl_count: number;
  max_consecutive_sl: number;
  pending_approvals: number;
  trade_size_sol: number;
  live_locks_ok: boolean;
  live_engine_available: boolean;
  bot_running: boolean;
  strategy_profile?: StrategyProfile;
  helius_connected?: boolean;
  telegram_connected?: boolean;
  telegram_bot_ready?: boolean;
  tp1_trigger_pct?: number;
  initial_stop_loss_pct?: number;
  disable_hard_stop_loss?: boolean;
  only_close_positive?: boolean;
  smart_be_arm_pct?: number;
  smart_be_floor_pct?: number;
  wick_harvest_arm_pct?: number;
  wick_pullback_exit_pct?: number;
  search_interval_seconds?: number;
  exit_poll_interval_seconds?: number;
  use_llm_judge?: boolean;
  tutorial_config: TutorialConfig;
  server_time: string;
}

export interface VerdictView {
  organic: "yes" | "no" | null;
  organic_prob: number | null;
  late: "yes" | "no" | null;
  late_prob: number | null;
  exit_risk: number | null;
  manipulation_trap: "yes" | "no" | null;
  trap_prob: number | null;
  action: "buy" | "watch" | "pass" | null;
  action_prob: number | null;
  gate_passed: boolean;
  kill_reason: string | null;
  created_at: string | null;
}

export interface RadarCardItem {
  id: number;
  mint_address: string;
  symbol: string;
  name: string;
  image_url?: string | null;
  stage: string;
  market_cap: number | null;
  liquidity_usd: number | null;
  main_pool_share: number | null;
  holders_count: number | null;
  price_usd: number | null;
  updated_at: string | null;
  verdict: VerdictView | null;
  approval_expires_at: string | null;
}

export interface RadarResponse {
  lanes: {
    SEARCH: RadarCardItem[];
    RISK_WHALE: RadarCardItem[];
    JUDGE: RadarCardItem[];
    POSITION: RadarCardItem[];
  };
  server_time: string;
}

export interface FlowSnapshot {
  volume_5m_usd?: number | null;
  buys_5m?: number | null;
  sells_5m?: number | null;
  liquidity_usd?: number | null;
  entry_liquidity_usd?: number | null;
  peak_volume_5m?: number | null;
}

export interface TradeEventView {
  id: number;
  event_type: string;
  sol_amount: number;
  tokens_amount: number;
  price_usd: number | null;
  effective_price_usd: number | null;
  fee_sol: number;
  slippage_sol: number;
  tx_signature: string | null;
  created_at: string;
}

export interface TradeView {
  id: number;
  lead_id: number | null;
  mint_address: string;
  symbol: string;
  image_url?: string | null;
  mode: TradingMode;
  status: "OPEN" | "TP1_HIT" | "RISK_FREE_RUNNER" | "CLOSED";
  invested_sol: number;
  invested_usd: number;
  entry_price_usd: number;
  effective_entry_price_usd: number;
  tokens_initial: number;
  tokens_remaining: number;
  fees_paid_sol: number;
  slippage_cost_sol: number;
  realized_sol: number;
  current_price_usd: number;
  highest_price_seen: number;
  peak_volume_5m: number;
  pnl_sol: number;
  pnl_usd: number;
  pnl_usd_live: number;
  pnl_pct: number;
  live_value_sol?: number;
  live_value_usd?: number;
  spot_move_pct?: number;
  tp_target_pct?: number;
  tp_progress_pct?: number;
  capital_recovered: boolean;
  exit_reason: string | null;
  opened_at: string;
  closed_at: string | null;
  flow: FlowSnapshot | null;
  events: TradeEventView[];
}

export interface CalibrationBucket {
  label: string;
  verdicts_count: number;
  closed_trades_count: number;
  wins: number;
  win_rate_pct: number;
  avg_pnl_pct: number;
  net_pnl_sol: number;
}

export interface PassAudit {
  total_pass_verdicts: number;
  evaluated_24h_count: number;
  died_or_dropped_50pct_count: number;
  saved_rate_pct: number;
}

export interface PositionsResponse {
  mode: TradingMode;
  sol_usd: number;
  open: TradeView[];
  closed: TradeView[];
  totals: {
    closed_count: number;
    wins: number;
    win_rate_pct: number;
    realized_pnl_sol: number;
    fees_sol: number;
    slippage_sol: number;
  };
  calibration: {
    buckets: CalibrationBucket[];
    pass_audit: PassAudit;
    period_days: number;
  };
}

export interface RejectedItem {
  id: number;
  mint_address: string;
  symbol: string;
  market_cap: number | null;
  liquidity_usd: number | null;
  dev_wallet: string | null;
  kill_reason: string | null;
  phase: string;
  verdict: VerdictView | null;
  updated_at: string | null;
}

export interface BlacklistItem {
  dev_wallet: string;
  reason: string;
  offending_mint: string | null;
  created_at: string;
}

export interface SmartWalletItem {
  address: string;
  label: string;
  added_at: string | null;
  category?: string | null;
  twitter_username?: string | null;
  twitter_name?: string | null;
  twitter_description?: string | null;
  avatar?: string | null;
  twitch?: string | null;
  sol_balance?: number | null;
  realized_profit_7d?: number | null;
  realized_profit_30d?: number | null;
  pnl_7d_pct?: number | null;
  pnl_30d_pct?: number | null;
  winrate_7d?: number | null;
  winrate_30d?: number | null;
  txs_30d?: number | null;
  buys_30d?: number | null;
  sells_30d?: number | null;
  hits_2x_5x_7d?: number | null;
  hits_gt_5x_7d?: number | null;
  avg_holding_sec_7d?: number | null;
  follow_count?: number | null;
  tags?: string[];
  source?: string | null;
  source_url?: string | null;
  snapshot_date?: string | null;
}

export interface SmartWalletDetailResponse {
  wallet: SmartWalletItem;
  live: {
    live_sol_balance: number | null;
    recent_txs: Array<{
      signature: string;
      block_time: number | null;
      err: boolean;
      solscan_tx_url: string;
    }>;
    fetched_at: string;
  };
  sol_usd: number;
  links: {
    solscan: string;
    gmgn: string;
    kolscan: string;
    cielo: string;
    arkham: string;
    twitter: string | null;
    twitch: string | null;
  };
}

export type CandleTimeframe = "1m" | "5m" | "15m";

export interface OhlcvCandle {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OhlcvResponse {
  mint_address: string;
  pair_address: string | null;
  tf: CandleTimeframe;
  candles: OhlcvCandle[];
  live_price: number | null;
}

export interface StrategyPreset {
  id: string;
  name: string;
  description: string;
  builtin: boolean;
  updated_at: string;
  params: Record<string, number | boolean | string>;
}

export interface StrategiesResponse {
  active_id: string;
  strategies: StrategyPreset[];
  current_params: Record<string, number | boolean | string>;
}

export interface WithdrawalWallet {
  address: string;
  label: string;
  added_at: string;
}

export interface WalletStatusResponse {
  configured: boolean;
  public_address: string | null;
  solscan_url: string | null;
  sol_balance: number | null;
  usd_balance: number | null;
  sol_usd: number;
  enable_live_execution: boolean;
  trading_mode: TradingMode;
  dual_lock_ready: boolean;
  max_live_trade_sol: number;
  min_live_gas_reserve_sol: number;
  withdrawal_wallets: WithdrawalWallet[];
}

export interface XMentionView {
  author_handle: string;
  author_followers: number;
  is_verified: boolean;
  is_notable_figure: boolean;
  notable_tag: string | null;
  text: string;
  url: string | null;
  fetched_at: string;
}

export interface XFeedGroup {
  mint_address: string;
  symbol: string;
  status: string | null;
  flow: FlowSnapshot | null;
  mentions: XMentionView[];
  notable_count: number;
}

export interface XFeedResponse {
  enabled: boolean;
  groups: XFeedGroup[];
  active_without_mentions: Array<{
    mint_address: string;
    symbol: string;
    status: string;
    flow: FlowSnapshot | null;
  }>;
}

export interface PricePoint {
  ts: string;
  price_usd: number;
  market_cap: number | null;
  liquidity_usd: number | null;
  vol_5m: number;
}

export interface LiveTokenMarket {
  price_usd: number | null;
  market_cap: number | null;
  fdv: number | null;
  liquidity_usd: number;
  total_liquidity_usd: number;
  main_pool_share: number | null;
  dex_id: string | null;
  pair_address: string | null;
  pair_url: string | null;
  pair_created_at: number | null;
  image_url: string | null;
  header_url: string | null;
  websites: Array<{ label: string; url: string }>;
  socials: Array<{ type: string; url: string }>;
  price_change: Record<"m5" | "h1" | "h6" | "h24", number | null>;
  volume: Record<"m5" | "h1" | "h6" | "h24", number | null>;
  txns: Record<"m5" | "h1" | "h6" | "h24", { buys: number; sells: number }>;
  fetched_at: string;
}

export interface SecurityAudit {
  available: boolean;
  mint_revoked?: boolean;
  freeze_revoked?: boolean;
  lp_locked_pct?: number;
  top10_pct?: number;
  dev_pct?: number;
  mutable_metadata?: boolean | null;
  transfer_fee_pct?: number;
  rugged?: boolean;
  rugcheck_score?: number | null;
  rugcheck_insider_flag?: boolean;
  insiders_detected?: number | null;
  insider_networks_count?: number;
  creator_tokens_count?: number | null;
  risks?: Array<{ name: string; level: string; value: string; description: string }>;
  thresholds: {
    lp_locked_min_pct: number;
    max_top10_pct: number;
    max_dev_pct: number;
    main_pool_min_pct: number;
    min_holders: number;
    min_liquidity_usd: number;
  };
}

export interface TokenDetailResponse {
  mint_address: string;
  symbol: string;
  name: string;
  lead: {
    id: number;
    stage: string;
    market_cap: number | null;
    liquidity_usd: number | null;
    main_pool_share: number | null;
    holders_count: number | null;
    dev_wallet: string | null;
    dev_blacklisted: boolean;
    price_usd: number | null;
    kill_reason: string | null;
    approval_expires_at: string | null;
    created_at: string | null;
    updated_at: string | null;
  } | null;
  live: LiveTokenMarket | null;
  security_audit?: SecurityAudit;
  smart_wallets_matched?: {
    count: number;
    total_tracked: number;
    checked_holders: number;
    wallets: Array<SmartWalletItem & { pct_held?: number | null }>;
  };
  price_history: PricePoint[];
  verdict: VerdictView | null;
  dossier: Record<string, string | number | boolean | null> | null;
  trade: {
    id: number;
    mode: TradingMode;
    status: string;
    invested_sol: number;
    invested_usd: number;
    entry_price_usd: number;
    effective_entry_price_usd: number;
    tokens_initial: number;
    tokens_remaining: number;
    fees_paid_sol: number;
    slippage_cost_sol: number;
    realized_sol: number;
    current_price_usd: number;
    highest_price_seen: number;
    peak_volume_5m: number;
    pnl_sol: number;
    pnl_usd: number;
    pnl_pct: number;
    capital_recovered: boolean;
    exit_reason: string | null;
    opened_at: string;
    closed_at: string | null;
    events: TradeEventView[];
  } | null;
  x_mentions: XMentionView[];
  server_time: string;
}
