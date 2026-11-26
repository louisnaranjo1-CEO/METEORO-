import { useEffect, useState } from "react";
import {
  Activity,
  Award,
  Check,
  Copy,
  Database,
  ExternalLink,
  Fish,
  Globe,
  ShieldCheck,
  TrendingUp,
  X,
} from "lucide-react";
import { api } from "../api";
import { fmtSol, fmtUsd, shortAddr } from "../format";
import type { SmartWalletDetailResponse, SmartWalletItem } from "../types";

interface SmartWalletModalProps {
  wallet: SmartWalletItem | null;
  onClose: () => void;
}

function fmtHoldDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function fmtBlockAgo(blockTime: number | null): string {
  if (!blockTime) return "—";
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - blockTime));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d`;
}

export function SmartWalletModal({ wallet, onClose }: SmartWalletModalProps) {
  const [detail, setDetail] = useState<SmartWalletDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!wallet) {
      setDetail(null);
      return;
    }
    let active = true;
    setLoading(true);
    setDetail(null);
    api
      .smartWalletDetail(wallet.address)
      .then((res) => {
        if (active) setDetail(res);
      })
      .catch(() => {
        // Keep base wallet info visible even if live RPC times out
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [wallet]);

  if (!wallet) return null;

  const w = detail?.wallet ?? wallet;
  const live = detail?.live;
  const solUsd = detail?.sol_usd ?? 0;
  const liveSol = live?.live_sol_balance ?? w.sol_balance ?? null;
  const liveUsd = liveSol !== null && solUsd > 0 ? liveSol * solUsd : null;

  const links = detail?.links ?? {
    solscan: `https://solscan.io/account/${w.address}`,
    gmgn: `https://gmgn.ai/sol/address/${w.address}`,
    kolscan: `https://kolscan.io/account/${w.address}`,
    cielo: `https://app.cielo.finance/profile/${w.address}`,
    arkham: `https://intel.arkm.com/explorer/address/${w.address}`,
    twitter: w.twitter_username ? `https://x.com/${w.twitter_username}` : null,
    twitch: w.twitch ? `https://twitch.tv/${w.twitch}` : null,
  };

  function copyAddress() {
    navigator.clipboard?.writeText(w.address).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-3 sm:p-5"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[90vh] rounded-3xl glass-panel border-amber-400/35 overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-white/10 bg-white/[0.03] flex items-start justify-between gap-3">
          <div className="flex items-start gap-3.5 min-w-0">
            {w.avatar ? (
              <img
                src={w.avatar}
                alt={w.label}
                className="w-12 h-12 rounded-2xl object-cover border border-amber-400/40 bg-black shrink-0"
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = "none";
                }}
              />
            ) : (
              <div className="w-12 h-12 rounded-2xl border border-amber-400/35 bg-amber-500/15 flex items-center justify-center text-amber-300 shrink-0">
                <Fish className="w-6 h-6" />
              </div>
            )}
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base sm:text-lg font-bold text-white truncate">
                  {w.label || shortAddr(w.address, 6, 5)}
                </h3>
                {w.category && (
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/20 border border-amber-400/40 text-amber-200">
                    {w.category}
                  </span>
                )}
                {w.twitter_username && (
                  <a
                    href={links.twitter ?? `https://x.com/${w.twitter_username}`}
                    target="_blank"
                    rel="noreferrer"
                    className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-sky-500/15 border border-sky-400/40 text-sky-300 hover:bg-sky-500/25 inline-flex items-center gap-1"
                  >
                    @{w.twitter_username} <ExternalLink className="w-3 h-3" />
                  </a>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
                <span className="font-mono break-all text-zinc-300">{w.address}</span>
                <button
                  type="button"
                  onClick={copyAddress}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white/[0.06] hover:bg-white/10 text-zinc-200 cursor-pointer"
                >
                  {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                  {copied ? "Copiado" : "Copiar"}
                </button>
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-xl bg-white/[0.06] hover:bg-white/15 text-zinc-300 cursor-pointer shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 overflow-y-auto space-y-5">
          {/* Bio */}
          {w.twitter_description && (
            <p className="text-xs text-zinc-300 leading-relaxed rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
              {w.twitter_description}
            </p>
          )}

          {/* Explorer & Social Links */}
          <section className="space-y-2">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
              <Globe className="w-3.5 h-3.5 text-cyan-300" /> Exploradores On-Chain (Equiv. Etherscan en Solana) y Redes
            </div>
            <div className="flex flex-wrap gap-2">
              <ExtPill href={links.solscan} label="Solscan (Explorador Solana)" tone="cyan" />
              <ExtPill href={links.gmgn} label="GMGN.ai (PnL & Winrate Live)" tone="emerald" />
              <ExtPill href={links.kolscan} label="Kolscan Leaderboard" tone="amber" />
              <ExtPill href={links.cielo} label="Cielo Finance" tone="violet" />
              <ExtPill href={links.arkham} label="Arkham Intel" tone="zinc" />
              {links.twitter && (
                <ExtPill href={links.twitter} label={`X / Twitter (@${w.twitter_username})`} tone="sky" />
              )}
              {links.twitch && (
                <ExtPill href={links.twitch} label={`Twitch (${w.twitch})`} tone="violet" />
              )}
            </div>
          </section>

          {/* Performance & Winrate Stats */}
          <section className="space-y-2.5">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
              <Award className="w-3.5 h-3.5 text-emerald-300" /> Rendimiento Verificado y Rating de Ganancias
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
              <StatTile
                label="Balance SOL (En Vivo)"
                value={liveSol !== null ? fmtSol(liveSol, 3) : "—"}
                sub={liveUsd !== null ? fmtUsd(liveUsd, true) : loading ? "Consultando RPC..." : "Solana Mainnet"}
                accent="text-cyan-300"
              />
              <StatTile
                label="Ganancia 30D"
                value={w.realized_profit_30d != null ? fmtUsd(w.realized_profit_30d, true) : "—"}
                sub={w.pnl_30d_pct != null ? `ROI 30D: ${w.pnl_30d_pct >= 0 ? "+" : ""}${w.pnl_30d_pct}%` : undefined}
                accent={(w.realized_profit_30d ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}
              />
              <StatTile
                label="Ganancia 7D"
                value={w.realized_profit_7d != null ? fmtUsd(w.realized_profit_7d, true) : "—"}
                sub={w.pnl_7d_pct != null ? `ROI 7D: ${w.pnl_7d_pct >= 0 ? "+" : ""}${w.pnl_7d_pct}%` : undefined}
                accent={(w.realized_profit_7d ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}
              />
              <StatTile
                label="Winrate (Acierto)"
                value={
                  w.winrate_30d != null
                    ? `${w.winrate_30d}%`
                    : w.winrate_7d != null
                    ? `${w.winrate_7d}%`
                    : "—"
                }
                sub={w.winrate_7d != null ? `7D: ${w.winrate_7d}%` : "Rating histórico"}
                accent="text-amber-300"
              />
              <StatTile
                label="Trades 30D"
                value={w.txs_30d != null && w.txs_30d > 0 ? w.txs_30d.toLocaleString() : "—"}
                sub={
                  w.buys_30d || w.sells_30d
                    ? `${w.buys_30d ?? 0} compras · ${w.sells_30d ?? 0} ventas`
                    : undefined
                }
              />
              <StatTile
                label="Aciertos 2x–5x (7D)"
                value={w.hits_2x_5x_7d != null ? String(w.hits_2x_5x_7d) : "—"}
                accent="text-emerald-300"
              />
              <StatTile
                label="Moonshots >5x (7D)"
                value={w.hits_gt_5x_7d != null ? String(w.hits_gt_5x_7d) : "—"}
                accent="text-violet-300"
              />
              <StatTile
                label="Hold Promedio"
                value={fmtHoldDuration(w.avg_holding_sec_7d)}
                sub={w.follow_count ? `${w.follow_count.toLocaleString()} seguidores GMGN` : undefined}
              />
            </div>
          </section>

          {/* Recent Live On-Chain Transactions */}
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
                <Activity className="w-3.5 h-3.5 text-cyan-300" /> Últimos Movimientos On-Chain (Solana RPC en vivo)
              </span>
              {loading && <span className="text-[10px] text-cyan-300 animate-pulse">Consultando blockchain...</span>}
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.025] divide-y divide-white/[0.06] overflow-hidden">
              {!live || live.recent_txs.length === 0 ? (
                <div className="px-4 py-4 text-xs text-zinc-400 text-center">
                  {loading
                    ? "Cargando firmas recientes desde Solana..."
                    : "Sin transacciones recientes devueltas por el RPC público (abre Solscan o GMGN arriba para historial completo)."}
                </div>
              ) : (
                live.recent_txs.map((tx) => (
                  <a
                    key={tx.signature}
                    href={tx.solscan_tx_url}
                    target="_blank"
                    rel="noreferrer"
                    className="px-4 py-2.5 flex items-center justify-between gap-3 hover:bg-white/[0.05] transition text-xs"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className={`w-2 h-2 rounded-full shrink-0 ${
                          tx.err ? "bg-rose-400" : "bg-emerald-400"
                        }`}
                      />
                      <span className="font-mono text-zinc-200 truncate">
                        {shortAddr(tx.signature, 12, 10)}
                      </span>
                      <span className="text-[10px] text-zinc-500">
                        {tx.err ? "Fallida" : "Confirmada"}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-zinc-400 shrink-0">
                      <span>hace {fmtBlockAgo(tx.block_time)}</span>
                      <ExternalLink className="w-3 h-3 text-cyan-300" />
                    </div>
                  </a>
                ))
              )}
            </div>
          </section>

          {/* Source Attribution & Verification */}
          <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 space-y-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5">
                <Database className="w-3.5 h-3.5 text-amber-300" /> Origen y Verificación de la Información
              </span>
              <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-400/30 text-emerald-300">
                <ShieldCheck className="w-3 h-3" /> Pubkey Base58 Validada
              </span>
            </div>
            <p className="text-xs text-zinc-400 leading-relaxed">
              <strong>Fuente:</strong> {w.source ?? "Añadida manualmente / Leaderboard Solana"}{" "}
              {w.snapshot_date ? `· Corte de métricas: ${w.snapshot_date}` : ""}. El balance y las transacciones se consultan en vivo contra Solana RPC; puedes auditar el PnL exacto al segundo haciendo clic en <strong>GMGN.ai</strong> o <strong>Solscan</strong>.
            </p>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {w.source_url && (
                <a
                  href={w.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] text-cyan-300 hover:underline"
                >
                  <TrendingUp className="w-3 h-3" /> Ver dataset público original <ExternalLink className="w-3 h-3" />
                </a>
              )}
              {w.tags && w.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 ml-auto">
                  {w.tags.map((t) => (
                    <span
                      key={t}
                      className="px-2 py-0.5 rounded-md bg-white/[0.06] text-[10px] text-zinc-300"
                    >
                      #{t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function StatTile({
  label,
  value,
  sub,
  accent = "text-white",
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2.5">
      <div className="text-[10px] text-zinc-400 uppercase tracking-wide">{label}</div>
      <div className={`text-sm font-bold tabular-nums mt-0.5 ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-zinc-400 mt-0.5 truncate">{sub}</div>}
    </div>
  );
}

function ExtPill({
  href,
  label,
  tone,
}: {
  href: string;
  label: string;
  tone: "cyan" | "emerald" | "amber" | "violet" | "sky" | "zinc";
}) {
  const styles = {
    cyan: "border-cyan-400/35 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20",
    emerald: "border-emerald-400/35 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20",
    amber: "border-amber-400/35 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20",
    violet: "border-violet-400/35 bg-violet-500/10 text-violet-200 hover:bg-violet-500/20",
    sky: "border-sky-400/35 bg-sky-500/10 text-sky-200 hover:bg-sky-500/20",
    zinc: "border-white/15 bg-white/[0.06] text-zinc-200 hover:bg-white/10",
  }[tone];
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={`px-3 py-1.5 rounded-xl border text-xs font-medium inline-flex items-center gap-1.5 transition ${styles}`}
    >
      {label} <ExternalLink className="w-3 h-3 opacity-80" />
    </a>
  );
}
