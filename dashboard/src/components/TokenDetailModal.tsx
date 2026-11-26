import { useEffect, useRef, useState } from "react";
import {
  Activity,
  BadgeCheck,
  Ban,
  Check,
  CheckCircle2,
  Copy,
  Cpu,
  ExternalLink,
  Fish,
  Globe,
  HelpCircle,
  LogOut,
  ShieldCheck,
  Sparkles,
  X,
  XCircle,
} from "lucide-react";
import { api } from "../api";
import { fmtAgo, fmtFollowers, fmtPct, fmtProb, fmtSol, fmtUsd, shortAddr } from "../format";
import type { CandleTimeframe, OhlcvCandle, PricePoint, SmartWalletItem, TokenDetailResponse } from "../types";
import { SmartWalletModal } from "./SmartWalletModal";

interface TokenDetailModalProps {
  mint: string | null;
  onClose: () => void;
  onApprove?: (leadId: number) => Promise<void>;
  onReject?: (leadId: number) => Promise<void>;
  onExitTrade?: (tradeId: number) => Promise<void>;
}

const TOKEN_POLL_MS = 250; // 0.25s real-time chart & metrics update

function fmtTokenPrice(px: number | null | undefined): string {
  if (px === null || px === undefined || Number.isNaN(px) || px <= 0) return "—";
  if (px >= 1) return `$${px.toFixed(4)}`;
  if (px >= 0.01) return `$${px.toFixed(5)}`;
  if (px >= 0.0001) return `$${px.toFixed(7)}`;
  return `$${px.toFixed(9)}`;
}

