import { useCallback, useEffect, useState } from "react";
import {
  Brain,
  CopyPlus,
  Cpu,
  Flame,
  Radar,
  RotateCcw,
  Save,
  Scale,
  ShieldCheck,
  Trash2,
  Zap,
} from "lucide-react";
import { api } from "../api";
import type { HudState, SmartWalletItem, StrategyPreset } from "../types";

interface StrategyTabProps {
  hud: HudState | null;
  smartWallets: SmartWalletItem[];
  onNotify: (kind: "ok" | "err", text: string) => void;
  onRefreshHud: () => Promise<void>;
}

type ParamMap = Record<string, number | boolean | string>;

export function StrategyTab({ hud, smartWallets, onNotify, onRefreshHud }: StrategyTabProps) {
  const [strategies, setStrategies] = useState<StrategyPreset[]>([]);
  const [activeId, setActiveId] = useState<string>("PREDETERMINADA");
  const [draft, setDraft] = useState<ParamMap | null>(null);
  const [saving, setSaving] = useState(false);

  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const loadStrategies = useCallback(async () => {
    try {
      const res = await api.strategies();
      setStrategies(res.strategies);
      setActiveId(res.active_id);
      setDraft({ ...res.current_params });
    } catch {
      // ignore transient error
    }
  }, []);

  useEffect(() => {
    void loadStrategies();
  }, [loadStrategies]);

  const activePreset = strategies.find((s) => s.id === activeId) ?? strategies[0] ?? null;

  function num(key: string, fallback = 0): number {
    const v = draft?.[key];
    return typeof v === "number" && Number.isFinite(v) ? v : fallback;
  }

  function boolVal(key: string, fallback = false): boolean {
    const v = draft?.[key];
    return typeof v === "boolean" ? v : fallback;
  }

  function strVal(key: string, fallback = "AGRESIVO"): string {
    const v = draft?.[key];
    return typeof v === "string" ? v : fallback;
  }

  function setField(key: string, val: number | boolean | string) {
    setDraft((prev) => ({ ...(prev ?? {}), [key]: val }));
  }

  async function handleSelectPreset(id: string) {
    setSaving(true);
    try {
      const res = await api.applyStrategy(id);
      setActiveId(res.active_id);
      setDraft({ ...res.current_params });
      onNotify("ok", `Estrategia aplicada en vivo: ${res.strategy.name}`);
      await onRefreshHud();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "No se pudo aplicar la estrategia.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveCurrent() {
    if (!draft || !activePreset) return;
    setSaving(true);
    try {
      const res = await api.saveStrategy({
        id: activePreset.id,
        name: activePreset.name,
        description: activePreset.description,
        params: draft,
      });
      setStrategies(res.strategies);
      setActiveId(res.active_id);
      setDraft({ ...res.current_params });
      onNotify("ok", `Estrategia "${res.strategy.name}" guardada y aplicada en vivo.`);
      await onRefreshHud();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "Error guardando estrategia.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveAsNew() {
    if (!draft || !newName.trim()) return;
    setSaving(true);
    try {
      const res = await api.saveStrategy({
        name: newName.trim(),
        description: newDesc.trim() || "Estrategia personalizada",
        params: draft,
      });
      setStrategies(res.strategies);
      setActiveId(res.active_id);
      setDraft({ ...res.current_params });
      setNewName("");
      setNewDesc("");
      setSaveAsOpen(false);
      onNotify("ok", `Nueva estrategia "${res.strategy.name}" creada y activada.`);
      await onRefreshHud();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "Error creando estrategia.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeletePreset() {
    if (!activePreset || activePreset.builtin) return;
    setSaving(true);
    try {
      const res = await api.deleteStrategy(activePreset.id);
      setStrategies(res.strategies);
      setActiveId(res.active_id);
      await handleSelectPreset(res.active_id);
      onNotify("ok", `Estrategia "${activePreset.name}" eliminada.`);
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "No se pudo eliminar.");
    } finally {
      setSaving(false);
    }
  }

  if (!draft) {
    return (
      <div className="rounded-3xl glass-panel p-10 text-center text-xs text-zinc-400">
        Cargando editor de estrategias...
      </div>
    );
  }

  const tradeSize = num("trade_size_sol", hud?.trade_size_sol ?? 0.25);
  const noSl = boolVal("disable_hard_stop_loss", true);
  const tp1Pct = num("tp1_trigger_pct", 18);
  const wickPct = num("wick_harvest_arm_pct", 14);
  const scanSec = num("search_interval_seconds", 6);
  const exitSec = num("exit_poll_interval_seconds", 3);

  return (
    <div className="space-y-5">
      {/* Top Strategy Preset Manager Bar */}
      <section className="rounded-3xl glass-panel rgb-hover-card p-5 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/[0.06] border border-white/10 text-[11px] font-medium text-cyan-300">
              <Cpu className="w-3.5 h-3.5" /> Editor Vivo de Estrategias (6 Fases Modificables)
            </div>
            <h2 className="text-lg sm:text-2xl font-bold tracking-tight text-white">
              Personaliza, guarda y cambia tus estrategias en 1 clic
            </h2>
            <p className="text-xs text-zinc-400 max-w-3xl">
              Todos los parámetros de las 6 fases son editables en vivo. Puedes guardar múltiples perfiles personalizados o restaurar en cualquier momento tu{" "}
              <strong className="text-cyan-300">Estrategia Predeterminada</strong>.
            </p>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
            <QuickPill label="Tamaño" value={`${tradeSize.toFixed(2)} SOL`} tone="cyan" />
            <QuickPill label="Stop Loss" value={noSl ? "SIN SL (Solo +)" : `-${num("initial_stop_loss_pct", 22)}%`} tone="rose" />
            <QuickPill label="TP1 / Mecha" value={`+${tp1Pct}% / +${wickPct}%`} tone="emerald" />
            <QuickPill label="Scan / Exit" value={`${scanSec}s / ${exitSec}s`} tone="amber" />
          </div>
        </div>

        {/* Preset Selector + Action Buttons */}
        <div className="flex flex-wrap items-center gap-2.5 pt-2 border-t border-white/10">
          <div className="flex items-center gap-2 flex-1 min-w-[240px]">
            <label className="text-[11px] text-zinc-400 font-semibold whitespace-nowrap">
              Estrategia Activa:
            </label>
            <select
              value={activeId}
              disabled={saving}
              onChange={(e) => void handleSelectPreset(e.target.value)}
              className="flex-1 rounded-xl border border-white/15 bg-black/70 px-3 py-2 text-xs font-semibold text-white focus:border-cyan-400 focus:outline-none"
            >
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.builtin ? `★ ${s.name}` : s.name}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            disabled={saving}
            onClick={() => void handleSaveCurrent()}
            className="px-3.5 py-2 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-black text-xs font-bold inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-40"
          >
            <Save className="w-3.5 h-3.5" /> {saving ? "Guardando..." : "Guardar y Aplicar"}
          </button>

          <button
            type="button"
            disabled={saving}
            onClick={() => setSaveAsOpen((v) => !v)}
            className="px-3.5 py-2 rounded-xl border border-emerald-400/50 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-200 text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer"
          >
            <CopyPlus className="w-3.5 h-3.5" /> Guardar como Nueva
          </button>

          <button
            type="button"
            disabled={saving}
            onClick={() => void handleSelectPreset("PREDETERMINADA")}
            className="px-3 py-2 rounded-xl border border-white/15 bg-white/[0.05] hover:bg-white/10 text-zinc-200 text-xs font-medium inline-flex items-center gap-1.5 cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" /> Predeterminada
          </button>

          {activePreset && !activePreset.builtin && (
            <button
              type="button"
              disabled={saving}
              onClick={() => void handleDeletePreset()}
              className="px-3 py-2 rounded-xl border border-rose-500/40 bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 text-xs font-semibold inline-flex items-center gap-1 cursor-pointer"
            >
              <Trash2 className="w-3.5 h-3.5" /> Eliminar
            </button>
          )}
        </div>

        {/* Save As New Inline Form */}
        {saveAsOpen && (
          <div className="rounded-2xl border border-emerald-400/35 bg-emerald-950/20 p-3.5 grid grid-cols-1 sm:grid-cols-12 gap-2.5">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Nombre de tu nueva estrategia (ej. Scalper Nocturno 15%)"
              className="sm:col-span-5 rounded-xl border border-white/15 bg-black/60 px-3 py-2 text-xs text-white focus:border-emerald-400 focus:outline-none"
            />
            <input
              type="text"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Descripción corta (opcional)"
              className="sm:col-span-5 rounded-xl border border-white/15 bg-black/60 px-3 py-2 text-xs text-white focus:border-emerald-400 focus:outline-none"
            />
            <button
              type="button"
              disabled={saving || !newName.trim()}
              onClick={() => void handleSaveAsNew()}
              className="sm:col-span-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold py-2 cursor-pointer disabled:opacity-40"
            >
              Crear Perfil
            </button>
          </div>
        )}
      </section>

      {/* 6 Editable Phase Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* PHASE 01: SEARCH */}
        <PhaseCard
          num="01"
          title="SEARCH · Escáner de Mercado"
          subtitle="GeckoTerminal + DexScreener Solana en rotación continua"
          icon={Radar}
          tone="cyan"
        >
          <div className="grid grid-cols-2 gap-2.5">
            <NumInput label="Escaneo cada (seg)" value={num("search_interval_seconds", 6)} min={3} max={120} step={1} onChange={(v) => setField("search_interval_seconds", Math.round(v))} />
            <NumInput label="Liquidez Mínima ($)" value={num("min_liquidity_usd", 12000)} min={1000} step={1000} onChange={(v) => setField("min_liquidity_usd", v)} />
            <NumInput label="Market Cap Mín ($)" value={num("min_market_cap_usd", 50000)} min={5000} step={5000} onChange={(v) => setField("min_market_cap_usd", v)} />
            <NumInput label="Market Cap Máx ($)" value={num("max_market_cap_usd", 1500000)} min={50000} step={50000} onChange={(v) => setField("max_market_cap_usd", v)} />
          </div>
        </PhaseCard>

        {/* PHASE 02: RISK & WHALE */}
        <PhaseCard
          num="02"
          title="RISK & WHALE · Filtro Duro + Smart Money"
          subtitle={`RugCheck On-Chain + ${smartWallets.length} Smart Wallets + Helius`}
          icon={ShieldCheck}
          tone="amber"
        >
          <div className="grid grid-cols-2 gap-2.5">
            <NumInput label="Holders Mínimos" value={num("min_holders", 60)} min={10} step={10} onChange={(v) => setField("min_holders", Math.round(v))} />
            <NumInput label="Top 10 Máx (%)" value={num("max_top10_holders_pct", 25)} min={5} max={80} step={1} onChange={(v) => setField("max_top10_holders_pct", v)} />
            <NumInput label="Dev Wallet Máx (%)" value={num("max_dev_wallet_pct", 4)} min={0} max={25} step={0.5} onChange={(v) => setField("max_dev_wallet_pct", v)} />
            <NumInput
              label="Pool Principal Mín (%)"
              value={Math.round(num("main_pool_liquidity_share_min", 0.8) * 100)}
              min={20}
              max={100}
              step={5}
              onChange={(v) => setField("main_pool_liquidity_share_min", Math.max(0.2, Math.min(1, v / 100)))}
            />
          </div>
          <div className="grid grid-cols-3 gap-2 pt-1">
            <ToggleChip label="Mint Revocada" checked={boolVal("require_mint_revoked", true)} onChange={(b) => setField("require_mint_revoked", b)} />
            <ToggleChip label="Freeze Revocada" checked={boolVal("require_freeze_revoked", true)} onChange={(b) => setField("require_freeze_revoked", b)} />
            <ToggleChip label="LP Quemada/Lock" checked={boolVal("require_lp_locked_or_burned", true)} onChange={(b) => setField("require_lp_locked_or_burned", b)} />
          </div>
        </PhaseCard>

        {/* PHASE 03: JUEZ MUDO / SNIPER ALGORÍTMICO */}
        <PhaseCard
          num="03"
          title="DECISIÓN · Juez IA o Sniper Algorítmico 0ms"
          subtitle={
            boolVal("use_llm_judge", true)
              ? "DeepSeek 4.1 Flash (LLM) · Temperatura 0.0"
              : "MOTOR 100% ALGORÍTMICO (LLM Apagado · 0ms latencia · $0 API)"
          }
          icon={Brain}
          tone="violet"
        >
          <div className="grid grid-cols-2 gap-2 pb-1">
            <ToggleChip
              label={boolVal("use_llm_judge", true) ? "LLM Activo (DeepSeek)" : "LLM Apagado (Algo 0ms)"}
              checked={!boolVal("use_llm_judge", true)}
              onChange={(algoOn) => setField("use_llm_judge", !algoOn)}
            />
            <NumInput label="Reevaluar WATCH (min)" value={num("watch_recheck_minutes", 4)} min={1} max={30} step={1} onChange={(v) => setField("watch_recheck_minutes", Math.round(v))} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <NumInput label="Anti-Punta 5m Máx (%)" value={num("sniper_max_entry_pc5m_pct", 18)} min={5} max={200} step={1} onChange={(v) => setField("sniper_max_entry_pc5m_pct", v)} />
            <NumInput label="Dip 5m Mín (%)" value={num("sniper_min_entry_pc5m_pct", -7)} min={-50} max={20} step={1} onChange={(v) => setField("sniper_min_entry_pc5m_pct", v)} />
            <NumInput label="Anti-Punta 1h Máx (%)" value={num("sniper_max_entry_pc1h_pct", 135)} min={20} max={1000} step={5} onChange={(v) => setField("sniper_max_entry_pc1h_pct", v)} />
            <NumInput label="Edad Máx (min)" value={num("sniper_max_age_minutes", 180)} min={10} max={10000} step={10} onChange={(v) => setField("sniper_max_age_minutes", v)} />
          </div>
          <div className="pt-1">
            <label className="block text-[10px] text-zinc-400 uppercase mb-1">Modo del Prompt (cuando LLM está encendido)</label>
            <select
              value={strVal("strategy_profile", "AGRESIVO")}
              onChange={(e) => setField("strategy_profile", e.target.value)}
              className="w-full rounded-xl border border-white/15 bg-black/65 px-2.5 py-1.5 text-xs font-semibold text-white focus:border-violet-400 focus:outline-none"
            >
              <option value="AGRESIVO">AGRESIVO (Wave Scalper)</option>
              <option value="CONSERVADOR">CONSERVADOR (Entradas estrictas)</option>
            </select>
          </div>
        </PhaseCard>

        {/* PHASE 04: GATE MATEMÁTICO */}
        <PhaseCard
          num="04"
          title="GATE MATEMÁTICO · Reglas Deterministas"
          subtitle="Umbrales exactos de probabilidad y frenos de capital"
          icon={Scale}
          tone="emerald"
        >
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            <PctInput label="Action Mín Conf (%)" value={num("gate_min_action_prob", 0.55)} onChange={(v) => setField("gate_min_action_prob", v)} />
            <PctInput label="Action Mín Early (%)" value={num("gate_min_action_prob_low_mcap", 0.58)} onChange={(v) => setField("gate_min_action_prob_low_mcap", v)} />
            <PctInput label="Organic Mín (%)" value={num("gate_min_organic_prob", 0.5)} onChange={(v) => setField("gate_min_organic_prob", v)} />
            <PctInput label="Late Máx (%)" value={num("gate_max_late_prob", 0.72)} onChange={(v) => setField("gate_max_late_prob", v)} />
            <NumInput label="Exit Risk Máx (1-5)" value={num("gate_max_exit_risk", 3)} min={1} max={5} step={1} onChange={(v) => setField("gate_max_exit_risk", Math.round(v))} />
            <NumInput label="Máx Trades Activos" value={num("max_concurrent_trades", 5)} min={1} max={20} step={1} onChange={(v) => setField("max_concurrent_trades", Math.round(v))} />
            <NumInput label="Pérdida Día Máx (SOL)" value={num("max_daily_loss_sol", 2.0)} min={0.1} step={0.25} onChange={(v) => setField("max_daily_loss_sol", v)} />
            <NumInput label="Pérdida Día Máx (%)" value={num("max_daily_loss_pct", 40)} min={5} max={100} step={5} onChange={(v) => setField("max_daily_loss_pct", v)} />
            <NumInput label="Máx % Entrada/Liq" value={num("max_entry_liquidity_pct", 1.0)} min={0.1} max={10} step={0.1} onChange={(v) => setField("max_entry_liquidity_pct", v)} />
          </div>
        </PhaseCard>

        {/* PHASE 05: EJECUCIÓN */}
        <PhaseCard
          num="05"
          title="EJECUCIÓN · Tamaño Fijo y Control"
          subtitle="Tutorial Realista o Live Doble Candado (Jupiter v6 + Jito)"
          icon={Zap}
          tone="sky"
        >
          <div className="grid grid-cols-2 gap-2.5">
            <NumInput label="Tamaño por Trade (SOL)" value={num("trade_size_sol", 0.25)} min={0.01} step={0.05} onChange={(v) => setField("trade_size_sol", v)} />
            <div className="flex items-end">
              <ToggleChip
                label="Semi-Auto (Confirmar 90s)"
                checked={boolVal("semi_auto_mode", false)}
                onChange={(b) => setField("semi_auto_mode", b)}
              />
            </div>
          </div>
          <p className="text-[11px] text-zinc-400 leading-relaxed pt-1">
            Con <strong>Semi-Auto</strong> activo, cada compra aprobada espera 90s tu clic en el Dashboard o Telegram. Desactivado = ejecución directa al instante.
          </p>
        </PhaseCard>

        {/* PHASE 06: EXIT MANAGER */}
        <PhaseCard
          num="06"
          title="EXIT MANAGER · Motor Wave & Wick"
          subtitle="Sin SL negativo · Smart Breakeven · Cazador de Mechas"
          icon={Flame}
          tone="rose"
        >
          <div className="grid grid-cols-2 gap-2 pb-1">
            <ToggleChip label="Sin Stop Loss Negativo" checked={boolVal("disable_hard_stop_loss", true)} onChange={(b) => setField("disable_hard_stop_loss", b)} />
            <ToggleChip label="Solo Cierre Positivo" checked={boolVal("only_close_positive", true)} onChange={(b) => setField("only_close_positive", b)} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            <NumInput label="Vigilar cada (seg)" value={num("exit_poll_interval_seconds", 0.5)} min={0.25} max={60} step={0.25} onChange={(v) => setField("exit_poll_interval_seconds", Math.max(0.25, v))} />
            <NumInput
              label={`Meta TP (${(1 + num("tp1_trigger_pct", 50) / 100).toFixed(2)}x)`}
              value={Number((1 + num("tp1_trigger_pct", 50) / 100).toFixed(2))}
              min={1.05}
              max={20}
              step={0.05}
              onChange={(mult) => setField("tp1_trigger_pct", Math.max(5, Math.round((mult - 1) * 1000) / 10))}
            />
            <NumInput label="TP Vender (%)" value={num("tp1_sell_pct", 100)} min={10} max={100} step={5} onChange={(v) => setField("tp1_sell_pct", v)} />
            <NumInput label="Smart BE Arma (%)" value={num("smart_be_arm_pct", 10)} min={2} step={1} onChange={(v) => setField("smart_be_arm_pct", v)} />
            <NumInput label="Piso BE Neto (%)" value={num("smart_be_floor_pct", 4)} min={1} step={0.5} onChange={(v) => setField("smart_be_floor_pct", v)} />
            <NumInput label="Mecha Arma (%)" value={num("wick_harvest_arm_pct", 25)} min={5} step={1} onChange={(v) => setField("wick_harvest_arm_pct", v)} />
            <NumInput label="Retroceso Mecha (%)" value={num("wick_pullback_exit_pct", 6)} min={1} step={0.5} onChange={(v) => setField("wick_pullback_exit_pct", v)} />
            <NumInput label="Runner Libre (%)" value={num("free_risk_trigger_pct", 100)} min={15} step={5} onChange={(v) => setField("free_risk_trigger_pct", v)} />
            <NumInput label="Trailing Runner (%)" value={num("trailing_stop_pct", 9)} min={3} max={50} step={1} onChange={(v) => setField("trailing_stop_pct", v)} />
            <NumInput label="Stop Loss Duro (%)" value={num("initial_stop_loss_pct", 22)} min={5} max={90} step={1} onChange={(v) => setField("initial_stop_loss_pct", v)} />
            <NumInput label="Espera +10% (min)" value={num("stagnation_window_minutes", 12)} min={2} max={120} step={1} onChange={(v) => setField("stagnation_window_minutes", Math.round(v))} />
          </div>
        </PhaseCard>
      </div>
    </div>
  );
}

function PhaseCard({
  num,
  title,
  subtitle,
  icon: Icon,
  tone,
  children,
}: {
  num: string;
  title: string;
  subtitle: string;
  icon: typeof Radar;
  tone: "cyan" | "amber" | "violet" | "emerald" | "sky" | "rose";
  children: React.ReactNode;
}) {
  const styles = {
    cyan: { t: "text-cyan-300", b: "border-cyan-400/35", bg: "bg-cyan-500/10" },
    amber: { t: "text-amber-300", b: "border-amber-400/35", bg: "bg-amber-500/10" },
    violet: { t: "text-violet-300", b: "border-violet-400/35", bg: "bg-violet-500/10" },
    emerald: { t: "text-emerald-300", b: "border-emerald-400/35", bg: "bg-emerald-500/10" },
    sky: { t: "text-sky-300", b: "border-sky-400/35", bg: "bg-sky-500/10" },
    rose: { t: "text-rose-300", b: "border-rose-400/35", bg: "bg-rose-500/10" },
  }[tone];
  return (
    <section className="rounded-3xl glass-panel rgb-hover-card p-5 space-y-3.5">
      <div className="flex items-center gap-3">
        <div className={`w-10 h-10 rounded-2xl border ${styles.b} ${styles.bg} flex items-center justify-center shrink-0`}>
          <Icon className={`w-5 h-5 ${styles.t}`} />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className={`text-[11px] font-bold ${styles.t}`}>FASE {num}</span>
            <span className="text-sm font-bold text-white">{title}</span>
          </div>
          <p className="text-[11px] text-zinc-400">{subtitle}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function NumInput({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="block text-[10px] text-zinc-400 uppercase mb-1 truncate">{label}</span>
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const parsed = parseFloat(e.target.value);
          if (Number.isFinite(parsed)) onChange(parsed);
        }}
        className="w-full rounded-xl border border-white/15 bg-black/60 px-2.5 py-1.5 text-xs font-semibold text-white tabular-nums focus:border-cyan-400 focus:outline-none"
      />
    </label>
  );
}

function PctInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (prob: number) => void;
}) {
  return (
    <NumInput
      label={label}
      value={Math.round(value * 100)}
      min={0}
      max={100}
      step={1}
      onChange={(pct) => onChange(Math.max(0, Math.min(1, pct / 100)))}
    />
  );
}

