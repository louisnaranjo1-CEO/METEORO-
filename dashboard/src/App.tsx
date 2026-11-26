import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, BookOpen, Radar, Sparkles, TrendingUp } from "lucide-react";
import { api, ApiError } from "./api";
import { HudBar } from "./components/HudBar";
import { LiveWalletModal } from "./components/LiveWalletModal";
import { PanicModal, TutorialSettingsModal } from "./components/Modals";
import { PinGate } from "./components/PinGate";
import { PositionsTab } from "./components/PositionsTab";
import { RadarTab } from "./components/RadarTab";
import { RejectedTab } from "./components/RejectedTab";
import { StrategyTab } from "./components/StrategyTab";
import { TokenDetailModal } from "./components/TokenDetailModal";
import { XRadarTab } from "./components/XRadarTab";
import type {
  AuthStatus,
  BlacklistItem,
  HudState,
  PositionsResponse,
  RadarResponse,
  RejectedItem,
  SmartWalletItem,
  TradingMode,
  TutorialConfig,
  XFeedResponse,
} from "./types";

type TabKey = "radar" | "positions" | "rejected" | "xfeed" | "strategy";

const POLL_MS = 250; // 0.25s real-time dashboard refresh with in-flight guard

export function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [tab, setTab] = useState<TabKey>("radar");
  const [positionsMode, setPositionsMode] = useState<TradingMode>("TUTORIAL");

  const [hud, setHud] = useState<HudState | null>(null);
  const [radar, setRadar] = useState<RadarResponse | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [rejected, setRejected] = useState<RejectedItem[]>([]);
  const [blacklist, setBlacklist] = useState<BlacklistItem[]>([]);
  const [smartWallets, setSmartWallets] = useState<SmartWalletItem[]>([]);
  const [xFeed, setXFeed] = useState<XFeedResponse | null>(null);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [walletOpen, setWalletOpen] = useState(false);
  const [panicOpen, setPanicOpen] = useState(false);
  const [selectedMint, setSelectedMint] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState(false);
  const [busyLeadId, setBusyLeadId] = useState<number | null>(null);
  const [busyTradeId, setBusyTradeId] = useState<number | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [rgbPulse, setRgbPulse] = useState(false);
  const toastTimer = useRef<number | null>(null);
  const pulseTimer = useRef<number | null>(null);
  const lastActivitySig = useRef<string>("");
  const pollInFlight = useRef(false);

  const triggerRgbPulse = useCallback(() => {
    setRgbPulse(true);
    if (pulseTimer.current) window.clearTimeout(pulseTimer.current);
    pulseTimer.current = window.setTimeout(() => setRgbPulse(false), 1100);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    const onMove = (e: PointerEvent) => {
      root.style.setProperty("--mx", `${e.clientX}px`);
      root.style.setProperty("--my", `${e.clientY}px`);
      root.style.setProperty("--mouse-active", "1");
    };
    const onLeave = () => {
      root.style.setProperty("--mouse-active", "0");
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerleave", onLeave, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  const notify = useCallback((kind: "ok" | "err", text: string) => {
    setToast({ kind, text });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const status = await api.authStatus();
      setAuth(status);
    } catch {
      setAuth({ pin_required: false, authenticated: true });
    }
  }, []);

  const refreshData = useCallback(async () => {
    if (pollInFlight.current) return;
    pollInFlight.current = true;
    try {
      const hudData = await api.hud();
      setHud(hudData);

      if (tab === "radar") {
        const nextRadar = await api.radar();
        setRadar(nextRadar);
        const sig = [
          nextRadar.lanes.SEARCH.map((x) => `${x.id}:${x.updated_at}`).join(","),
          nextRadar.lanes.RISK_WHALE.map((x) => `${x.id}:${x.updated_at}`).join(","),
          nextRadar.lanes.JUDGE.map((x) => `${x.id}:${x.updated_at}`).join(","),
          nextRadar.lanes.POSITION.map((x) => `${x.id}:${x.updated_at}`).join(","),
        ].join("|");
        if (lastActivitySig.current && sig !== lastActivitySig.current) {
          triggerRgbPulse();
        }
        lastActivitySig.current = sig;
      } else if (tab === "positions") {
        setPositions(await api.positions(positionsMode));
      } else if (tab === "rejected") {
        const [rej, bl, sw] = await Promise.all([
          api.rejected(),
          api.blacklist(),
          api.smartWallets(),
        ]);
        const rejSig = `rej:${rej.items[0]?.id ?? 0}:${rej.items[0]?.updated_at ?? ""}`;
        if (lastActivitySig.current && rejSig !== lastActivitySig.current && lastActivitySig.current.startsWith("rej:")) {
          triggerRgbPulse();
        }
        lastActivitySig.current = rejSig;
        setRejected(rej.items);
        setBlacklist(bl.items);
        setSmartWallets(sw.items);
      } else if (tab === "xfeed") {
        setXFeed(await api.xFeed());
      } else if (tab === "strategy") {
        const sw = await api.smartWallets();
        setSmartWallets(sw.items);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setAuth({ pin_required: true, authenticated: false });
      }
    } finally {
      pollInFlight.current = false;
    }
  }, [tab, positionsMode, triggerRgbPulse]);

  useEffect(() => {
    void checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    if (!auth?.authenticated) return;
    void refreshData();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void refreshData();
      }
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [auth?.authenticated, refreshData]);

  async function handleLogin(pin: string) {
    await api.login(pin);
    setAuth({ pin_required: true, authenticated: true });
  }

  async function handleTogglePause() {
    setBusyAction(true);
    try {
      const res = await api.togglePause();
      notify(
        "ok",
        res.is_paused
          ? "Escáner pausado."
          : res.breaker_reset
          ? "Escáner reanudado (freno diario reiniciado)."
          : "Escáner reanudado.",
      );
      await refreshData();
    } catch (err) {
      notify("err", err instanceof Error ? err.message : "Error al pausar.");
    } finally {
      setBusyAction(false);
    }
  }

  async function handlePanicConfirm() {
    setBusyAction(true);
    try {
      const res = await api.panicCloseAll();
      setPanicOpen(false);
      notify("ok", `Pánico ejecutado: ${res.closed.length} posición(es) cerrada(s) y bot pausado.`);
      await refreshData();
    } catch (err) {
      notify("err", err instanceof Error ? err.message : "Error ejecutando pánico.");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleApproveLead(leadId: number) {
    setBusyLeadId(leadId);
    try {
      await api.approveLead(leadId);
      notify("ok", "Compra aprobada y ejecutada.");
      await refreshData();
    } catch (err) {
      notify("err", err instanceof Error ? err.message : "No se pudo aprobar.");
    } finally {
      setBusyLeadId(null);
    }
  }

  async function handleRejectLead(leadId: number) {
    setBusyLeadId(leadId);
    try {
      await api.rejectLead(leadId);
      notify("ok", "Lead descartado manualmente.");
      await refreshData();
    } catch (err) {
      notify("err", err instanceof Error ? err.message : "No se pudo descartar.");
    } finally {
      setBusyLeadId(null);
    }
  }

  async function handleExitTrade(tradeId: number) {
    setBusyTradeId(tradeId);
    try {
      const res = await api.exitTrade(tradeId);
      notify("ok", `Posición cerrada (PnL: ${res.trade.pnl_sol.toFixed(4)} SOL).`);
      await refreshData();
    } catch (err) {
      notify("err", err instanceof Error ? err.message : "No se pudo cerrar la posición.");
    } finally {
      setBusyTradeId(null);
    }
  }

  async function handleSaveTutorial(patch: Partial<TutorialConfig>) {
    await api.updateTutorialConfig(patch);
    notify("ok", "Ajustes del Modo Tutorial actualizados.");
    await refreshData();
  }

  async function handleResetTutorial(initialSol: number) {
    await api.resetTutorial(initialSol);
    notify("ok", `Cuenta Tutorial reiniciada a ${initialSol} SOL.`);
    await refreshData();
  }

  async function handleSwitchMode(mode: TradingMode) {
    await api.switchMode(mode);
    setPositionsMode(mode);
    notify("ok", `Modo cambiado a ${mode}.`);
    await refreshData();
  }

  if (auth === null) {
    return (
      <div className="min-h-screen flex items-center justify-center font-mono text-xs text-slate-400">
        Conectando con Solana Judge Desk...
      </div>
    );
  }

  if (auth.pin_required && !auth.authenticated) {
    return <PinGate onLogin={handleLogin} />;
  }

  const tabs: Array<{ key: TabKey; label: string; icon: typeof Radar; badge?: number }> = [
    {
      key: "radar",
      label: "RADAR EN VIVO",
      icon: Radar,
      badge: hud?.pending_approvals || undefined,
    },
    {
      key: "positions",
      label: "POSICIONES Y GANANCIAS",
      icon: TrendingUp,
      badge: hud?.open_trades || undefined,
    },
    { key: "rejected", label: "DESCARTES Y BLACKLIST", icon: Ban },
    { key: "xfeed", label: "RADAR X Y CATALIZADORES", icon: Sparkles },
    { key: "strategy", label: "ESTRATEGIA Y SISTEMA", icon: BookOpen },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-transparent text-white relative">
      {/* Dynamic RGB Ambient Gradient Backdrop + Mouse Spotlight */}
      <div className={`rgb-ambient-layer ${rgbPulse ? "rgb-pulse-active" : ""}`} aria-hidden="true">
        <div className="rgb-orb rgb-orb-cyan" />
        <div className="rgb-orb rgb-orb-emerald" />
        <div className="rgb-orb rgb-orb-violet" />
        <div className="rgb-orb rgb-orb-amber" />
      </div>
      <div className="rgb-mouse-aura" aria-hidden="true" />

      <div className="relative z-10 flex flex-col min-h-screen">
      <HudBar
        hud={hud}
        busyAction={busyAction}
        onTogglePause={() => void handleTogglePause()}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenWallet={() => setWalletOpen(true)}
        onOpenPanic={() => setPanicOpen(true)}
      />

      {/* 5 Clean Primary Tabs */}
      <nav className="border-b border-white/10 bg-black/55 backdrop-blur-2xl">
        <div className="max-w-[1600px] mx-auto px-3 sm:px-5 flex items-center gap-1 overflow-x-auto">
          {tabs.map((t) => {
            const Icon = t.icon;
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`px-3.5 py-3 text-xs font-medium tracking-tight whitespace-nowrap border-b-2 flex items-center gap-2 transition cursor-pointer ${
                  active
                    ? "border-white text-white"
                    : "border-transparent text-zinc-400 hover:text-zinc-200"
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{t.label}</span>
                {t.badge !== undefined && t.badge > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full bg-amber-500/20 border border-amber-400/50 font-mono text-[10px] text-amber-300">
                    {t.badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </nav>

      {/* Active Tab Content */}
      <main className="max-w-[1600px] w-full mx-auto px-3 sm:px-5 py-5 flex-1">
        {tab === "radar" && (
          <RadarTab
            radar={radar}
            busyId={busyLeadId}
            onSelectToken={setSelectedMint}
            onApprove={handleApproveLead}
            onReject={handleRejectLead}
          />
        )}

        {tab === "positions" && (
          <PositionsTab
            data={positions}
            selectedMode={positionsMode}
            busyTradeId={busyTradeId}
            onSelectMode={setPositionsMode}
            onSelectToken={setSelectedMint}
            onExitTrade={handleExitTrade}
          />
        )}

        {tab === "rejected" && (
          <RejectedTab
            rejected={rejected}
            blacklist={blacklist}
            smartWallets={smartWallets}
            onSelectToken={setSelectedMint}
            onAddBlacklist={async (wallet, reason, mint) => {
              await api.addBlacklist(wallet, reason, mint);
              notify("ok", "Wallet añadida a blacklisted_devs.");
              await refreshData();
            }}
            onRemoveBlacklist={async (wallet) => {
              await api.removeBlacklist(wallet);
              notify("ok", "Wallet eliminada de la lista negra.");
              await refreshData();
            }}
            onAddSmartWallet={async (address, label) => {
              await api.addSmartWallet(address, label);
              notify("ok", "Smart Wallet añadida.");
              await refreshData();
            }}
            onRemoveSmartWallet={async (address) => {
              await api.removeSmartWallet(address);
              notify("ok", "Smart Wallet eliminada.");
              await refreshData();
            }}
          />
        )}

        {tab === "xfeed" && <XRadarTab feed={xFeed} />}

        {tab === "strategy" && (
          <StrategyTab
            hud={hud}
            smartWallets={smartWallets}
            onNotify={notify}
            onRefreshHud={refreshData}
          />
        )}
      </main>
      </div>

      {/* Modals */}
      <LiveWalletModal
        open={walletOpen}
        onClose={() => setWalletOpen(false)}
        onNotify={notify}
      />
      <TutorialSettingsModal
        open={settingsOpen}
        hud={hud}
        onClose={() => setSettingsOpen(false)}
        onSave={handleSaveTutorial}
        onReset={handleResetTutorial}
        onSwitchMode={handleSwitchMode}
      />

      <PanicModal
        open={panicOpen}
        openCount={hud?.open_trades ?? 0}
        mode={hud?.mode ?? "TUTORIAL"}
        busy={busyAction}
        onCancel={() => setPanicOpen(false)}
        onConfirm={handlePanicConfirm}
      />

      <TokenDetailModal
        mint={selectedMint}
        onClose={() => setSelectedMint(null)}
        onApprove={handleApproveLead}
        onReject={handleRejectLead}
        onExitTrade={handleExitTrade}
      />

      {/* Compact Toast */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 z-50 px-4 py-2.5 rounded-xl border text-xs font-medium shadow-xl backdrop-blur ${
            toast.kind === "ok"
              ? "bg-emerald-950/90 border-emerald-500/50 text-emerald-200"
              : "bg-rose-950/90 border-rose-500/50 text-rose-200"
          }`}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

export default App;
