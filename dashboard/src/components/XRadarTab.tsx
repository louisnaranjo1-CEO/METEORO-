import { Activity, BadgeCheck, ExternalLink, Sparkles } from "lucide-react";
import type { FlowSnapshot, XFeedResponse } from "../types";
import { fmtAgo, fmtFollowers, fmtUsd, shortAddr } from "../format";

interface XRadarTabProps {
  feed: XFeedResponse | null;
}

export function XRadarTab({ feed }: XRadarTabProps) {
  const groups = feed?.groups ?? [];
  const activeNoMentions = feed?.active_without_mentions ?? [];

  return (
    <div className="space-y-5">
      {/* Status Banner */}
      <div className="rounded-3xl glass-panel px-4 py-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <Sparkles className="w-4 h-4 text-violet-400" />
          <div className="text-xs text-slate-200">
            <span className="font-bold uppercase tracking-wider">Radar X y Catalizadores</span>{" "}
            <span className="text-slate-400">
              — Ley inquebrantable: el volumen real (Compras vs. Ventas) siempre prevalece sobre X.
            </span>
          </div>
        </div>
        <span
          className={`px-2.5 py-1 rounded-full font-mono text-[11px] border ${
            feed?.enabled
              ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
              : "bg-slate-800 border-slate-700 text-slate-400"
          }`}
        >
          {feed?.enabled ? "X RADAR ACTIVO EN RUNNERS" : "X RADAR DESACTIVADO (.env)"}
        </span>
      </div>

      {groups.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-900/35 p-10 text-center space-y-2">
          <div className="text-sm font-semibold text-slate-300">
            Sin menciones cacheadas de X en las últimas 24h
          </div>
          <p className="text-xs text-slate-500 max-w-md mx-auto">
            Activa <code className="font-mono text-cyan-300">ENABLE_X_CONTEXT_ON_RUNNER=true</code>{" "}
            y configura <code className="font-mono text-cyan-300">X_API_URL</code> en{" "}
            <code className="font-mono">.env</code> para monitorear catalizadores en tokens{" "}
            <code className="font-mono">RISK_FREE_RUNNER</code>.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {groups.map((g) => (
            <section
              key={g.mint_address}
              className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 space-y-3"
            >
              <div className="flex flex-wrap items-start justify-between gap-2 pb-2.5 border-b border-slate-800">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-base font-bold text-slate-100">
                      ${g.symbol || shortAddr(g.mint_address, 4, 4)}
                    </span>
                    {g.status && (
                      <span className="px-2 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/40 font-mono text-[10px] text-emerald-300">
                        {g.status}
                      </span>
                    )}
                    {g.notable_count > 0 && (
                      <span className="px-2 py-0.5 rounded-full bg-amber-500/20 border border-amber-400/60 font-mono text-[10px] font-bold text-amber-200">
                        {g.notable_count} FIGURA RELEVANTE
                      </span>
                    )}
                  </div>
                  <div className="font-mono text-[11px] text-slate-500">
                    {shortAddr(g.mint_address, 6, 4)}
                  </div>
                </div>

                {/* Real Volume & Orderflow Contrast */}
                <RealVolumeBar flow={g.flow} />
              </div>

              <div className="space-y-2.5 max-h-80 overflow-y-auto pr-1">
                {g.mentions.map((m, idx) => (
                  <article
                    key={`${m.author_handle}-${idx}`}
                    className={`rounded-xl border p-3 space-y-1.5 ${
                      m.is_notable_figure
                        ? "border-amber-400/50 bg-amber-950/20"
                        : "border-slate-800 bg-slate-950/70"
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 font-mono text-xs">
                        <span className="font-bold text-slate-100">@{m.author_handle}</span>
                        {m.is_verified && <BadgeCheck className="w-3.5 h-3.5 text-cyan-400" />}
                        <span className="text-slate-400">
                          {fmtFollowers(m.author_followers)} seg.
                        </span>
                      </div>

                      <div className="flex items-center gap-2">
                        {m.is_notable_figure && (
                          <span className="px-2 py-0.5 rounded-full bg-amber-500/25 border border-amber-400/60 font-mono text-[10px] font-bold text-amber-200 uppercase">
                            FIGURA RELEVANTE / {m.notable_tag ?? "ALTO ALCANCE"}
                          </span>
                        )}
                        {m.url && (
                          <a
                            href={m.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-slate-400 hover:text-cyan-300"
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                          </a>
                        )}
                      </div>
                    </div>
                    <p className="text-xs text-slate-200 leading-relaxed">{m.text}</p>
                    <div className="font-mono text-[10px] text-slate-500">
                      {fmtAgo(m.fetched_at)}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {activeNoMentions.length > 0 && (
        <section className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4 space-y-2.5">
          <div className="text-xs font-bold uppercase tracking-wider text-slate-400">
            Posiciones Activas sin Menciones en X ({activeNoMentions.length})
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {activeNoMentions.map((a) => (
              <div
                key={a.mint_address}
                className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 flex items-center justify-between gap-2"
              >
                <div>
                  <div className="font-mono text-sm font-bold text-slate-100">${a.symbol}</div>
                  <div className="font-mono text-[10px] text-emerald-400">{a.status}</div>
                </div>
                <RealVolumeBar flow={a.flow} />
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function RealVolumeBar({ flow }: { flow: FlowSnapshot | null }) {
  const buys = flow?.buys_5m ?? null;
  const sells = flow?.sells_5m ?? null;
  const vol5m = flow?.volume_5m_usd ?? null;
  const totalTx = (buys ?? 0) + (sells ?? 0);
  const buyPct = totalTx > 0 && buys !== null ? Math.round((buys / totalTx) * 100) : null;

  return (
    <div className="min-w-[165px] rounded-lg border border-slate-800 bg-slate-950/80 px-2.5 py-1.5 font-mono">
      <div className="flex items-center justify-between text-[10px] text-slate-400">
        <span className="flex items-center gap-1">
          <Activity className="w-3 h-3 text-cyan-400" /> VOL REAL 5m
        </span>
        <span className="text-slate-200 font-semibold">{fmtUsd(vol5m, true)}</span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded-full bg-rose-500/40 overflow-hidden">
        <div
          className="h-full bg-emerald-400 transition-all"
          style={{ width: `${buyPct ?? 50}%` }}
        />
      </div>
      <div className="mt-1 flex items-center justify-between text-[10px]">
        <span className="text-emerald-400">C: {buys ?? "—"}</span>
        <span className="text-rose-400">V: {sells ?? "—"}</span>
      </div>
    </div>
  );
}