export function TokenDetailModal({
  mint,
  onClose,
  onApprove,
  onReject,
  onExitTrade,
}: TokenDetailModalProps) {
  const [data, setData] = useState<TokenDetailResponse | null>(null);
  const [timeframe, setTimeframe] = useState<CandleTimeframe>("1m");
  const [candles, setCandles] = useState<OhlcvCandle[]>([]);
  const [selectedWallet, setSelectedWallet] = useState<SmartWalletItem | null>(null);
  const [copied, setCopied] = useState(false);
  const [imgFailed, setImgFailed] = useState(false);
  const [tickClass, setTickClass] = useState<"" | "tick-up" | "tick-down">("");
  const prevPriceRef = useRef<number | null>(null);
  const inFlightRef = useRef(false);
  const ohlcvInFlightRef = useRef(false);

  useEffect(() => {
    setImgFailed(false);
    if (!mint) {
      setData(null);
      setCandles([]);
      prevPriceRef.current = null;
      return;
    }

    let cancelled = false;
    const poll = async () => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      try {
        const res = await api.tokenDetail(mint);
        if (cancelled) return;
        const livePx = res.live?.price_usd ?? res.lead?.price_usd ?? null;
        if (livePx !== null && prevPriceRef.current !== null && livePx !== prevPriceRef.current) {
          setTickClass(livePx > prevPriceRef.current ? "tick-up" : "tick-down");
          window.setTimeout(() => setTickClass(""), 450);
        }
        prevPriceRef.current = livePx;
        setData(res);
        // Immediately reflect live 0.25s tick onto rightmost candle
        if (livePx !== null && livePx > 0) {
          setCandles((prev) => {
            if (prev.length === 0) return prev;
            const next = prev.slice();
            const last = { ...next[next.length - 1] };
            last.close = livePx;
            if (livePx > last.high) last.high = livePx;
            if (livePx < last.low) last.low = livePx;
            next[next.length - 1] = last;
            return next;
          });
        }
      } catch {
        // keep last valid snapshot
      } finally {
        inFlightRef.current = false;
      }
    };

    void poll();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void poll();
    }, TOKEN_POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [mint]);

  // Fetch real OHLCV Japanese candlesticks (1m / 5m / 15m) every 3s + immediately on TF switch
  useEffect(() => {
    if (!mint) {
      setCandles([]);
      return;
    }
    let cancelled = false;
    const fetchCandles = async () => {
      if (ohlcvInFlightRef.current) return;
      ohlcvInFlightRef.current = true;
      try {
        const res = await api.tokenOhlcv(mint, timeframe);
        if (!cancelled && res.candles) {
          setCandles(res.candles);
        }
      } catch {
        // keep existing candles
      } finally {
        ohlcvInFlightRef.current = false;
      }
    };
    void fetchCandles();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void fetchCandles();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [mint, timeframe]);

  if (!mint) return null;

  const live = data?.live ?? null;
  const lead = data?.lead ?? null;
  const verdict = data?.verdict ?? null;
  const trade = data?.trade ?? null;
  const price = live?.price_usd ?? lead?.price_usd ?? null;
  const mcap = live?.market_cap ?? lead?.market_cap ?? null;
  const liq = live?.liquidity_usd ?? lead?.liquidity_usd ?? null;
  const mainPool = live?.main_pool_share ?? lead?.main_pool_share ?? null;

  const tx5m = live?.txns.m5 ?? { buys: 0, sells: 0 };
  const tx1h = live?.txns.h1 ?? { buys: 0, sells: 0 };
  const total5m = tx5m.buys + tx5m.sells;
  const buyPct5m = total5m > 0 ? Math.round((tx5m.buys / total5m) * 100) : 50;

  async function copyMint() {
    if (!mint) return;
    try {
      await navigator.clipboard.writeText(mint);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore clipboard errors
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-3 sm:p-5"
      onClick={onClose}
    >
      <div
        className="w-full max-w-4xl max-h-[90vh] rounded-3xl glass-panel flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-white/10 bg-white/[0.02] flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-3.5 min-w-0">
            {live?.image_url && !imgFailed ? (
              <img
                src={live.image_url}
                alt={data?.symbol ?? "token"}
                onError={() => setImgFailed(true)}
                className="h-12 w-12 rounded-xl border border-slate-700 object-cover bg-slate-950 shrink-0"
              />
            ) : (
              <div className="h-12 w-12 rounded-xl border border-cyan-500/40 bg-cyan-500/15 flex items-center justify-center font-mono text-sm font-bold text-cyan-300 shrink-0">
                {(data?.symbol ?? "?").slice(0, 3)}
              </div>
            )}
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
                <h2 className="font-mono text-lg font-bold text-slate-100">
                  ${data?.symbol ?? "..."}
                </h2>
                <span className="text-xs text-slate-400 truncate max-w-[200px]">
                  {data?.name ?? ""}
                </span>
                {lead?.stage && (
                  <span className="px-2 py-0.5 rounded-md border border-cyan-500/40 bg-cyan-500/15 font-mono text-[10px] font-bold text-cyan-200">
                    {lead.stage}
                  </span>
                )}
                {lead?.kill_reason && (
                  <span className="px-2 py-0.5 rounded-md border border-rose-500/40 bg-rose-950/70 font-mono text-[10px] font-bold text-rose-300">
                    {lead.kill_reason}
                  </span>
                )}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[11px] text-slate-400">
                <span>{shortAddr(mint, 8, 6)}</span>
                <button
                  type="button"
                  onClick={() => void copyMint()}
                  className="p-1 rounded hover:bg-slate-800 text-slate-300 cursor-pointer"
                  title="Copiar Mint completo"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
                <a
                  href={live?.pair_url ?? `https://dexscreener.com/solana/${mint}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-cyan-300"
                >
                  DexScreener <ExternalLink className="w-3 h-3" />
                </a>
                {live?.socials.map((s, i) => (
                  <a
                    key={`${s.type}-${i}`}
                    href={s.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 uppercase text-[10px]"
                  >
                    {s.type === "twitter" ? "X / Twitter" : s.type}
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                ))}
                {live?.websites.map((w, i) => (
                  <a
                    key={`web-${i}`}
                    href={w.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-[10px]"
                  >
                    <Globe className="w-3 h-3 text-cyan-400" /> {w.label}
                  </a>
                ))}
                {live?.pair_created_at && (
                  <span className="text-[10px] text-slate-500">
                    Creado hace {fmtAgo(new Date(live.pair_created_at).toISOString())}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="text-right font-mono">
              <div className={`text-lg font-bold text-slate-100 tabular-nums px-1.5 rounded ${tickClass}`}>
                {fmtTokenPrice(price)}
              </div>
              <div className="text-[10px] text-emerald-400 uppercase tracking-wider">
                LIVE · 0.25s TICK
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="p-2 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800 cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="p-5 overflow-y-auto space-y-5">
          {!data ? (
            <div className="py-12 text-center font-mono text-xs text-slate-400">
              Cargando telemetría en vivo...
            </div>
          ) : (
            <>
              {/* Live Japanese Candlestick Chart (1m / 5m / 15m + 0.25s tick) */}
              <section className="rounded-2xl border border-white/10 bg-black/50 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-cyan-300">
                      <Activity className="w-3.5 h-3.5" /> Velas Japonesas en Vivo ({timeframe})
                    </span>
                    <span className="text-[10px] text-zinc-400">
                      · tick 0.25s · {live?.dex_id ?? "solana"}
                    </span>
                  </div>

                  <div className="flex items-center gap-1 rounded-xl border border-white/10 bg-white/[0.04] p-0.5">
                    {(["1m", "5m", "15m"] as const).map((tf) => (
                      <button
                        key={tf}
                        type="button"
                        onClick={() => setTimeframe(tf)}
                        className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold transition cursor-pointer ${
                          timeframe === tf
                            ? "bg-cyan-400/20 text-cyan-200 border border-cyan-400/50"
                            : "text-zinc-400 hover:text-white border border-transparent"
                        }`}
                      >
                        {tf}
                      </button>
                    ))}
                  </div>
                </div>
                {candles.length > 0 ? (
                  <CandlestickChart candles={candles} timeframe={timeframe} />
                ) : (
                  <LivePriceChart points={data.price_history} />
                )}
              </section>

              {/* Key Metrics Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono">
                <MetricBox label="Market Cap" value={fmtUsd(mcap, true)} accent="text-cyan-300" />
                <MetricBox
                  label="Liquidez Pool"
                  value={fmtUsd(liq, true)}
                  sub={mainPool !== null ? `Pool ppal: ${Math.round(mainPool * 100)}%` : undefined}
                  accent="text-emerald-300"
                />
                <MetricBox
                  label="Holders"
                  value={lead?.holders_count ? lead.holders_count.toLocaleString() : "—"}
                  sub={live?.fdv ? `FDV ${fmtUsd(live.fdv, true)}` : undefined}
                />
                <MetricBox
                  label="Dev Wallet"
                  value={shortAddr(lead?.dev_wallet, 5, 4)}
                  sub={lead?.dev_blacklisted ? "BLACKLISTED" : "Limpio"}
                  accent={lead?.dev_blacklisted ? "text-rose-400" : "text-slate-200"}
                />
              </div>

              {/* Timeframes: Price Change + Volume + Orderflow */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-3.5 space-y-2.5 font-mono">
                  <div className="text-[11px] uppercase tracking-wider text-slate-400">
                    Variación de Precio y Volumen
                  </div>
                  <div className="grid grid-cols-4 gap-2 text-center">
                    {(["m5", "h1", "h6", "h24"] as const).map((tf) => {
                      const chg = live?.price_change[tf] ?? null;
                      const vol = live?.volume[tf] ?? null;
                      return (
                        <div key={tf} className="rounded-lg border border-slate-800 bg-slate-900/70 p-2">
                          <div className="text-[10px] text-slate-500 uppercase">{tf}</div>
                          <div
                            className={`text-xs font-bold tabular-nums ${
                              chg === null
                                ? "text-slate-400"
                                : chg >= 0
                                ? "text-emerald-400"
                                : "text-rose-400"
                            }`}
                          >
                            {fmtPct(chg, true)}
                          </div>
                          <div className="text-[10px] text-slate-400 tabular-nums mt-0.5">
                            {fmtUsd(vol, true)}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>

                <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-3.5 space-y-2.5 font-mono">
                  <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-slate-400">
                    <span>Flujo Real (Compras vs. Ventas)</span>
                    <span className="text-slate-300">5m / 1h</span>
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex justify-between text-xs">
                      <span className="text-emerald-400">5m Compras: {tx5m.buys}</span>
                      <span className="text-rose-400">Ventas: {tx5m.sells}</span>
                    </div>
                    <div className="h-2 w-full rounded-full bg-rose-500/40 overflow-hidden">
                      <div
                        className="h-full bg-emerald-400 transition-all duration-300"
                        style={{ width: `${buyPct5m}%` }}
                      />
                    </div>
                    <div className="flex justify-between text-[11px] text-slate-400 pt-1">
                      <span>1h Compras: {tx1h.buys}</span>
                      <span>1h Ventas: {tx1h.sells}</span>
                    </div>
                  </div>
                </section>
              </div>

              {/* On-Chain Security Audit Checklist (RugCheck + Parameters) */}
              {data.security_audit && (
                <SecurityAuditPanel
                  audit={data.security_audit}
                  mainPoolShare={mainPool}
                  liquidityUsd={liq}
                  marketCapUsd={mcap}
                  holdersCount={lead?.holders_count ?? null}
                />
              )}

              {/* Smart Wallets Whitelist Match (Lista Blanca) */}
              {data.smart_wallets_matched && (
                <section
                  className={`rounded-2xl border p-4 space-y-2.5 ${
                    data.smart_wallets_matched.count > 0
                      ? "border-amber-400/45 bg-amber-500/10"
                      : "border-white/10 bg-white/[0.025]"
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span
                      className={`flex items-center gap-1.5 text-xs font-bold uppercase ${
                        data.smart_wallets_matched.count > 0 ? "text-amber-300" : "text-zinc-300"
                      }`}
                    >
                      <Fish className="w-4 h-4" /> Smart Wallets (Lista Blanca) en este Token
                    </span>
                    <span
                      className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold tabular-nums ${
                        data.smart_wallets_matched.count > 0
                          ? "bg-amber-400/20 border border-amber-400/50 text-amber-200"
                          : "bg-white/[0.06] border border-white/10 text-zinc-400"
                      }`}
                    >
                      {data.smart_wallets_matched.count} / {data.smart_wallets_matched.total_tracked} detectadas
                    </span>
                  </div>

                  {data.smart_wallets_matched.count === 0 ? (
                    <p className="text-xs text-zinc-400">
                      Ninguna de tus {data.smart_wallets_matched.total_tracked} Smart Wallets aparece entre los{" "}
                      {data.smart_wallets_matched.checked_holders > 0
                        ? `Top ${data.smart_wallets_matched.checked_holders}`
                        : "principales"}{" "}
                      holders actuales de este token.
                    </p>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {data.smart_wallets_matched.wallets.map((w) => {
                        const wr = w.winrate_30d ?? w.winrate_7d;
                        const prof = w.realized_profit_30d ?? w.realized_profit_7d;
                        return (
                          <button
                            key={w.address}
                            type="button"
                            onClick={() => setSelectedWallet(w)}
                            className="text-left rounded-xl border border-amber-400/35 bg-black/55 hover:bg-amber-500/15 px-3 py-2 flex items-center justify-between gap-2 transition cursor-pointer"
                          >
                            <div className="flex items-center gap-2.5 min-w-0">
                              {w.avatar ? (
                                <img
                                  src={w.avatar}
                                  alt={w.label}
                                  className="w-8 h-8 rounded-full object-cover border border-amber-400/40 shrink-0"
                                  onError={(e) => {
                                    (e.currentTarget as HTMLImageElement).style.display = "none";
                                  }}
                                />
                              ) : (
                                <div className="w-8 h-8 rounded-full border border-amber-400/35 bg-amber-500/20 text-amber-200 flex items-center justify-center text-[10px] font-bold shrink-0">
                                  {(w.label || w.address).slice(0, 2).toUpperCase()}
                                </div>
                              )}
                              <div className="min-w-0">
                                <div className="flex items-center gap-1.5 min-w-0">
                                  <span className="text-xs font-bold text-white truncate">
                                    {w.label || shortAddr(w.address, 6, 4)}
                                  </span>
                                  {w.twitter_username && (
                                    <span className="text-[10px] text-sky-300 truncate">
                                      @{w.twitter_username}
                                    </span>
                                  )}
                                </div>
                                <div className="flex flex-wrap items-center gap-2 text-[10px] text-zinc-400 tabular-nums">
                                  {wr != null && <span className="text-emerald-300 font-semibold">WR {wr}%</span>}
                                  {prof != null && prof > 0 && (
                                    <span className="text-cyan-300">+{fmtUsd(prof, true)}</span>
                                  )}
                                </div>
                              </div>
                            </div>
                            {w.pct_held != null && (
                              <span className="px-2 py-0.5 rounded-lg bg-amber-500/20 text-amber-200 text-[11px] font-bold tabular-nums shrink-0">
                                {w.pct_held.toFixed(2)}%
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </section>
              )}

              {/* Judge Verdict & Anti-Wash Dossier (Interactive Explanations) */}
              {verdict && <JudgeVerdictPanel verdict={verdict} leadStage={lead?.stage ?? null} />}

              {/* Plan del Bot / Próximo Paso */}
              {lead && (
                <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-1.5">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-300">
                    Plan del Bot · Qué sigue ahora
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed">
                    {lead.stage === "JUDGE" && verdict?.action === "watch"
                      ? "El Juez Mudo marcó WATCH (duda). El bot lo deja en vigilancia 4 minutos para ver si mejora la liquidez y las compras reales, y luego lo reevalúa UNA sola vez: si mejora y aprueba los 5 filtros pasa a compra; si vuelve a salir WATCH o PASS, se descarta automáticamente."
                      : lead.stage === "JUDGE"
                      ? "El Juez Mudo está evaluando los 5 filtros (Organic, Late, Exit Risk, Trap y Action) junto con el Gate matemático antes de decidir si compra o descarta."
                      : lead.stage === "PENDING_APPROVAL"
                      ? "Aprobado por el Juez y el Gate. Está esperando tu confirmación manual (ventana de 90 segundos) para ejecutar la compra."
                      : lead.stage === "OPEN" || lead.stage === "RISK_FREE_RUNNER"
                      ? "Posición activa en Motor Wave & Wick (sin Stop Loss negativo en modo Agresivo): revisa cada 3s para armar Breakeven Inteligente (+12% -> piso +3%), cazar mechas (+18% si retrocede 6%), cobrar TP1 (+25%) y dejar Runner 2X libre de riesgo."
                      : lead.stage === "REJECTED"
                      ? `Descartado definitivamente por el filtro (${lead.kill_reason ?? verdict?.kill_reason ?? "RECHAZADO"}). No se invertirá en este token.`
                      : "Pasando por el embudo automático de filtros (Search → Risk & Whale → Juez → Gate)."}
                  </p>
                </section>
              )}

              {/* Cached X Mentions */}
              {data.x_mentions.length > 0 && (
                <section className="rounded-xl border border-amber-500/30 bg-amber-950/10 p-4 space-y-2.5">
                  <div className="flex items-center gap-1.5 font-mono text-xs font-bold uppercase text-amber-300">
                    <Sparkles className="w-4 h-4" /> Menciones en X ({data.x_mentions.length})
                  </div>
                  <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                    {data.x_mentions.map((m, idx) => (
                      <div
                        key={`${m.author_handle}-${idx}`}
                        className="rounded-lg border border-slate-800 bg-slate-950/75 p-2.5 text-xs space-y-1"
                      >
                        <div className="flex items-center justify-between gap-2 font-mono text-[11px]">
                          <span className="flex items-center gap-1 font-bold text-slate-100">
                            @{m.author_handle}
                            {m.is_verified && <BadgeCheck className="w-3.5 h-3.5 text-cyan-400" />}
                            <span className="text-slate-400 font-normal">
                              ({fmtFollowers(m.author_followers)})
                            </span>
                          </span>
                          {m.is_notable_figure && (
                            <span className="px-1.5 py-0.5 rounded bg-amber-500/20 border border-amber-400/50 text-[10px] text-amber-200">
                              {m.notable_tag ?? "FIGURA RELEVANTE"}
                            </span>
                          )}
                        </div>
                        <p className="text-slate-300 leading-relaxed">{m.text}</p>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Trade Info & Quick Actions */}
              {trade && (
                <section className="rounded-xl border border-emerald-500/40 bg-emerald-950/15 p-4 flex flex-wrap items-center justify-between gap-3 font-mono">
                  <div className="space-y-0.5 text-xs">
                    <div className="font-bold text-emerald-300">
                      POSICIÓN {trade.mode} · {trade.status}
                    </div>
                    <div className="text-slate-300">
                      Invertido: {fmtSol(trade.invested_sol, 4)} · Realizado: {fmtSol(trade.realized_sol, 4)} ·
                      PnL:{" "}
                      <span className={trade.pnl_sol >= 0 ? "text-emerald-400 font-bold" : "text-rose-400 font-bold"}>
                        {fmtSol(trade.pnl_sol, 4, true)} ({fmtPct(trade.pnl_pct, true)})
                      </span>
                    </div>
                  </div>
                  {trade.status !== "CLOSED" && onExitTrade && (
                    <button
                      type="button"
                      onClick={() => void onExitTrade(trade.id)}
                      className="px-3.5 py-2 rounded-lg border border-rose-500/60 bg-rose-950/70 hover:bg-rose-600/30 text-rose-200 text-xs font-bold flex items-center gap-1.5 cursor-pointer"
                    >
                      <LogOut className="w-3.5 h-3.5" /> SALIR 100%
                    </button>
                  )}
                </section>
              )}

              {lead?.stage === "PENDING_APPROVAL" && onApprove && onReject && (
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <button
                    type="button"
                    onClick={() => void onApprove(lead.id)}
                    className="py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-mono text-xs font-bold cursor-pointer"
                  >
                    ✅ APROBAR COMPRA
                  </button>
                  <button
                    type="button"
                    onClick={() => void onReject(lead.id)}
                    className="py-2.5 rounded-xl border border-rose-500/60 bg-rose-950/60 hover:bg-rose-600/30 text-rose-200 font-mono text-xs font-bold cursor-pointer flex items-center justify-center gap-1.5"
                  >
                    <Ban className="w-3.5 h-3.5" /> DESCARTAR
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <SmartWalletModal
        wallet={selectedWallet}
        onClose={() => setSelectedWallet(null)}
      />
    </div>
  );
}

function JudgeVerdictPanel({
  verdict,
  leadStage,
}: {
  verdict: NonNullable<TokenDetailResponse["verdict"]>;
  leadStage: string | null;
}) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const items = [
    {
      k: "ORGANIC",
      v: `${verdict.organic} (${fmtProb(verdict.organic_prob)})`,
      ok: verdict.organic === "yes",
      what: "Mide si el crecimiento de compras y holders viene de compradores reales e independientes (demanda genuina) y no de 1 o 2 bots inflando volumen.",
      meaning:
        verdict.organic === "yes"
          ? `BUENA SEÑAL (${fmtProb(verdict.organic_prob)}): el Juez detecta flujo comprador saludable y distribución real.`
          : `ALERTA (${fmtProb(verdict.organic_prob)}): el flujo de compras se ve débil, artificial o dominado por ventas.`,
    },
    {
      k: "LATE",
      v: `${verdict.late} (${fmtProb(verdict.late_prob)})`,
      ok: verdict.late === "no",
      what: "Evalúa si la subida fuerte ya ocurrió antes de que llegáramos al radar (riesgo de comprar el techo y quedar atrapado).",
      meaning:
        verdict.late === "no"
          ? `BUENA SEÑAL: todavía hay recorrido por delante; no parece estar extendido en el pico.`
          : `ENTRADA TARDÍA (${fmtProb(verdict.late_prob)}): el precio ya subió demasiado o viene cayendo tras un pump viejo; alto riesgo de ser liquidez de salida.`,
    },
    {
      k: "EXIT RISK",
      v: `${verdict.exit_risk ?? "—"}/5`,
      ok: (verdict.exit_risk ?? 5) <= 3,
      what: "Escala del 1 al 5 que mide qué tan fácil y barato será SALIR (vender) con nuestro tamaño de SOL según la profundidad del pool.",
      meaning:
        (verdict.exit_risk ?? 5) <= 2
          ? `SALIDA LIMPIA (${verdict.exit_risk}/5): el pool tiene buena liquidez frente a tu tamaño de trade, bajo slippage al vender.`
          : (verdict.exit_risk ?? 5) === 3
          ? `RIESGO MODERADO (3/5): se puede salir, pero vigila la liquidez si el volumen baja.`
          : `RIESGO ALTO (${verdict.exit_risk}/5): pool demasiado delgado; al vender perderías mucho en slippage.`,
    },
    {
      k: "TRAP",
      v: `${verdict.manipulation_trap} (${fmtProb(verdict.trap_prob)})`,
      ok: verdict.manipulation_trap === "no",
      what: "Busca señales de trampa: wash trading (volumen falso), redes de insiders/bundlers, dev serial o concentración sospechosa.",
      meaning:
        verdict.manipulation_trap === "no"
          ? `LIMPIO (${fmtProb(verdict.trap_prob)} riesgo): no se detectan patrones fuertes de manipulación ni trampa.`
          : `POSIBLE TRAMPA (${fmtProb(verdict.trap_prob)}): señales claras de manipulación, bundlers o volumen fabricado. El Gate bloquea la compra.`,
    },
    {
      k: "ACTION",
      v: `${(verdict.action ?? "").toUpperCase()} (${fmtProb(verdict.action_prob)})`,
      ok: verdict.action === "buy",
      what: "Decisión final del Juez Mudo combinando las 4 variables anteriores: BUY (entrar ya), WATCH (esperar 4 min y reevaluar 1 vez) o PASS (descartar).",
      meaning:
        verdict.action === "buy"
          ? `COMPRA (${fmtProb(verdict.action_prob)}): el Juez recomienda entrar; el Gate matemático decide si supera el umbral mínimo.`
          : verdict.action === "watch"
          ? `EN OBSERVACIÓN (${fmtProb(verdict.action_prob)}): interesante pero aún falta confirmación (ej. bajar el riesgo LATE o subir compras). Se reevalúa 1 vez en 4 min.`
          : `DESCARTAR (${fmtProb(verdict.action_prob)}): riesgo alto o sin momentum claro; el bot pasa al siguiente token.`,
    },
  ];

  const activeItem = items.find((i) => i.k === selectedKey) ?? null;

  return (
    <section className="rounded-2xl border border-violet-500/35 bg-violet-950/15 p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-bold uppercase text-violet-300">
          <Cpu className="w-4 h-4" /> Veredicto del Juez Mudo
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setShowAll((v) => !v);
              setSelectedKey(null);
            }}
            className={`px-2 py-0.5 rounded-lg border text-[10px] font-semibold inline-flex items-center gap-1 cursor-pointer transition ${
              showAll
                ? "border-violet-400/60 bg-violet-500/25 text-violet-100"
                : "border-white/10 bg-white/[0.04] text-zinc-300 hover:text-white"
            }`}
          >
            <HelpCircle className="w-3 h-3" /> {showAll ? "Ocultar guía" : "¿Qué significa?"}
          </button>
          <span
            className={`px-2 py-0.5 rounded text-[11px] font-bold ${
              verdict.gate_passed
                ? "bg-emerald-500/20 text-emerald-300"
                : verdict.action === "watch" && leadStage === "JUDGE"
                ? "bg-amber-500/20 text-amber-300"
                : "bg-rose-500/20 text-rose-300"
            }`}
          >
            {verdict.gate_passed
              ? "GATE APROBADO"
              : verdict.action === "watch" && leadStage === "JUDGE"
              ? "EN OBSERVACIÓN (WATCH)"
              : verdict.kill_reason ?? "RECHAZADO"}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        {items.map((item) => {
          const isSelected = selectedKey === item.k;
          return (
            <button
              key={item.k}
              type="button"
              onClick={() => {
                setShowAll(false);
                setSelectedKey((prev) => (prev === item.k ? null : item.k));
              }}
              title="Haz clic para ver qué significa este indicador"
              className={`rounded-xl border p-2.5 text-center transition cursor-pointer ${
                item.ok
                  ? "border-emerald-500/40 bg-emerald-950/30 text-emerald-300 hover:border-emerald-400"
                  : "border-rose-500/40 bg-rose-950/30 text-rose-300 hover:border-rose-400"
              } ${isSelected ? "ring-2 ring-violet-400/70" : ""}`}
            >
              <div className="flex items-center justify-center gap-1 text-[10px] font-semibold">
                {item.ok ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                <span>{item.k}</span>
                <HelpCircle className="w-2.5 h-2.5 opacity-60" />
              </div>
              <div className="text-xs font-bold mt-1 tabular-nums">{item.v}</div>
            </button>
          );
        })}
      </div>

      {activeItem && !showAll && (
        <div className="rounded-xl border border-violet-400/35 bg-black/60 p-3 text-xs space-y-1">
          <div className="font-bold text-violet-200">
            {activeItem.k} · {activeItem.v}
          </div>
          <p className="text-zinc-300 leading-relaxed">
            <strong className="text-white">Qué mide:</strong> {activeItem.what}
          </p>
          <p className="text-zinc-200 leading-relaxed">
            <strong className={activeItem.ok ? "text-emerald-300" : "text-rose-300"}>
              Qué significa aquí:
            </strong>{" "}
            {activeItem.meaning}
          </p>
        </div>
      )}

      {showAll && (
        <div className="rounded-xl border border-violet-400/30 bg-black/60 p-3 space-y-2 text-xs">
          {items.map((it) => (
            <div key={it.k} className="border-b border-white/[0.06] last:border-0 pb-2 last:pb-0">
              <div className="flex items-center justify-between font-bold">
                <span className="text-violet-200">{it.k}</span>
                <span className={it.ok ? "text-emerald-300" : "text-rose-300"}>{it.v}</span>
              </div>
              <p className="text-zinc-400 text-[11px] mt-0.5">{it.what}</p>
              <p className="text-zinc-200 text-[11px] mt-0.5">{it.meaning}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function SecurityAuditPanel({
  audit,
  mainPoolShare,
  liquidityUsd,
  marketCapUsd,
  holdersCount,
}: {
  audit: NonNullable<TokenDetailResponse["security_audit"]>;
  mainPoolShare: number | null;
  liquidityUsd: number | null;
  marketCapUsd: number | null;
  holdersCount: number | null;
}) {
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  if (!audit.available) {
    return (
      <section className="rounded-2xl border border-white/10 bg-white/[0.025] p-4 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-bold uppercase text-zinc-300">
          <ShieldCheck className="w-4 h-4 text-cyan-300" /> Auditoría de Seguridad On-Chain (RugCheck)
        </span>
        <span className="text-[11px] text-zinc-400">Reporte RugCheck no disponible aún</span>
      </section>
    );
  }

  const th = audit.thresholds;
  const poolPct = mainPoolShare !== null ? Math.round(mainPoolShare * 100) : null;
  const liqToMcPct =
    liquidityUsd && marketCapUsd && marketCapUsd > 0
      ? (liquidityUsd / marketCapUsd) * 100
      : null;

  const checks: Array<{
    label: string;
    value: string;
    rule: string;
    status: "ok" | "warn" | "bad";
    what: string;
    meaning: string;
  }> = [
    {
      label: "Mint Authority",
      value: audit.mint_revoked ? "Revocada" : "ACTIVA",
      rule: "Debe estar revocada",
      status: audit.mint_revoked ? "ok" : "bad",
      what: "Permiso técnico para imprimir (crear) tokens nuevos de la nada.",
      meaning: audit.mint_revoked
        ? "SEGURO: nadie puede imprimir más supply para desplomar el precio a cero."
        : "PELIGRO CRÍTICO: el creador puede imprimir tokens infinitos y vaciar la liquidez.",
    },
    {
      label: "Freeze Authority",
      value: audit.freeze_revoked ? "Revocada" : "ACTIVA",
      rule: "Debe estar revocada",
      status: audit.freeze_revoked ? "ok" : "bad",
      what: "Permiso para congelar carteras de compradores e impedirles vender (honeypot).",
      meaning: audit.freeze_revoked
        ? "SEGURO: tus tokens nunca pueden ser congelados; podrás vender cuando quieras."
        : "HONEYPOT: el dev puede bloquear tu cartera para que no puedas vender.",
    },
    {
      label: "LP Bloqueada / Quemada",
      value: audit.lp_locked_pct != null ? `${audit.lp_locked_pct.toFixed(1)}%` : "—",
      rule: `Mínimo ≥ ${th.lp_locked_min_pct}%`,
      status:
        audit.lp_locked_pct != null && audit.lp_locked_pct >= th.lp_locked_min_pct ? "ok" : "bad",
      what: "Porcentaje de la liquidez del pool que fue quemada o bloqueada para que el creador no pueda retirarla.",
      meaning:
        audit.lp_locked_pct != null && audit.lp_locked_pct >= th.lp_locked_min_pct
          ? `SEGURO (${audit.lp_locked_pct.toFixed(1)}%): el dev no puede hacer rug pull retirando la liquidez del pool.`
          : "RIESGO DE RUG: la liquidez está desbloqueada y el dev podría sacarla en cualquier momento.",
    },
    {
      label: "Top 10 Holders",
      value: audit.top10_pct != null ? `${audit.top10_pct.toFixed(1)}%` : "—",
      rule: `Máximo ≤ ${th.max_top10_pct}%`,
      status:
        audit.top10_pct == null
          ? "warn"
          : audit.top10_pct <= th.max_top10_pct
          ? "ok"
          : "bad",
      what: "Porcentaje total del supply en manos de las 10 carteras más grandes (excluyendo el pool/AMM).",
      meaning:
        audit.top10_pct != null && audit.top10_pct <= th.max_top10_pct
          ? `BUENA DISTRIBUCIÓN (${audit.top10_pct.toFixed(1)}%): ninguna ballena pequeña tiene suficiente para matar el gráfico sola.`
          : "ALTA CONCENTRACIÓN: pocas carteras controlan demasiado supply y pueden tumbar el precio de golpe.",
    },
    {
      label: "Dev Wallet",
      value: audit.dev_pct != null ? `${audit.dev_pct.toFixed(2)}%` : "—",
      rule: `Máximo ≤ ${th.max_dev_pct}%`,
      status:
        audit.dev_pct == null
          ? "warn"
          : audit.dev_pct <= th.max_dev_pct
          ? "ok"
          : "bad",
      what: "Porcentaje del token que todavía conserva la cartera del creador (deployer).",
      meaning:
        audit.dev_pct != null && audit.dev_pct <= th.max_dev_pct
          ? `LIMPIO (${audit.dev_pct.toFixed(2)}%): el creador ya no tiene una bolsa grande para dumpearte encima.`
          : "DEV CON BOLSA ALTA: el creador controla más del 4% y podría venderte encima.",
    },
    {
      label: "Pool Principal",
      value: poolPct != null ? `${poolPct}%` : "—",
      rule: `Mínimo ≥ ${th.main_pool_min_pct}%`,
      status: poolPct == null ? "warn" : poolPct >= th.main_pool_min_pct ? "ok" : "bad",
      what: "Cuánto de toda la liquidez del token está concentrada en el pool principal (evita liquidez fragmentada).",
      meaning:
        poolPct != null && poolPct >= th.main_pool_min_pct
          ? `CONCENTRADO (${poolPct}%): todo el volumen pasa por un solo pool profundo, ejecución limpia.`
          : "LIQUIDEZ FRAGMENTADA: está repartida en varios pools pequeños, genera slippage impredecible.",
    },
    {
      label: "Ratio Liq / MCap",
      value: liqToMcPct != null ? `${liqToMcPct.toFixed(1)}%` : "—",
      rule: "Sano ≥ 10% · Peligro < 5%",
      status:
        liqToMcPct == null
          ? "warn"
          : liqToMcPct >= 10
          ? "ok"
          : liqToMcPct >= 6
          ? "warn"
          : "bad",
      what: "Relación entre el dinero real en el pool (Liquidez) y la valoración total (Market Cap).",
      meaning:
        liqToMcPct != null && liqToMcPct >= 10
          ? `PROFUNDO (${liqToMcPct.toFixed(1)}%): colchón sano para entrar y salir aprovechando mechas sin destruir el precio.`
          : liqToMcPct != null && liqToMcPct >= 6
          ? `AJUSTADO (${liqToMcPct.toFixed(1)}%): usable con tamaño pequeño, pero más volátil.`
          : "MUY DELGADO (<6%): valoración inflada con poca liquidez real; una sola venta tira el precio -30%.",
    },
    {
      label: "Impuesto Transfer",
      value: `${audit.transfer_fee_pct ?? 0}%`,
      rule: "Debe ser 0%",
      status: (audit.transfer_fee_pct ?? 0) === 0 ? "ok" : "bad",
      what: "Comisión oculta de Token-2022 que te quita un porcentaje automático cada vez que compras o vendes.",
      meaning:
        (audit.transfer_fee_pct ?? 0) === 0
          ? "LIMPIO (0%): no hay impuestos ocultos en el contrato."
          : "TRAMPA DE IMPUESTO: el contrato te cobra comisión oculta en cada transferencia.",
    },
    {
      label: "Metadata",
      value:
        audit.mutable_metadata === false
          ? "Inmutable"
          : audit.mutable_metadata === true
          ? "Mutable"
          : "—",
      rule: "Ideal inmutable",
      status:
        audit.mutable_metadata === false
          ? "ok"
          : audit.mutable_metadata === true
          ? "warn"
          : "warn",
      what: "Indica si el creador puede cambiar el nombre, logo o enlaces del token después de lanzarlo.",
      meaning:
        audit.mutable_metadata === false
          ? "INMUTABLE: nadie puede alterar la identidad ni enlaces del proyecto."
          : "MUTABLE: el dev todavía podría modificar nombre, imagen o redes del token.",
    },
    {
      label: "Redes Insiders",
      value: audit.rugcheck_insider_flag
        ? `ALERTA (${audit.insider_networks_count ?? 0} redes)`
        : `${audit.insider_networks_count ?? 0} redes (Normal)`,
      rule:
        audit.insiders_detected != null
          ? `${audit.insiders_detected} cuentas en grafo CEX`
          : "Sin alerta RugCheck",
      status: audit.rugcheck_insider_flag ? "bad" : "ok",
      what: "Clústeres de carteras conectadas en el grafo de RugCheck. En tokens virales con miles de holders, es normal ver redes grandes porque miles de usuarios fondean desde el mismo exchange (Binance, Coinbase, OKX, Phantom).",
      meaning: audit.rugcheck_insider_flag
        ? "ALERTA REAL DE INSIDERS/BUNDLERS: RugCheck marcó explícitamente riesgo de insiders o bundlers en este contrato."
        : "NORMAL / SIN ALERTA DE RUGCHECK: aunque el grafo muestre conexiones comunes (exchanges/CEX), RugCheck NO marcó alerta de insiders ni bundlers. Revisa Top 10 Holders para confirmar buena distribución.",
    },
    {
      label: "Tokens del Dev",
      value: audit.creator_tokens_count != null ? `${audit.creator_tokens_count}` : "—",
      rule: "Creador serial si > 5",
      status:
        audit.creator_tokens_count == null
          ? "warn"
          : audit.creator_tokens_count <= 2
          ? "ok"
          : audit.creator_tokens_count <= 5
          ? "warn"
          : "bad",
      what: "Cantidad de otros tokens que esta misma cartera creadora ha lanzado anteriormente en Solana.",
      meaning:
        audit.creator_tokens_count == null
          ? "SIN HISTORIAL REPORTADO (—): RugCheck no devolvió otros lanzamientos de esta cartera. No es un error grave; suele pasar en devs nuevos."
          : audit.creator_tokens_count <= 2
          ? `LIMPIO (${audit.creator_tokens_count}): no es un lanzador serial de tokens basura.`
          : `DEV SERIAL (${audit.creator_tokens_count} tokens): suele lanzar muchos proyectos seguidos para sacar liquidez rápido.`,
    },
    {
      label: "Holders Totales",
      value: holdersCount != null ? holdersCount.toLocaleString() : "—",
      rule: `Mínimo ≥ ${th.min_holders}`,
      status:
        holdersCount == null ? "warn" : holdersCount >= th.min_holders ? "ok" : "bad",
      what: "Número total de carteras distintas que actualmente poseen el token.",
      meaning:
        holdersCount != null && holdersCount >= th.min_holders
          ? `COMUNIDAD ACTIVA (${holdersCount.toLocaleString()}): suficiente base de compradores para sostener olas de volumen.`
          : "POCOS HOLDERS: todavía muy temprano o con poco interés del mercado.",
    },
  ];

  const badCount = checks.filter((c) => c.status === "bad").length;
  const okCount = checks.filter((c) => c.status === "ok").length;
  const activeCheck = checks.find((c) => c.label === selectedLabel) ?? null;

  return (
    <section className="rounded-2xl border border-cyan-500/30 bg-cyan-950/10 p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-bold uppercase text-cyan-300">
          <ShieldCheck className="w-4 h-4" /> Auditoría de Seguridad On-Chain (RugCheck)
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setShowAll((v) => !v);
              setSelectedLabel(null);
            }}
            className={`px-2 py-0.5 rounded-lg border text-[10px] font-semibold inline-flex items-center gap-1 cursor-pointer transition ${
              showAll
                ? "border-cyan-400/60 bg-cyan-500/25 text-cyan-100"
                : "border-white/10 bg-white/[0.04] text-zinc-300 hover:text-white"
            }`}
          >
            <HelpCircle className="w-3 h-3" /> {showAll ? "Ocultar guía" : "¿Qué significa?"}
          </button>
          {audit.rugged && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/25 border border-rose-400/60 text-rose-200">
              RUG DETECTADO
            </span>
          )}
          <span
            className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold tabular-nums ${
              badCount === 0
                ? "bg-emerald-500/20 border border-emerald-400/50 text-emerald-200"
                : "bg-rose-500/20 border border-rose-400/50 text-rose-200"
            }`}
          >
            {okCount}/{checks.length} OK {badCount > 0 ? `· ${badCount} alerta(s)` : "· Cumple filtros"}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
        {checks.map((c) => {
          const isSelected = selectedLabel === c.label;
          const cls =
            c.status === "ok"
              ? "border-emerald-500/35 bg-emerald-950/25 text-emerald-300 hover:border-emerald-400"
              : c.status === "warn"
              ? "border-amber-500/35 bg-amber-950/25 text-amber-200 hover:border-amber-400"
              : "border-rose-500/45 bg-rose-950/35 text-rose-300 hover:border-rose-400";
          return (
            <button
              key={c.label}
              type="button"
              onClick={() => {
                setShowAll(false);
                setSelectedLabel((prev) => (prev === c.label ? null : c.label));
              }}
              title="Haz clic para ver qué significa este filtro"
              className={`text-left rounded-xl border px-2.5 py-2 transition cursor-pointer ${cls} ${
                isSelected ? "ring-2 ring-cyan-400/70" : ""
              }`}
            >
              <div className="flex items-center justify-between gap-1 text-[10px] opacity-90">
                <span className="truncate font-medium">{c.label}</span>
                <span className="flex items-center gap-1 shrink-0">
                  <HelpCircle className="w-2.5 h-2.5 opacity-60" />
                  {c.status === "ok" ? (
                    <CheckCircle2 className="w-3 h-3" />
                  ) : (
                    <XCircle className="w-3 h-3" />
                  )}
                </span>
              </div>
              <div className="text-xs font-bold tabular-nums mt-0.5">{c.value}</div>
              <div className="text-[10px] text-zinc-400 truncate mt-0.5">{c.rule}</div>
            </button>
          );
        })}
      </div>

      {activeCheck && !showAll && (
        <div className="rounded-xl border border-cyan-400/35 bg-black/60 p-3 text-xs space-y-1">
          <div className="font-bold text-cyan-200">
            {activeCheck.label} · {activeCheck.value} ({activeCheck.rule})
          </div>
          <p className="text-zinc-300 leading-relaxed">
            <strong className="text-white">Qué es:</strong> {activeCheck.what}
          </p>
          <p className="text-zinc-200 leading-relaxed">
            <strong
              className={
                activeCheck.status === "ok"
                  ? "text-emerald-300"
                  : activeCheck.status === "warn"
                  ? "text-amber-300"
                  : "text-rose-300"
              }
            >
              Qué significa aquí:
            </strong>{" "}
            {activeCheck.meaning}
          </p>
        </div>
      )}

      {showAll && (
        <div className="rounded-xl border border-cyan-400/30 bg-black/60 p-3 space-y-2 text-xs max-h-64 overflow-y-auto">
          {checks.map((c) => (
            <div key={c.label} className="border-b border-white/[0.06] last:border-0 pb-2 last:pb-0">
              <div className="flex items-center justify-between font-bold">
                <span className="text-cyan-200">{c.label}</span>
                <span
                  className={
                    c.status === "ok"
                      ? "text-emerald-300"
                      : c.status === "warn"
                      ? "text-amber-300"
                      : "text-rose-300"
                  }
                >
                  {c.value}
                </span>
              </div>
              <p className="text-zinc-400 text-[11px] mt-0.5">{c.what}</p>
              <p className="text-zinc-200 text-[11px] mt-0.5">{c.meaning}</p>
            </div>
          ))}
        </div>
      )}

      {audit.risks && audit.risks.length > 0 && (
        <div className="pt-1 flex flex-wrap gap-1.5">
          {audit.risks.map((r, i) => (
            <span
              key={`${r.name}-${i}`}
              title={r.description}
              className={`px-2 py-0.5 rounded-lg border text-[10px] font-medium ${
                r.level === "danger"
                  ? "border-rose-500/50 bg-rose-500/15 text-rose-200"
                  : "border-amber-500/40 bg-amber-500/15 text-amber-200"
              }`}
            >
              {r.name}
              {r.value ? `: ${r.value}` : ""}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function MetricBox({
  label,
  value,
  sub,
  accent = "text-slate-100",
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`text-sm font-bold tabular-nums mt-0.5 ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-400 mt-0.5">{sub}</div>}
    </div>
  );
}

function CandlestickChart({
  candles,
  timeframe,
}: {
  candles: OhlcvCandle[];
  timeframe: CandleTimeframe;
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const visible = candles.slice(-64);
  const width = 760;
  const priceHeight = 190;
  const volHeight = 40;
  const height = priceHeight + volHeight + 12;
  const padX = 12;
  const padTop = 12;

  const lows = visible.map((c) => c.low);
  const highs = visible.map((c) => c.high);
  const minP = Math.min(...lows);
  const maxP = Math.max(...highs);
  const rangeP = maxP - minP || maxP * 0.02 || 1e-9;
  const maxVol = Math.max(...visible.map((c) => c.volume), 1);

  const step = (width - padX * 2) / Math.max(visible.length, 1);
  const bodyW = Math.max(3, Math.min(11, step * 0.66));

  const yPrice = (p: number) =>
    padTop + ((maxP - p) / rangeP) * (priceHeight - padTop * 2);

  const activeCandle =
    hoverIdx !== null && visible[hoverIdx] ? visible[hoverIdx] : visible[visible.length - 1];
  const lastCandle = visible[visible.length - 1];
  const lastY = yPrice(lastCandle.close);
  const lastUp = lastCandle.close >= lastCandle.open;

  const fmtTime = (tsSec: number) => {
    const d = new Date(tsSec * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="space-y-2">
      {/* Live OHLCV Header Readout */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono tabular-nums px-1">
        <div className="flex flex-wrap items-center gap-3 text-zinc-300">
          <span className="text-zinc-500">{fmtTime(activeCandle.ts)} ({timeframe})</span>
          <span>O: <strong className="text-white">{fmtTokenPrice(activeCandle.open)}</strong></span>
          <span>H: <strong className="text-emerald-300">{fmtTokenPrice(activeCandle.high)}</strong></span>
          <span>L: <strong className="text-rose-300">{fmtTokenPrice(activeCandle.low)}</strong></span>
          <span>
            C:{" "}
            <strong className={activeCandle.close >= activeCandle.open ? "text-emerald-400" : "text-rose-400"}>
              {fmtTokenPrice(activeCandle.close)}
            </strong>
          </span>
        </div>
        <span className="text-zinc-400">
          Vol: <strong className="text-cyan-300">{fmtUsd(activeCandle.volume, true)}</strong>
        </span>
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-56 select-none overflow-visible"
        onMouseLeave={() => setHoverIdx(null)}
      >
        {/* Subtle horizontal price grid */}
        {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
          const y = padTop + frac * (priceHeight - padTop * 2);
          return (
            <line
              key={frac}
              x1={padX}
              x2={width - padX}
              y1={y}
              y2={y}
              stroke="rgba(255,255,255,0.06)"
              strokeDasharray="3 4"
            />
          );
        })}

        {/* Dashed current live price line */}
        <line
          x1={padX}
          x2={width - padX}
          y1={lastY}
          y2={lastY}
          stroke={lastUp ? "rgba(52, 211, 153, 0.55)" : "rgba(251, 113, 133, 0.55)"}
          strokeWidth="1"
          strokeDasharray="4 4"
        />

        {/* Candles + Volume bars */}
        {visible.map((c, idx) => {
          const cx = padX + idx * step + step / 2;
          const up = c.close >= c.open;
          const color = up ? "#34d399" : "#fb7185";
          const yHigh = yPrice(c.high);
          const yLow = yPrice(c.low);
          const yOpen = yPrice(c.open);
          const yClose = yPrice(c.close);
          const top = Math.min(yOpen, yClose);
          const bodyH = Math.max(1.8, Math.abs(yClose - yOpen));
          const vH = Math.max(1.5, (c.volume / maxVol) * volHeight);
          const vY = height - vH;
          const isHovered = hoverIdx === idx;

          return (
            <g
              key={c.ts}
              onMouseEnter={() => setHoverIdx(idx)}
              className="cursor-crosshair"
            >
              {/* Invisible wide hit target */}
              <rect
                x={cx - step / 2}
                y={0}
                width={step}
                height={height}
                fill={isHovered ? "rgba(255,255,255,0.05)" : "transparent"}
              />
              {/* Volume bar */}
              <rect
                x={cx - bodyW / 2}
                y={vY}
                width={bodyW}
                height={vH}
                rx={1}
                fill={up ? "rgba(52, 211, 153, 0.28)" : "rgba(251, 113, 133, 0.28)"}
              />
              {/* Wick */}
              <line
                x1={cx}
                x2={cx}
                y1={yHigh}
                y2={yLow}
                stroke={color}
                strokeWidth={isHovered ? 2 : 1.3}
              />
              {/* Candle body */}
              <rect
                x={cx - bodyW / 2}
                y={top}
                width={bodyW}
                height={bodyH}
                rx={1.2}
                fill={color}
                opacity={isHovered ? 1 : 0.92}
              />
            </g>
          );
        })}
      </svg>

      <div className="flex items-center justify-between font-mono text-[10px] text-zinc-400 tabular-nums px-1">
        <span>{fmtTime(visible[0].ts)} · Mín: {fmtTokenPrice(minP)}</span>
        <span>{visible.length} velas ({timeframe})</span>
        <span>Máx: {fmtTokenPrice(maxP)} · {fmtTime(lastCandle.ts)}</span>
      </div>
    </div>
  );
}

function LivePriceChart({ points }: { points: PricePoint[] }) {
  if (points.length === 0) {
    return (
      <div className="h-36 flex items-center justify-center font-mono text-xs text-slate-500">
        Esperando datos de precio...
      </div>
    );
  }

  const width = 640;
  const height = 140;
  const pad = 10;
  const values = points.map((p) => p.price_usd);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || min * 0.02 || 1e-9;

  const coords = values.map((v, i) => {
    const x =
      points.length === 1
        ? width - pad
        : pad + (i / (points.length - 1)) * (width - pad * 2);
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return { x, y };
  });

  const linePoints = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
  const areaPoints =
    coords.length > 1
      ? `${coords[0].x.toFixed(1)},${height - pad} ${linePoints} ${coords[coords.length - 1].x.toFixed(1)},${height - pad}`
      : "";
  const isUp = values[values.length - 1] >= values[0];
  const stroke = isUp ? "#34d399" : "#fb7185";
  const last = coords[coords.length - 1];

  return (
    <div className="space-y-1">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-36 overflow-visible">
        <defs>
          <linearGradient id="liveChartFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.32" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {areaPoints && <polygon points={areaPoints} fill="url(#liveChartFill)" />}
        {coords.length > 1 && (
          <polyline
            fill="none"
            stroke={stroke}
            strokeWidth="2.5"
            strokeLinejoin="round"
            strokeLinecap="round"
            points={linePoints}
          />
        )}
        <circle cx={last.x} cy={last.y} r="4.5" fill={stroke} />
      </svg>
      <div className="flex items-center justify-between font-mono text-[10px] text-slate-400 tabular-nums">
        <span>Min: {fmtTokenPrice(min)}</span>
        <span>Max: {fmtTokenPrice(max)}</span>
      </div>
    </div>
  );
}
