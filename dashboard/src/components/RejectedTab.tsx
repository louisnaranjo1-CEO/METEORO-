import { useMemo, useState, type FormEvent } from "react";
import { Ban, Fish, Plus, Search, Trash2 } from "lucide-react";
import type { BlacklistItem, RejectedItem, SmartWalletItem } from "../types";
import { fmtAgo, fmtUsd, shortAddr } from "../format";
import { SmartWalletModal } from "./SmartWalletModal";

interface RejectedTabProps {
  rejected: RejectedItem[];
  blacklist: BlacklistItem[];
  smartWallets: SmartWalletItem[];
  onSelectToken: (mint: string) => void;
  onAddBlacklist: (wallet: string, reason: string, mint?: string) => Promise<void>;
  onRemoveBlacklist: (wallet: string) => Promise<void>;
  onAddSmartWallet: (address: string, label: string) => Promise<void>;
  onRemoveSmartWallet: (address: string) => Promise<void>;
}

const PHASES = ["ALL", "RISK", "JUDGE", "GATE", "APPROVAL"] as const;

export function RejectedTab({
  rejected,
  blacklist,
  smartWallets,
  onSelectToken,
  onAddBlacklist,
  onRemoveBlacklist,
  onAddSmartWallet,
  onRemoveSmartWallet,
}: RejectedTabProps) {
  const [phaseFilter, setPhaseFilter] = useState<(typeof PHASES)[number]>("ALL");
  const [query, setQuery] = useState("");

  const [devWallet, setDevWallet] = useState("");
  const [devReason, setDevReason] = useState("MANUAL");
  const [devMint, setDevMint] = useState("");
  const [swAddr, setSwAddr] = useState("");
  const [swLabel, setSwLabel] = useState("");
  const [swQuery, setSwQuery] = useState("");
  const [swCategory, setSwCategory] = useState<string>("ALL");
  const [selectedWallet, setSelectedWallet] = useState<SmartWalletItem | null>(null);
  const [busy, setBusy] = useState(false);

  const filteredWallets = useMemo(() => {
    const q = swQuery.trim().toLowerCase();
    return smartWallets.filter((w) => {
      if (swCategory !== "ALL" && (w.category ?? "") !== swCategory) return false;
      if (!q) return true;
      return (
        w.address.toLowerCase().includes(q) ||
        w.label.toLowerCase().includes(q) ||
        (w.twitter_username ?? "").toLowerCase().includes(q) ||
        (w.category ?? "").toLowerCase().includes(q)
      );
    });
  }, [smartWallets, swQuery, swCategory]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rejected.filter((item) => {
      if (phaseFilter !== "ALL" && item.phase !== phaseFilter) return false;
      if (!q) return true;
      return (
        item.symbol.toLowerCase().includes(q) ||
        item.mint_address.toLowerCase().includes(q) ||
        (item.kill_reason ?? "").toLowerCase().includes(q)
      );
    });
  }, [rejected, phaseFilter, query]);

  async function handleAddBlacklist(e: FormEvent) {
    e.preventDefault();
    if (!devWallet.trim()) return;
    setBusy(true);
    try {
      await onAddBlacklist(devWallet.trim(), devReason.trim() || "MANUAL", devMint.trim() || undefined);
      setDevWallet("");
      setDevMint("");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddSmartWallet(e: FormEvent) {
    e.preventDefault();
    if (!swAddr.trim()) return;
    setBusy(true);
    try {
      await onAddSmartWallet(swAddr.trim(), swLabel.trim());
      setSwAddr("");
      setSwLabel("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-12 gap-5">
      {/* Left: Filterable Rejected Tokens */}
      <section className="xl:col-span-7 rounded-3xl glass-panel flex flex-col overflow-hidden">
        <div className="p-4 border-b border-slate-800 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100">
              Tokens Descartados ({filtered.length} / últimos 50)
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {PHASES.map((ph) => (
                <button
                  key={ph}
                  type="button"
                  onClick={() => setPhaseFilter(ph)}
                  className={`px-2.5 py-1 rounded-md font-mono text-[11px] border transition cursor-pointer ${
                    phaseFilter === ph
                      ? "bg-rose-500/20 border-rose-500/60 text-rose-200"
                      : "border-slate-800 bg-slate-950 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {ph === "ALL" ? "TODOS" : ph}
                </button>
              ))}
            </div>
          </div>

          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filtrar por ticker, mint o motivo (ej. FRAGMENTED_LIQUIDITY, LATE_ENTRY)..."
              className="w-full rounded-lg border border-slate-800 bg-slate-950 pl-9 pr-3 py-2 text-xs text-slate-200 placeholder:text-slate-500 focus:border-cyan-400 focus:outline-none"
            />
          </div>
        </div>

        <div className="divide-y divide-slate-800/70 max-h-[68vh] overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="p-10 text-center text-xs text-slate-500">
              Sin descartes que coincidan con el filtro actual.
            </div>
          ) : (
            filtered.map((item) => (
              <div
                key={item.id}
                onClick={() => onSelectToken(item.mint_address)}
                className="px-4 py-3 flex flex-wrap items-center justify-between gap-2 hover:bg-slate-950/70 cursor-pointer transition"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-sm text-slate-100">
                      ${item.symbol}
                    </span>
                    <span className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] font-mono text-slate-300">
                      {item.phase}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-rose-950/80 border border-rose-500/40 font-mono text-[10px] font-semibold text-rose-300">
                      {item.kill_reason ?? "REJECTED"}
                    </span>
                  </div>
                  <div className="font-mono text-[11px] text-slate-500 mt-0.5">
                    {shortAddr(item.mint_address, 6, 4)} · MCap {fmtUsd(item.market_cap, true)} ·
                    Liq {fmtUsd(item.liquidity_usd, true)}
                    {item.verdict?.late === "yes" ? " · late=yes" : ""}
                    {item.verdict?.manipulation_trap === "yes" ? " · trap=yes" : ""}
                  </div>
                </div>
                <div className="font-mono text-[11px] text-slate-500">
                  {fmtAgo(item.updated_at)}
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      {/* Right: Blacklisted Devs + Smart Wallets */}
      <div className="xl:col-span-5 space-y-5">
        {/* Blacklist Panel */}
        <section className="rounded-3xl glass-panel border-rose-500/30 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Ban className="w-4 h-4 text-rose-400" />
              <h2 className="text-xs font-bold uppercase tracking-wider text-rose-200">
                Wallets Bloqueadas (blacklisted_devs · {blacklist.length})
              </h2>
            </div>
          </div>

          <form onSubmit={handleAddBlacklist} className="grid grid-cols-1 sm:grid-cols-12 gap-2">
            <input
              type="text"
              required
              minLength={20}
              value={devWallet}
              onChange={(e) => setDevWallet(e.target.value)}
              placeholder="Wallet deployer Solana..."
              className="sm:col-span-6 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-100 focus:border-rose-400 focus:outline-none"
            />
            <input
              type="text"
              value={devReason}
              onChange={(e) => setDevReason(e.target.value)}
              placeholder="Motivo"
              className="sm:col-span-4 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-rose-400 focus:outline-none"
            />
            <button
              type="submit"
              disabled={busy || devWallet.trim().length < 20}
              className="sm:col-span-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold flex items-center justify-center gap-1 py-1.5 cursor-pointer disabled:opacity-40"
            >
              <Plus className="w-3.5 h-3.5" /> Add
            </button>
          </form>

          <div className="space-y-1.5 max-h-56 overflow-y-auto">
            {blacklist.length === 0 ? (
              <p className="text-xs text-slate-500 py-3 text-center">
                Sin deployers en lista negra.
              </p>
            ) : (
              blacklist.map((b) => (
                <div
                  key={b.dev_wallet}
                  className="rounded-lg border border-slate-800 bg-slate-950/75 px-3 py-2 flex items-center justify-between gap-2"
                >
                  <div className="min-w-0">
                    <div className="font-mono text-xs text-rose-300 truncate">
                      {shortAddr(b.dev_wallet, 8, 6)}
                    </div>
                    <div className="font-mono text-[10px] text-slate-500">
                      {b.reason}
                      {b.offending_mint ? ` · mint ${shortAddr(b.offending_mint, 5, 4)}` : ""}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void onRemoveBlacklist(b.dev_wallet)}
                    title="Quitar de blacklist"
                    className="p-1.5 rounded-md text-slate-400 hover:text-rose-300 hover:bg-rose-950/50 cursor-pointer"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))
            )}
          </div>
        </section>

        {/* Smart Wallets Panel */}
        <section className="rounded-3xl glass-panel border-amber-500/30 p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Fish className="w-4 h-4 text-amber-400" />
              <h2 className="text-xs font-bold uppercase tracking-wider text-amber-200">
                Smart Wallets (WHALE · {filteredWallets.length} / {smartWallets.length})
              </h2>
            </div>
            <span className="text-[10px] text-zinc-400">
              Haz clic en cualquiera para ver perfil, X, Solscan y PnL
            </span>
          </div>

          <form onSubmit={handleAddSmartWallet} className="grid grid-cols-1 sm:grid-cols-12 gap-2">
            <input
              type="text"
              required
              minLength={20}
              value={swAddr}
              onChange={(e) => setSwAddr(e.target.value)}
              placeholder="Wallet smart money Solana..."
              className="sm:col-span-6 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-100 focus:border-amber-400 focus:outline-none"
            />
            <input
              type="text"
              value={swLabel}
              onChange={(e) => setSwLabel(e.target.value)}
              placeholder="Alias (ej. Ballena Alpha)"
              className="sm:col-span-4 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-amber-400 focus:outline-none"
            />
            <button
              type="submit"
              disabled={busy || swAddr.trim().length < 20}
              className="sm:col-span-2 rounded-lg bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-bold flex items-center justify-center gap-1 py-1.5 cursor-pointer disabled:opacity-40"
            >
              <Plus className="w-3.5 h-3.5" /> Add
            </button>
          </form>

          {/* Search & Category Filter for the 250 Smart Wallets */}
          <div className="space-y-2">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={swQuery}
                onChange={(e) => setSwQuery(e.target.value)}
                placeholder="Buscar trader, @twitter o dirección Solana..."
                className="w-full rounded-lg border border-white/10 bg-black/60 pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 focus:border-amber-400 focus:outline-none"
              />
            </div>
            <div className="flex flex-wrap gap-1">
              {[
                { key: "ALL", label: "TODAS" },
                { key: "KOL", label: "KOL" },
                { key: "LAUNCHPAD_SMART", label: "LAUNCHPAD" },
                { key: "SMART_DEGEN", label: "DEGEN" },
                { key: "TOP_FOLLOWED", label: "FOLLOWED" },
              ].map((cat) => (
                <button
                  key={cat.key}
                  type="button"
                  onClick={() => setSwCategory(cat.key)}
                  className={`px-2 py-0.5 rounded-md text-[10px] font-semibold border transition cursor-pointer ${
                    swCategory === cat.key
                      ? "bg-amber-500/20 border-amber-400/60 text-amber-200"
                      : "border-white/10 bg-white/[0.03] text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5 max-h-80 overflow-y-auto pr-0.5">
            {filteredWallets.length === 0 ? (
              <p className="text-xs text-slate-500 py-3 text-center">
                Sin Smart Wallets que coincidan con la búsqueda.
              </p>
            ) : (
              filteredWallets.map((w) => {
                const wr = w.winrate_30d ?? w.winrate_7d;
                const prof = w.realized_profit_30d ?? w.realized_profit_7d;
                return (
                  <div
                    key={w.address}
                    onClick={() => setSelectedWallet(w)}
                    className="rounded-xl border border-white/10 bg-white/[0.03] hover:bg-white/[0.07] hover:border-amber-400/45 px-3 py-2 flex items-center justify-between gap-2 cursor-pointer transition"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      {w.avatar ? (
                        <img
                          src={w.avatar}
                          alt={w.label}
                          className="w-7 h-7 rounded-full object-cover border border-amber-400/35 bg-black shrink-0"
                          onError={(e) => {
                            (e.currentTarget as HTMLImageElement).style.display = "none";
                          }}
                        />
                      ) : (
                        <div className="w-7 h-7 rounded-full border border-amber-400/30 bg-amber-500/15 text-amber-300 flex items-center justify-center text-[10px] font-bold shrink-0">
                          {(w.label || w.address).slice(0, 2).toUpperCase()}
                        </div>
                      )}
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="text-xs font-semibold text-white truncate">
                            {w.label || shortAddr(w.address, 6, 5)}
                          </span>
                          {w.twitter_username && (
                            <span className="text-[10px] text-sky-300 truncate">
                              @{w.twitter_username}
                            </span>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-2 text-[10px] text-zinc-400 tabular-nums">
                          <span className="text-amber-200/80">{shortAddr(w.address, 6, 4)}</span>
                          {wr != null && (
                            <span className="text-emerald-300 font-semibold">WR {wr}%</span>
                          )}
                          {prof != null && prof > 0 && (
                            <span className="text-cyan-300 font-semibold">
                              +{fmtUsd(prof, true)}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        void onRemoveSmartWallet(w.address);
                      }}
                      title="Quitar smart wallet"
                      className="p-1.5 rounded-md text-slate-400 hover:text-rose-300 hover:bg-rose-950/50 cursor-pointer shrink-0"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                );
              })
            )}
          </div>
        </section>
      </div>

      <SmartWalletModal
        wallet={selectedWallet}
        onClose={() => setSelectedWallet(null)}
      />
    </div>
  );
}
