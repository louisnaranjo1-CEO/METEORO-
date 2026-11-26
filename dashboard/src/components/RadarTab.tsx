import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Clock,
  Cpu,
  Flame,
  Radar,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import type { RadarCardItem, RadarResponse, VerdictView } from "../types";
import { fmtAgo, fmtProb, fmtUsd, shortAddr } from "../format";

interface RadarTabProps {
  radar: RadarResponse | null;
  busyId: number | null;
  onSelectToken: (mint: string) => void;
  onApprove: (leadId: number) => Promise<void>;
  onReject: (leadId: number) => Promise<void>;
}

const LANE_META = [
  {
    key: "SEARCH" as const,
    title: "SEARCH",
    subtitle: "DexScreener $50K–$1.5M",
    accent: "text-cyan-400 border-cyan-500/40 bg-cyan-500/10",
    dot: "bg-cyan-400",
    icon: Radar,
  },
  {
    key: "RISK_WHALE" as const,
    title: "RISK & WHALE",
    subtitle: "RugCheck + Smart Money",
    accent: "text-amber-300 border-amber-500/40 bg-amber-500/10",
    dot: "bg-amber-400",
    icon: ShieldCheck,
  },
  {
    key: "JUDGE" as const,
    title: "JUDGE (JUEZ)",
    subtitle: "Juez Mudo + Gate",
    accent: "text-violet-300 border-violet-500/40 bg-violet-500/10",
    dot: "bg-violet-400",
    icon: Cpu,
  },
  {
    key: "POSITION" as const,
    title: "EN POSICIÓN",
    subtitle: "Abiertas & Runners 2X",
    accent: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
    dot: "bg-emerald-400",
    icon: Flame,
  },
];

