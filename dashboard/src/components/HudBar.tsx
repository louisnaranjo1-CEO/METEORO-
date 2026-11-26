import { AlertOctagon, Pause, Play, Radar, SlidersHorizontal, Wallet } from "lucide-react";
import type { HudState } from "../types";
import { fmtSol, fmtUsd } from "../format";

interface HudBarProps {
  hud: HudState | null;
  busyAction: boolean;
  onTogglePause: () => void;
  onOpenSettings: () => void;
  onOpenWallet: () => void;
  onOpenPanic: () => void;
}

export function HudBar({
  hud,
  busyAction,
  onTogglePause,
  onOpenSettings,
  onOpenWallet,
  onOpenPanic,
}: HudBarProps) {
  const isLive = hud?.mode === "LIVE";
  const isPaused = hud?.is_paused ?? false;
  const totalLivePnlSol = hud?.total_live_pnl_sol ?? ((hud?.daily_pnl_sol ?? 0) + (hud?.unrealized_pnl_sol ?? 0));
  const totalLivePnlUsd = hud?.total_live_pnl_usd ?? ((hud?.daily_pnl_usd ?? 0) + (hud?.unrealized_pnl_usd ?? 0));
  const pnlColor =
    totalLivePnlSol > 0 ? "text-emerald-400" : totalLivePnlSol < 0 ? "text-rose-400" : "text-slate-200";
  const equitySol = hud?.equity_sol ?? hud?.available_sol ?? null;
  const equityUsd = hud?.equity_usd ?? hud?.available_usd ?? null;

  return (
    <header className="sticky top-0 z-30 border-b border-white/10 bg-black/75 backdrop-blur-2xl">
      <div className="max-w-[1600px] mx-auto px-3 sm:px-5 py-3 flex flex-wrap items-center justify-between gap-3">
        {/* Brand + Giant Mode Pill */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-2xl glass-subtle flex items-center justify-center text-cyan-300">
              <Radar className="w-5 h-5" />
            </div>
            <div className="leading-tight">
              <div className="text-xs font-semibold tracking-tight text-white">
                METEORO TRADER
              </div>
              <div className="text-[11px] text-zinc-400">
                Solana Judge Desk
              </div>
            </div>
          </div>

          <div
            className={`px-3.5 py-1.5 rounded-full border text-xs sm:text-sm font-medium tracking-tight flex items-center gap-2 backdrop-blur-xl ${
              isLive
                ? "bg-rose-500/15 border-rose-400/50 text-rose-200"
                : "bg-white/[0.06] border-emerald-400/50 text-emerald-200"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                isLive ? "bg-rose-400 animate-pulse" : "bg-emerald-400 animate-pulse"
              }`}
            />
            {isLive ? "REAL (DINERO EN VIVO)" : "TUTORIAL (SOL VIRTUAL)"}
          </div>

          {hud?.strategy_profile && (
            <span
              className={`px-2.5 py-1 rounded-full border text-[11px] font-semibold ${
                hud.strategy_profile === "AGRESIVO"
                  ? "border-amber-400/50 bg-amber-500/15 text-amber-200"
                  : "border-cyan-400/40 bg-cyan-500/10 text-cyan-200"
              }`}
            >
              {hud.strategy_profile === "AGRESIVO" ? "SNIPER AGRESIVO" : "CONSERVADOR"}
            </span>
          )}

          {hud?.use_llm_judge === false && (
            <span
              className="px-2.5 py-1 rounded-full border border-fuchsia-400/50 bg-fuchsia-500/15 text-fuchsia-200 text-[11px] font-semibold"
              title="Juez LLM apagado: motor matemático 0ms activo (Anti-Punta)"
            >
              ⚡ ALGO 0ms (SIN LLM)
            </span>
          )}

          {hud?.helius_connected && (
            <span className="px-2.5 py-1 rounded-full border border-emerald-400/35 bg-emerald-500/10 text-emerald-200 text-[11px] font-medium">
              Helius RPC
            </span>
          )}

          {hud?.telegram_bot_ready && (
            <span
              className={`px-2.5 py-1 rounded-full border text-[11px] font-medium ${
                hud.telegram_connected
                  ? "border-sky-400/40 bg-sky-500/15 text-sky-200"
                  : "border-amber-400/35 bg-amber-500/10 text-amber-200"
              }`}
              title={
                hud.telegram_connected
                  ? "Telegram conectado y enviando alertas"
                  : "Abre @TradingAlertLouisBot en Telegram y envía /start para vincular"
              }
            >
              {hud.telegram_connected ? "Telegram ON" : "Telegram: enviar /start"}
            </span>
          )}

          {hud?.semi_auto_mode && (
            <span className="px-2.5 py-1 rounded-full border border-amber-400/40 bg-amber-400/10 text-amber-200 text-[11px] font-medium">
              Semi-Auto
            </span>
          )}
        </div>

        {/* Live HUD Telemetry */}
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div className="rounded-2xl glass-subtle px-3 py-1.5">
              <div className="text-[10px] text-zinc-400">
                Banca en Vivo
              </div>
              <div className="font-mono text-xs sm:text-sm font-bold text-white tabular-nums">
                {equitySol !== null ? fmtSol(equitySol, 4) : "Wallet Live"}
              </div>
              <div className="font-mono text-[10px] text-zinc-400 tabular-nums">
                {equityUsd !== null ? fmtUsd(equityUsd) : "—"}
                {hud?.available_sol !== null && hud?.available_sol !== undefined && hud.open_trades > 0
                  ? ` · Libre ${hud.available_sol.toFixed(2)}`
                  : ""}
              </div>
            </div>

            <div className="rounded-2xl glass-subtle px-3 py-1.5">
              <div className="text-[10px] text-zinc-400">
                PnL en Vivo
              </div>
              <div className={`font-mono text-xs sm:text-sm font-bold tabular-nums ${pnlColor}`}>
                {fmtSol(totalLivePnlSol, 4, true)}
              </div>
              <div className="font-mono text-[10px] text-zinc-400 tabular-nums">
                {hud ? `${totalLivePnlUsd >= 0 ? "+" : ""}${fmtUsd(totalLivePnlUsd)}` : "—"}
                {hud && hud.open_trades > 0
                  ? ` · Ab ${hud.unrealized_pnl_sol >= 0 ? "+" : ""}${hud.unrealized_pnl_sol.toFixed(3)}`
                  : ""}
              </div>
            </div>

            <div className="rounded-2xl glass-subtle px-3 py-1.5">
              <div className="text-[10px] text-zinc-400">
                Trades Activos
              </div>
              <div className="font-mono text-xs sm:text-sm font-semibold text-emerald-300 tabular-nums">
                {hud ? `${hud.open_trades}/${hud.max_concurrent_trades}` : "—"}
              </div>
              <div className="font-mono text-[10px] text-zinc-400 tabular-nums">
                SL hoy: {hud ? `${hud.daily_sl_count}/${hud.max_consecutive_sl}` : "—"}
              </div>
            </div>

            <div className="rounded-2xl glass-subtle px-3 py-1.5">
              <div className="text-[10px] text-zinc-400">
                SOL / USD
              </div>
              <div className="font-mono text-xs sm:text-sm font-semibold text-white tabular-nums">
                {hud ? fmtUsd(hud.sol_usd) : "—"}
              </div>
              <div className="font-mono text-[10px] text-zinc-400 tabular-nums">
                Size: {hud ? `${hud.trade_size_sol} SOL` : "—"}
              </div>
            </div>
          </div>

          {/* Essential Controls Only */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={busyAction || !hud}
              onClick={onTogglePause}
              className={`px-3.5 py-2 rounded-xl border text-xs font-medium flex items-center gap-1.5 transition cursor-pointer disabled:opacity-40 backdrop-blur-xl ${
                isPaused
                  ? "bg-emerald-500/20 border-emerald-400/50 text-emerald-200 hover:bg-emerald-500/30"
                  : "bg-amber-500/15 border-amber-400/40 text-amber-200 hover:bg-amber-500/25"
              }`}
            >
              {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              <span>{isPaused ? "Reanudar" : "Pausar"}</span>
            </button>

            <button
              type="button"
              disabled={!hud}
              onClick={onOpenSettings}
              className="px-3.5 py-2 rounded-xl glass-subtle hover:bg-white/[0.08] text-zinc-100 text-xs font-medium flex items-center gap-1.5 transition cursor-pointer disabled:opacity-40"
            >
              <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-300" />
              <span className="hidden sm:inline">Ajustes</span>
            </button>

            <button
              type="button"
              disabled={!hud}
              onClick={onOpenWallet}
              className="px-3.5 py-2 rounded-xl border border-emerald-400/40 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-200 text-xs font-medium flex items-center gap-1.5 transition cursor-pointer disabled:opacity-40 backdrop-blur-xl"
            >
              <Wallet className="w-3.5 h-3.5 text-emerald-300" />
              <span className="hidden md:inline">Billetera Real</span>
              <span className="md:hidden">Wallet</span>
            </button>

            <button
              type="button"
              disabled={busyAction || !hud}
              onClick={onOpenPanic}
              className="px-3.5 py-2 rounded-xl border border-rose-500/45 bg-rose-500/15 hover:bg-rose-500/25 text-rose-200 text-xs font-medium flex items-center gap-1.5 transition cursor-pointer disabled:opacity-40 backdrop-blur-xl"
            >
              <AlertOctagon className="w-3.5 h-3.5 text-rose-400" />
              <span className="hidden md:inline">Pánico (Cerrar Todo)</span>
              <span className="md:hidden">Pánico</span>
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