function ToggleChip({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (b: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`w-full px-2.5 py-2 rounded-xl border text-[11px] font-semibold flex items-center justify-between gap-1.5 cursor-pointer transition ${
        checked
          ? "border-emerald-400/50 bg-emerald-500/15 text-emerald-200"
          : "border-white/10 bg-black/50 text-zinc-400 hover:text-zinc-200"
      }`}
    >
      <span className="truncate">{label}</span>
      <span className={`w-2 h-2 rounded-full shrink-0 ${checked ? "bg-emerald-400" : "bg-zinc-600"}`} />
    </button>
  );
}

function QuickPill({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "cyan" | "rose" | "emerald" | "amber";
}) {
  const styles = {
    cyan: "border-cyan-400/30 bg-cyan-500/10 text-cyan-200",
    rose: "border-rose-400/30 bg-rose-500/10 text-rose-200",
    emerald: "border-emerald-400/30 bg-emerald-500/10 text-emerald-200",
    amber: "border-amber-400/30 bg-amber-500/10 text-amber-200",
  }[tone];
  return (
    <div className={`rounded-2xl border px-3 py-1.5 ${styles}`}>
      <div className="text-[10px] uppercase tracking-wider opacity-80">{label}</div>
      <div className="text-xs font-bold tabular-nums mt-0.5">{value}</div>
    </div>
  );
}