export function RadarTab({ radar, busyId, onSelectToken, onApprove, onReject }: RadarTabProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
      {LANE_META.map((lane) => {
        const items = radar?.lanes[lane.key] ?? [];
        const Icon = lane.icon;
        return (
          <section
            key={lane.key}
            className="rounded-3xl glass-panel flex flex-col min-h-[420px] max-h-[78vh] overflow-hidden"
          >
            <div className="px-4 py-3.5 border-b border-white/10 flex items-center justify-between bg-white/[0.02]">
              <div className="flex items-center gap-2.5">
                <span className={`p-1.5 rounded-xl border ${lane.accent}`}>
                  <Icon className="w-4 h-4" />
                </span>
                <div>
                  <div className="text-xs font-semibold tracking-tight text-white">
                    {lane.title}
                  </div>
                  <div className="text-[11px] text-zinc-400">{lane.subtitle}</div>
                </div>
              </div>
              <span className="font-mono text-xs px-2.5 py-0.5 rounded-full bg-white/10 text-zinc-200 tabular-nums">
                {items.length}
              </span>
            </div>

            <div className="p-3 space-y-2.5 overflow-y-auto flex-1">
              {items.length === 0 ? (
                <div className="h-32 flex flex-col items-center justify-center text-center text-xs text-zinc-500 border border-dashed border-white/10 rounded-2xl p-4">
                  <span className={`w-2 h-2 rounded-full mb-2 opacity-50 ${lane.dot}`} />
                  Sin tokens en {lane.title}
                </div>
              ) : (
                items.map((item) => (
                  <TacticalCard
                    key={item.id}
                    item={item}
                    nowMs={nowMs}
                    busy={busyId === item.id}
                    onSelect={() => onSelectToken(item.mint_address)}
                    onApprove={onApprove}
                    onReject={onReject}
                  />
                ))
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function getStageVisuals(stage: string) {
  switch (stage) {
    case "SEARCH":
      return {
        card: "border-cyan-500/45 border-l-4 border-l-cyan-400 bg-cyan-950/15 hover:border-cyan-400/70",
        badge: "bg-cyan-500/20 border-cyan-400/50 text-cyan-200",
        label: "SEARCH",
        bar: "from-cyan-500 to-sky-400",
      };
    case "RISK":
      return {
        card: "border-amber-500/45 border-l-4 border-l-amber-400 bg-amber-950/15 hover:border-amber-400/70",
        badge: "bg-amber-500/20 border-amber-400/50 text-amber-200",
        label: "RISK OK",
        bar: "from-amber-500 to-yellow-400",
      };
    case "WHALE":
      return {
        card: "border-orange-500/50 border-l-4 border-l-orange-400 bg-orange-950/20 hover:border-orange-400/80",
        badge: "bg-orange-500/20 border-orange-400/60 text-orange-200",
        label: "WHALE",
        bar: "from-orange-500 to-amber-400",
      };
    case "JUDGE":
      return {
        card: "border-violet-500/50 border-l-4 border-l-violet-400 bg-violet-950/20 hover:border-violet-400/80",
        badge: "bg-violet-500/20 border-violet-400/60 text-violet-200",
        label: "JUEZ",
        bar: "from-violet-500 to-fuchsia-400",
      };
    case "GATE":
      return {
        card: "border-indigo-400/60 border-l-4 border-l-indigo-400 bg-indigo-950/25",
        badge: "bg-indigo-500/25 border-indigo-400/60 text-indigo-200",
        label: "GATE OK",
        bar: "from-indigo-400 to-cyan-400",
      };
    case "PENDING_APPROVAL":
      return {
        card: "border-amber-400 border-l-4 border-l-amber-300 bg-amber-950/30 shadow-lg shadow-amber-950/40",
        badge: "bg-amber-400/25 border-amber-300 text-amber-200 animate-pulse",
        label: "APROBAR",
        bar: "from-amber-400 to-yellow-300",
      };
    case "RISK_FREE_RUNNER":
      return {
        card: "border-emerald-400 border-l-4 border-l-emerald-300 bg-emerald-950/30 shadow-lg shadow-emerald-950/40",
        badge: "bg-emerald-400/25 border-emerald-300 text-emerald-200",
        label: "2X FREE",
        bar: "from-emerald-400 to-teal-300",
      };
    case "OPEN":
      return {
        card: "border-emerald-500/50 border-l-4 border-l-emerald-400 bg-emerald-950/20",
        badge: "bg-emerald-500/20 border-emerald-400/50 text-emerald-200",
        label: "ABIERTO",
        bar: "from-emerald-500 to-green-400",
      };
    default:
      return {
        card: "border-slate-800 border-l-4 border-l-slate-600 bg-slate-950/80",
        badge: "bg-slate-800 border-slate-700 text-slate-300",
        label: stage,
        bar: "from-cyan-500 to-emerald-400",
      };
  }
}

function TacticalCard({
  item,
  nowMs,
  busy,
  onSelect,
  onApprove,
  onReject,
}: {
  item: RadarCardItem;
  nowMs: number;
  busy: boolean;
  onSelect: () => void;
  onApprove: (leadId: number) => Promise<void>;
  onReject: (leadId: number) => Promise<void>;
}) {
  const isPending = item.stage === "PENDING_APPROVAL";
  const visuals = getStageVisuals(item.stage);
  const [prevMc, setPrevMc] = useState<number | null>(item.market_cap);
  const [tickClass, setTickClass] = useState<"" | "tick-up" | "tick-down">("");
  const [imgErr, setImgErr] = useState(false);

  useEffect(() => {
    if (
      item.market_cap !== null &&
      prevMc !== null &&
      Math.abs(item.market_cap - prevMc) > 0.01
    ) {
      setTickClass(item.market_cap > prevMc ? "tick-up" : "tick-down");
      const t = window.setTimeout(() => setTickClass(""), 850);
      setPrevMc(item.market_cap);
      return () => window.clearTimeout(t);
    }
    if (item.market_cap !== prevMc) {
      setPrevMc(item.market_cap);
    }
  }, [item.market_cap, prevMc]);

  const liqRatio =
    item.market_cap && item.liquidity_usd && item.market_cap > 0
      ? Math.min(100, Math.round((item.liquidity_usd / item.market_cap) * 100 * 2.5))
      : 0;

  let remainingSec = 0;
  let progressPct = 0;
  if (isPending && item.approval_expires_at) {
    const expMs = new Date(item.approval_expires_at).getTime();
    remainingSec = Math.max(0, Math.ceil((expMs - nowMs) / 1000));
    progressPct = Math.min(100, Math.max(0, (remainingSec / 90) * 100));
  }

  return (
    <article
      onClick={onSelect}
      className={`animate-radar-in rounded-2xl glass-card border p-3.5 transition-all duration-300 cursor-pointer hover:bg-white/[0.075] ${visuals.card}`}
    >
      <div className="flex items-start justify-between gap-2.5">
        <div className="flex items-center gap-2.5 min-w-0">
          {item.image_url && !imgErr ? (
            <img
              src={item.image_url}
              alt={item.symbol}
              onError={() => setImgErr(true)}
              className="h-9 w-9 rounded-xl border border-white/15 object-cover bg-black shrink-0"
            />
          ) : (
            <div className="h-9 w-9 rounded-xl border border-white/15 bg-white/[0.06] flex items-center justify-center font-mono text-[11px] font-bold text-zinc-200 shrink-0">
              {item.symbol.slice(0, 3)}
            </div>
          )}
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-sm text-white truncate">
                ${item.symbol}
              </span>
              <span
                className={`px-1.5 py-0.5 rounded-md border text-[10px] font-mono font-semibold ${visuals.badge}`}
              >
                {visuals.label}
              </span>
            </div>
            <div className="font-mono text-[10px] text-zinc-400">
              {shortAddr(item.mint_address, 5, 4)} · {fmtAgo(item.updated_at)}
            </div>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div
            className={`font-mono text-xs font-semibold text-white tabular-nums px-1 rounded ${tickClass}`}
          >
            {fmtUsd(item.market_cap, true)}
          </div>
          <div className="text-[10px] text-zinc-500 uppercase">MCap</div>
        </div>
      </div>

      {/* Liquidity / Pool Concentration Bar */}
      <div className="mt-2.5 space-y-1">
        <div className="flex items-center justify-between text-[10px] font-mono text-slate-300">
          <span>LIQ {fmtUsd(item.liquidity_usd, true)}</span>
          <span>
            POOL{" "}
            {item.main_pool_share !== null && item.main_pool_share !== undefined
              ? `${Math.round(item.main_pool_share * 100)}%`
              : "—"}
            {item.holders_count ? ` · ${item.holders_count}H` : ""}
          </span>
        </div>
        <div className="h-1.5 w-full rounded-full bg-slate-800/90 overflow-hidden">
          <div
            className={`h-full rounded-full bg-gradient-to-r transition-all duration-500 ${visuals.bar}`}
            style={{ width: `${Math.max(8, liqRatio)}%` }}
          />
        </div>
      </div>

      {/* 5 Mute Judge Answers Meter */}
      {item.verdict && <JudgeMeter verdict={item.verdict} />}

      {/* Semi-Auto Approval Countdown & Buttons */}
      {isPending && (
        <div className="mt-3 pt-2.5 border-t border-amber-500/30 space-y-2">
          <div className="flex items-center justify-between text-[11px] font-mono text-amber-300">
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" /> Ventana Semi-Auto
            </span>
            <span className="font-bold tabular-nums">{remainingSec}s</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-slate-800 overflow-hidden">
            <div
              className="h-full bg-amber-400 transition-all duration-1000"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <div className="grid grid-cols-2 gap-2" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              disabled={busy || remainingSec === 0}
              onClick={() => void onApprove(item.id)}
              className="py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-mono text-xs font-bold tracking-wide cursor-pointer disabled:opacity-40"
            >
              APROBAR
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void onReject(item.id)}
              className="py-1.5 rounded-lg border border-rose-500/50 bg-rose-950/60 hover:bg-rose-600/30 text-rose-300 font-mono text-xs font-bold tracking-wide cursor-pointer disabled:opacity-40"
            >
              DESCARTAR
            </button>
          </div>
        </div>
      )}
    </article>
  );
}

function JudgeMeter({ verdict }: { verdict: VerdictView }) {
  const pills = [
    {
      label: "ORG",
      ok: verdict.organic === "yes",
      value: fmtProb(verdict.organic_prob),
    },
    {
      label: "LATE",
      ok: verdict.late === "no",
      value: fmtProb(verdict.late_prob),
    },
    {
      label: "EXIT",
      ok: (verdict.exit_risk ?? 5) <= 3,
      value: verdict.exit_risk ? `${verdict.exit_risk}/5` : "—",
    },
    {
      label: "TRAP",
      ok: verdict.manipulation_trap === "no",
      value: fmtProb(verdict.trap_prob),
    },
    {
      label: (verdict.action ?? "pass").toUpperCase(),
      ok: verdict.action === "buy",
      value: fmtProb(verdict.action_prob),
    },
  ];

  return (
    <div className="mt-2.5 pt-2 border-t border-slate-800/90 grid grid-cols-5 gap-1">
      {pills.map((p) => (
        <div
          key={p.label}
          className={`rounded-md border px-1 py-1 text-center font-mono ${
            p.ok
              ? "border-emerald-500/35 bg-emerald-950/35 text-emerald-300"
              : "border-rose-500/35 bg-rose-950/35 text-rose-300"
          }`}
        >
          <div className="flex items-center justify-center gap-0.5 text-[9px] font-bold">
            {p.ok ? (
              <CheckCircle2 className="w-2.5 h-2.5" />
            ) : (
              <XCircle className="w-2.5 h-2.5" />
            )}
            <span>{p.label}</span>
          </div>
          <div className="text-[10px] tabular-nums">{p.value}</div>
        </div>
      ))}
    </div>
  );
}
