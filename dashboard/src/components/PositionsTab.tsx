import { useState } from "react";
import { Activity, Award, LogOut, ShieldCheck, Sparkles, TrendingUp } from "lucide-react";
import type { PositionsResponse, TradingMode, TradeView } from "../types";
import { fmtAgo, fmtPct, fmtSol, fmtUsd, shortAddr } from "../format";

interface PositionsTabProps {
  data: PositionsResponse | null;
  selectedMode: TradingMode;
  busyTradeId: number | null;
  onSelectMode: (mode: TradingMode) => void;
  onSelectToken: (mint: string) => void;
  onExitTrade: (tradeId: number) => Promise<void>;
}

export function PositionsTab({
  data,
  selectedMode,
  busyTradeId,
  onSelectMode,
  onSelectToken,
  onExitTrade,
}: PositionsTabProps) {
  const openTrades = data?.open ?? [];
  const closedTrades = data?.closed ?? [];
  const totals = data?.totals;

  return (
    <div className="space-y-6">
      {/* Mode filter + Totals Strip */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {(["TUTORIAL", "LIVE"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onSelectMode(m)}
              className={`px-3 py-1.5 rounded-lg font-mono text-xs font-bold border transition cursor-pointer ${
                selectedMode === m
                  ? m === "LIVE"
                    ? "bg-rose-500/20 border-rose-500 text-rose-200"
                    : "bg-cyan-500/20 border-cyan-400 text-cyan-200"
                  : "border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200"
              }`}
            >
              {m === "TUTORIAL" ? "HISTORIAL TUTORIAL" : "HISTORIAL REAL (LIVE)"}
            </button>
          ))}
        </div>

        {totals && (
          <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
            <span className="px-2.5 py-1 rounded-lg border border-slate-800 bg-slate-900/70 text-slate-300">
              Cerrados: <b>{totals.closed_count}</b> ({totals.win_rate_pct}% WR)
            </span>
            <span
              className={`px-2.5 py-1 rounded-lg border border-slate-800 bg-slate-900/70 ${
                totals.realized_pnl_sol >= 0 ? "text-emerald-400" : "text-rose-400"
              }`}
            >
              Realizado: <b>{fmtSol(totals.realized_pnl_sol, 4, true)}</b>
            </span>
            <span className="px-2.5 py-1 rounded-lg border border-slate-800 bg-slate-900/70 text-slate-400">
              Fees+Slip: {fmtSol(totals.fees_sol + totals.slippage_sol, 4)}
            </span>
          </div>
        )}
      </div>

      {/* Open Positions */}
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-emerald-400" />
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">
            Posiciones Abiertas ({openTrades.length})
          </h2>
        </div>

        {openTrades.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-900/30 p-8 text-center text-xs text-slate-500">
            No hay posiciones abiertas en modo {selectedMode}.
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {openTrades.map((t) => (
              <TradeCard
                key={t.id}
                trade={t}
                busy={busyTradeId === t.id}
                onSelect={() => onSelectToken(t.mint_address)}
                onExit={() => onExitTrade(t.id)}
              />
            ))}
          </div>
        )}
      </section>

      {/* Judge Calibration Table */}
      {data && (
        <section className="rounded-2xl border border-slate-800 bg-slate-900/55 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <Award className="w-4 h-4 text-violet-400" />
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100">
                Calibración del Juez ({data.calibration.period_days} días)
              </h2>
            </div>
            <div className="font-mono text-xs text-slate-400">
              PASS evaluados 24h:{" "}
              <span className="text-slate-200 font-semibold">
                {data.calibration.pass_audit.died_or_dropped_50pct_count}/
                {data.calibration.pass_audit.evaluated_24h_count}
              </span>{" "}
              cayeron &ge;50% ({data.calibration.pass_audit.saved_rate_pct}% acierto defensivo)
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                  <th className="py-2 pr-4">Confianza BUY</th>
                  <th className="py-2 pr-4">Veredictos</th>
                  <th className="py-2 pr-4">Cerrados</th>
                  <th className="py-2 pr-4">Win Rate Real</th>
                  <th className="py-2 pr-4">Avg PnL %</th>
                  <th className="py-2">Neto SOL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {data.calibration.buckets.map((b) => (
                  <tr key={b.label} className="text-slate-200">
                    <td className="py-2.5 pr-4 font-bold text-violet-300">{b.label}</td>
                    <td className="py-2.5 pr-4 tabular-nums">{b.verdicts_count}</td>
                    <td className="py-2.5 pr-4 tabular-nums">{b.closed_trades_count}</td>
                    <td className="py-2.5 pr-4 tabular-nums">
                      <span
                        className={
                          b.win_rate_pct >= 50 ? "text-emerald-400 font-semibold" : "text-slate-300"
                        }
                      >
                        {b.win_rate_pct.toFixed(1)}%
                      </span>
                    </td>
                    <td
                      className={`py-2.5 pr-4 tabular-nums ${
                        b.avg_pnl_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {fmtPct(b.avg_pnl_pct, true)}
                    </td>
                    <td
                      className={`py-2.5 tabular-nums font-semibold ${
                        b.net_pnl_sol >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {fmtSol(b.net_pnl_sol, 4, true)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Closed Trades History */}
      <section className="space-y-3">
        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-400">
          Historial de Operaciones Cerradas ({closedTrades.length})
        </h2>
        {closedTrades.length === 0 ? (
          <div className="rounded-xl border border-slate-800/70 bg-slate-900/30 p-6 text-center text-xs text-slate-500">
            Aún no hay operaciones cerradas en modo {selectedMode}.
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {closedTrades.map((t) => (
              <TradeCard
                key={t.id}
                trade={t}
                busy={false}
                onSelect={() => onSelectToken(t.mint_address)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function TradeCard({
  trade,
  busy,
  onSelect,
  onExit,
}: {
  trade: TradeView;
  busy: boolean;
  onSelect: () => void;
  onExit?: () => Promise<void>;
}) {
  const [imgErr, setImgErr] = useState(false);
  const isOpen = trade.status !== "CLOSED";
  const isRunner = trade.capital_recovered || trade.status === "RISK_FREE_RUNNER";
  const pnlPos = trade.pnl_sol >= 0;
  const tpTarget = trade.tp_target_pct ?? 18;
  const tpProgress =
    trade.tp_progress_pct ??
    (trade.pnl_pct > 0 && tpTarget > 0
      ? Math.min(1000, Math.round((trade.pnl_pct / tpTarget) * 1000) / 10)
      : 0);
  const hitTp = isOpen && (tpProgress >= 100 || trade.status === "TP1_HIT" || isRunner);
  const isNearTp = isOpen && !hitTp && tpProgress >= 60;

  // Dynamic real-time card aura: Red (negative) -> Emerald (positive/approaching TP) -> Electric Bright Blue (TP hit!)
  const cardTone = !isOpen
    ? "border-white/10 opacity-90"
    : hitTp
    ? "tp-hit-electric"
    : pnlPos
    ? isNearTp
      ? "border-emerald-300/80 bg-emerald-500/15 shadow-[0_0_28px_-6px_rgba(52,211,153,0.45)]"
      : "border-emerald-400/55 bg-emerald-950/25 shadow-[0_0_20px_-8px_rgba(16,185,129,0.35)]"
    : trade.pnl_pct <= -15
    ? "border-rose-500/75 bg-rose-950/35 shadow-[0_0_26px_-8px_rgba(244,63,94,0.45)]"
    : "border-rose-500/45 bg-rose-950/20 shadow-[0_0_18px_-8px_rgba(244,63,94,0.28)]";

  const pnlAccent = hitTp
    ? "text-sky-300"
    : pnlPos
    ? "text-emerald-400"
    : "text-rose-400";

  const currentVol = trade.flow?.volume_5m_usd ?? null;
  const peakVol = Math.max(trade.peak_volume_5m || 0, currentVol || 0);
  const volHealthPct =
    currentVol !== null && peakVol > 0 ? Math.min(100, Math.round((currentVol / peakVol) * 100)) : null;

  const barFillPct = Math.min(100, Math.max(0, tpProgress));

  return (
    <article
      onClick={onSelect}
      className={`rounded-3xl glass-card p-4 space-y-3.5 cursor-pointer transition-all duration-300 ${cardTone}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          {trade.image_url && !imgErr ? (
            <img
              src={trade.image_url}
              alt={trade.symbol}
              onError={() => setImgErr(true)}
              className="h-11 w-11 rounded-2xl border border-white/15 object-cover bg-black shrink-0"
            />
          ) : (
            <div className="h-11 w-11 rounded-2xl border border-white/15 bg-white/[0.06] flex items-center justify-center font-mono text-xs font-bold text-zinc-200 shrink-0">
              {trade.symbol.slice(0, 3)}
            </div>
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-base font-bold text-white">
                ${trade.symbol}
              </span>
              {hitTp && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-sky-400/25 border border-sky-300/70 text-sky-100 text-[10px] font-bold animate-pulse">
                  <Sparkles className="w-3 h-3" /> TP ALCANZADO
                </span>
              )}
              {isRunner && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/20 border border-emerald-400/50 text-emerald-200 text-[10px] font-bold">
                  <ShieldCheck className="w-3 h-3" /> RUNNER LIBRE
                </span>
              )}
              {!isOpen && trade.exit_reason && (
                <span className="px-2 py-0.5 rounded-md bg-white/10 text-zinc-300 text-[10px]">
                  {trade.exit_reason}
                </span>
              )}
            </div>
            <div className="text-[11px] text-zinc-400 mt-0.5">
              {shortAddr(trade.mint_address, 5, 4)} · {trade.status} · {fmtAgo(trade.opened_at)}
            </div>
          </div>
        </div>

        {/* BIG Live PnL Display (SOL, USD, %) */}
        <div className="text-right shrink-0">
          <div className={`text-xl sm:text-2xl font-extrabold leading-tight tabular-nums ${pnlAccent}`}>
            {fmtSol(trade.pnl_sol, 4, true)}
          </div>
          <div className={`text-xs sm:text-sm font-bold tabular-nums ${pnlAccent}`}>
            {trade.pnl_usd_live >= 0 ? "+" : ""}
            {fmtUsd(trade.pnl_usd_live)} ({fmtPct(trade.pnl_pct, true)})
          </div>
        </div>
      </div>

      {/* Live Progress to TP (0% -> 100% at TP -> up to 1000%) */}
      {isOpen && (
        <div className="rounded-2xl border border-white/10 bg-black/45 px-3 py-2.5 space-y-1.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-zinc-300 font-medium">
              Meta TP (+{tpTarget}%) ·{" "}
              <span className={pnlAccent}>
                {hitTp
                  ? "En zona TP / Runner"
                  : pnlPos
                  ? "Acercándose al TP"
                  : "En espera (sin SL negativo)"}
              </span>
            </span>
            <span className={`text-sm font-extrabold tabular-nums ${pnlAccent}`}>
              {tpProgress.toFixed(0)}%
            </span>
          </div>
          <div className="h-2 w-full rounded-full bg-white/10 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                hitTp
                  ? "bg-gradient-to-r from-cyan-400 via-sky-400 to-blue-400"
                  : pnlPos
                  ? "bg-gradient-to-r from-emerald-500 to-emerald-300"
                  : "bg-gradient-to-r from-rose-600 to-rose-400"
              }`}
              style={{
                width: `${pnlPos ? Math.max(4, barFillPct) : Math.min(100, Math.abs(trade.pnl_pct))}%`,
              }}
            />
          </div>
        </div>
      )}

      {/* Accounting Breakdown with Live Current Value */}
      <div className="grid grid-cols-3 gap-2 rounded-2xl border border-white/10 bg-black/45 p-2.5 text-[11px]">
        <div>
          <div className="text-[10px] text-zinc-400">INVERTIDO</div>
          <div className="text-white font-semibold tabular-nums">{trade.invested_sol.toFixed(4)} SOL</div>
          <div className="text-[10px] text-zinc-400 tabular-nums">{fmtUsd(trade.invested_usd)}</div>
        </div>
        <div>
          <div className="text-[10px] text-zinc-400">{isOpen ? "VALOR EN VIVO" : "REALIZADO"}</div>
          <div className={`font-bold tabular-nums ${pnlAccent}`}>
            {(trade.live_value_sol ?? trade.realized_sol).toFixed(4)} SOL
          </div>
          <div className="text-[10px] text-zinc-400 tabular-nums">
            {fmtUsd(trade.live_value_usd ?? trade.realized_sol)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-zinc-400">FEES + SLIP</div>
          <div className="text-amber-300 font-semibold tabular-nums">
            {(trade.fees_paid_sol + trade.slippage_cost_sol).toFixed(4)} SOL
          </div>
          <div className="text-[10px] text-zinc-400 tabular-nums">
            {trade.tokens_initial > 0
              ? `${Math.round((trade.tokens_remaining / trade.tokens_initial) * 100)}% rem`
              : "0% rem"}
          </div>
        </div>
      </div>

      {/* Mini Volume Health Meter */}
      {isOpen && (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[11px]">
            <span className="flex items-center gap-1 text-zinc-400">
              <Activity className="w-3 h-3 text-cyan-400" /> Salud de Volumen (5m vs Pico)
            </span>
            <span className="text-zinc-200 tabular-nums">
              {volHealthPct !== null
                ? `${volHealthPct}% (${fmtUsd(currentVol, true)} / ${fmtUsd(peakVol, true)})`
                : `Pico ${fmtUsd(peakVol, true)}`}
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-white/10 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                volHealthPct === null
                  ? "bg-zinc-600"
                  : volHealthPct >= 50
                  ? "bg-emerald-400"
                  : volHealthPct >= 35
                  ? "bg-amber-400"
                  : "bg-rose-500"
              }`}
              style={{ width: `${volHealthPct ?? 15}%` }}
            />
          </div>
        </div>
      )}

      {/* Partial Events + Single Manual Exit Button */}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <div className="flex flex-wrap gap-1">
          {trade.events.map((ev) => (
            <span
              key={ev.id}
              className="px-2 py-0.5 rounded-md bg-white/[0.08] text-[10px] text-zinc-200"
            >
              {ev.event_type}
            </span>
          ))}
        </div>

        {isOpen && onExit && (
          <button
            type="button"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void onExit();
            }}
            className="px-3 py-1.5 rounded-xl border border-rose-500/50 bg-rose-950/60 hover:bg-rose-600/30 text-rose-200 text-xs font-bold flex items-center gap-1.5 transition cursor-pointer disabled:opacity-40"
          >
            <LogOut className="w-3.5 h-3.5" />
            {busy ? "Saliendo..." : "SALIR 100%"}
          </button>
        )}
      </div>
    </article>
  );
}
