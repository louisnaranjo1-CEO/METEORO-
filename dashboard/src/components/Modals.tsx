import { useEffect, useRef, useState, type FormEvent } from "react";
import { AlertTriangle, HelpCircle, RotateCcw, ShieldAlert, X } from "lucide-react";
import type { HudState, TradingMode, TutorialConfig } from "../types";

interface TutorialSettingsModalProps {
  open: boolean;
  hud: HudState | null;
  onClose: () => void;
  onSave: (patch: Partial<TutorialConfig>) => Promise<void>;
  onReset: (initialSol: number) => Promise<void>;
  onSwitchMode: (mode: TradingMode) => Promise<void>;
}

interface FieldHelp {
  title: string;
  desc: string;
  impact: string;
}

const PARAM_HELP: Record<string, FieldHelp> = {
  tutorial_initial_bankroll_sol: {
    title: "Banca Inicial Tutorial (SOL)",
    desc: "Capital virtual total con el que practica el bot.",
    impact: "Al reiniciar Tutorial, tu saldo disponible vuelve a este monto. También define el tope máximo del 10% de pérdida diaria.",
  },
  trade_size_sol: {
    title: "Tamaño Fijo por Trade (SOL)",
    desc: "Cantidad exacta de SOL que el bot invierte en cada compra aprobada.",
    impact: "Nunca cambia según la confianza del Juez. Si lo subes, ganas/pierdes más por trade; no debe superar el 1% de la liquidez del pool.",
  },
  max_concurrent_trades: {
    title: "Máx. Trades Activos Simultáneos",
    desc: "Cantidad máxima de operaciones abiertas al mismo tiempo (de 1 a 20).",
    impact: "Subirlo te permite capturar más olas a la vez sin quedar bloqueado si una moneda tarda en subir; asegúrate de tener banca suficiente para cubrirlos.",
  },
  sim_dex_fee_pct: {
    title: "Fee DEX Simulado (%)",
    desc: "Comisión que cobra el DEX (Raydium/PumpSwap/Meteora) por cada swap.",
    impact: "Se descuenta al comprar y al vender (ej. 0.25% por operación). Subirlo hace la simulación más conservadora.",
  },
  sim_priority_fee_sol: {
    title: "Priority Fee Solana (SOL)",
    desc: "Pago extra a los validadores de Solana para que tu transacción entre rápido.",
    impact: "Costo fijo por cada compra o venta (0.0005 SOL ≈ unos centavos). Afecta la rentabilidad en trades muy pequeños.",
  },
  sim_jito_tip_sol: {
    title: "Propina Jito (SOL)",
    desc: "Propina anti-MEV para que los bloques de Jito prioricen tu orden y eviten sándwiches.",
    impact: "Costo fijo por transacción (0.002 SOL). Se suma al priority fee en cada entrada y salida.",
  },
  sim_buy_slippage_pct: {
    title: "Slippage Compra (%)",
    desc: "Deslizamiento de precio en contra al entrar a un memecoin volátil.",
    impact: "Simula entrar a un precio peor que el spot (ej. 1.5% más caro), reduciendo los tokens recibidos.",
  },
  sim_tp_slippage_pct: {
    title: "Slippage Take Profit (%)",
    desc: "Deslizamiento al vender en ganancias (TP1, 2x o Trailing Stop).",
    impact: "Reduce ligeramente los SOL netos recibidos al tomar ganancias ordenadas.",
  },
  sim_sl_slippage_pct: {
    title: "Slippage SL / Pánico (%)",
    desc: "Deslizamiento severo al vender en Stop Loss, caída de liquidez o Pánico.",
    impact: "Refleja velas rojas rápidas y libros delgados (8% por defecto) para no engañarte con ganancias irreales.",
  },
};

export function TutorialSettingsModal({
  open,
  hud,
  onClose,
  onSave,
  onReset,
  onSwitchMode,
}: TutorialSettingsModalProps) {
  const [form, setForm] = useState<TutorialConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [modeError, setModeError] = useState<string | null>(null);
  const [showAllHelp, setShowAllHelp] = useState(false);
  const [activeHelpKey, setActiveHelpKey] = useState<string | null>(null);
  const wasOpenRef = useRef(false);

  // Only hydrate form when modal transitions from closed -> open (never overwrite user typing on 0.25s HUD polls)
  useEffect(() => {
    if (open && !wasOpenRef.current && hud) {
      setForm({ ...hud.tutorial_config });
      setConfirmReset(false);
      setModeError(null);
      setSaveError(null);
      setActiveHelpKey(null);
    }
    wasOpenRef.current = open;
  }, [open, hud]);

  if (!open || !form || !hud) return null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    setSaveError(null);
    setSaving(true);
    try {
      await onSave(form);
      onClose();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "No se pudieron guardar los ajustes.");
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    if (!form) return;
    if (!confirmReset) {
      setConfirmReset(true);
      return;
    }
    setSaving(true);
    try {
      await onReset(form.tutorial_initial_bankroll_sol);
      setConfirmReset(false);
      onClose();
    } finally {
      setSaving(false);
    }
  }

  async function handleModeSwitch(target: TradingMode) {
    setModeError(null);
    setSaving(true);
    try {
      await onSwitchMode(target);
      onClose();
    } catch (err) {
      setModeError(err instanceof Error ? err.message : "No se pudo cambiar el modo.");
    } finally {
      setSaving(false);
    }
  }

  function toggleHelp(key: string) {
    setActiveHelpKey((prev) => (prev === key ? null : key));
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4">
      <div className="w-full max-w-2xl rounded-3xl glass-panel overflow-hidden">
        <div className="px-5 py-4 border-b border-white/10 flex items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-100">
              Ajustes Tutorial y Modo de Ejecución
            </h2>
            <p className="text-xs text-slate-400">
              Se guarda en SQLite y se recuerda al reiniciar. Presiona{" "}
              <span className="text-cyan-300 font-semibold">?</span> para ver el impacto de cada parámetro.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowAllHelp((v) => !v)}
              className={`px-2.5 py-1.5 rounded-lg border text-[11px] font-semibold flex items-center gap-1 transition cursor-pointer ${
                showAllHelp
                  ? "bg-cyan-500/20 border-cyan-400 text-cyan-200"
                  : "border-slate-700 bg-slate-950 text-slate-300 hover:border-cyan-500/50"
              }`}
            >
              <HelpCircle className="w-3.5 h-3.5 text-cyan-400" />
              {showAllHelp ? "Ocultar Guía" : "Guía de Parámetros"}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800 cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-5 max-h-[80vh] overflow-y-auto">
          {showAllHelp && (
            <div className="rounded-xl border border-cyan-500/35 bg-cyan-950/20 p-3.5 space-y-2 text-xs">
              <div className="font-bold uppercase tracking-wider text-cyan-300 text-[11px]">
                Qué significa e impacta cada parámetro
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                {Object.entries(PARAM_HELP).map(([k, h]) => (
                  <div key={k} className="rounded-lg border border-slate-800 bg-slate-950/75 p-2.5">
                    <div className="font-semibold text-slate-100">{h.title}</div>
                    <p className="text-[11px] text-slate-400 mt-0.5">{h.desc}</p>
                    <p className="text-[11px] text-cyan-300/90 mt-1">{h.impact}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
            <NumericField
              fieldKey="tutorial_initial_bankroll_sol"
              label="Banca Inicial Tutorial (SOL)"
              step="0.1"
              value={form.tutorial_initial_bankroll_sol}
              help={PARAM_HELP.tutorial_initial_bankroll_sol}
              showHelp={activeHelpKey === "tutorial_initial_bankroll_sol"}
              onToggleHelp={() => toggleHelp("tutorial_initial_bankroll_sol")}
              onChange={(v) => setForm({ ...form, tutorial_initial_bankroll_sol: v })}
            />
            <NumericField
              fieldKey="trade_size_sol"
              label="Tamaño Fijo por Trade (SOL)"
              step="0.05"
              value={form.trade_size_sol}
              help={PARAM_HELP.trade_size_sol}
              showHelp={activeHelpKey === "trade_size_sol"}
              onToggleHelp={() => toggleHelp("trade_size_sol")}
              onChange={(v) => setForm({ ...form, trade_size_sol: v })}
            />
            <NumericField
              fieldKey="max_concurrent_trades"
              label="Máx. Trades Activos (1–20)"
              step="1"
              value={form.max_concurrent_trades ?? hud.max_concurrent_trades ?? 3}
              help={PARAM_HELP.max_concurrent_trades}
              showHelp={activeHelpKey === "max_concurrent_trades"}
              onToggleHelp={() => toggleHelp("max_concurrent_trades")}
              onChange={(v) =>
                setForm({
                  ...form,
                  max_concurrent_trades: Math.max(1, Math.min(20, Math.round(v))),
                })
              }
            />
            <NumericField
              fieldKey="sim_dex_fee_pct"
              label="Fee DEX Simulado (%)"
              step="0.05"
              value={form.sim_dex_fee_pct}
              help={PARAM_HELP.sim_dex_fee_pct}
              showHelp={activeHelpKey === "sim_dex_fee_pct"}
              onToggleHelp={() => toggleHelp("sim_dex_fee_pct")}
              onChange={(v) => setForm({ ...form, sim_dex_fee_pct: v })}
            />
            <NumericField
              fieldKey="sim_priority_fee_sol"
              label="Priority Fee Solana (SOL)"
              step="0.0001"
              value={form.sim_priority_fee_sol}
              help={PARAM_HELP.sim_priority_fee_sol}
              showHelp={activeHelpKey === "sim_priority_fee_sol"}
              onToggleHelp={() => toggleHelp("sim_priority_fee_sol")}
              onChange={(v) => setForm({ ...form, sim_priority_fee_sol: v })}
            />
            <NumericField
              fieldKey="sim_jito_tip_sol"
              label="Propina Jito (SOL)"
              step="0.0005"
              value={form.sim_jito_tip_sol}
              help={PARAM_HELP.sim_jito_tip_sol}
              showHelp={activeHelpKey === "sim_jito_tip_sol"}
              onToggleHelp={() => toggleHelp("sim_jito_tip_sol")}
              onChange={(v) => setForm({ ...form, sim_jito_tip_sol: v })}
            />
            <NumericField
              fieldKey="sim_buy_slippage_pct"
              label="Slippage Compra (%)"
              step="0.1"
              value={form.sim_buy_slippage_pct}
              help={PARAM_HELP.sim_buy_slippage_pct}
              showHelp={activeHelpKey === "sim_buy_slippage_pct"}
              onToggleHelp={() => toggleHelp("sim_buy_slippage_pct")}
              onChange={(v) => setForm({ ...form, sim_buy_slippage_pct: v })}
            />
            <NumericField
              fieldKey="sim_tp_slippage_pct"
              label="Slippage Take Profit (%)"
              step="0.1"
              value={form.sim_tp_slippage_pct}
              help={PARAM_HELP.sim_tp_slippage_pct}
              showHelp={activeHelpKey === "sim_tp_slippage_pct"}
              onToggleHelp={() => toggleHelp("sim_tp_slippage_pct")}
              onChange={(v) => setForm({ ...form, sim_tp_slippage_pct: v })}
            />
            <NumericField
              fieldKey="sim_sl_slippage_pct"
              label="Slippage SL / Pánico (%)"
              step="0.5"
              value={form.sim_sl_slippage_pct}
              help={PARAM_HELP.sim_sl_slippage_pct}
              showHelp={activeHelpKey === "sim_sl_slippage_pct"}
              onToggleHelp={() => toggleHelp("sim_sl_slippage_pct")}
              onChange={(v) => setForm({ ...form, sim_sl_slippage_pct: v })}
            />
          </div>

          {/* Strategy Profile Selector: CONSERVADOR vs AGRESIVO (SCALPER) */}
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3.5 space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-xs font-semibold text-white uppercase tracking-wide">
                  Perfil de Velocidad y Riesgo
                </div>
                <div className="text-[11px] text-zinc-400">
                  Mantiene intactos todos los candados de seguridad (Mint/Freeze/LP/Trap).
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setForm({ ...form, strategy_profile: "CONSERVADOR" })}
                  className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition cursor-pointer ${
                    (form.strategy_profile ?? "CONSERVADOR") === "CONSERVADOR"
                      ? "bg-cyan-500/20 border-cyan-400 text-cyan-200"
                      : "border-white/10 text-zinc-400 hover:text-white"
                  }`}
                >
                  CONSERVADOR
                </button>
                <button
                  type="button"
                  onClick={() => setForm({ ...form, strategy_profile: "AGRESIVO" })}
                  className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition cursor-pointer ${
                    form.strategy_profile === "AGRESIVO"
                      ? "bg-amber-500/25 border-amber-400 text-amber-200"
                      : "border-amber-500/35 text-amber-300/80 hover:bg-amber-500/10"
                  }`}
                >
                  AGRESIVO (SCALPER)
                </button>
              </div>
            </div>
            <div className="text-[11px] text-zinc-300 bg-black/40 border border-white/10 rounded-xl px-3 py-2 leading-relaxed">
              {form.strategy_profile === "AGRESIVO" ? (
                <>
                  <strong className="text-amber-300">Wave &amp; Wick Scalper (+10% a +25% · Sin SL negativo):</strong>{" "}
                  escaneo <strong>6s</strong> · salidas cada <strong>3s</strong> · mín.{" "}
                  <strong>60 holders</strong> · Gate <strong>58% / 55%</strong> ·{" "}
                  <strong>Solo cierra en positivo</strong> · Smart BE en <strong>+10%</strong>{" "}
                  (piso <strong>+4%</strong>) · Cazador de Mechas en <strong>+14%</strong> (sale si retrocede 4.5%) · TP1{" "}
                  <strong>+18%</strong> (cobra 60%) · Runner en <strong>+45%</strong> (trailing -9%).
                </>
              ) : (
                <>
                  <strong className="text-cyan-300">Conservador:</strong> escaneo cada{" "}
                  <strong>15s</strong> · mín. <strong>150 holders</strong> · Gate{" "}
                  <strong>75% / 70%</strong> · TP1 <strong>+45%</strong> (cobra 50%) · Stop Loss{" "}
                  <strong>-22%</strong>.
                </>
              )}
            </div>
          </div>

          <label className="flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950/70 px-4 py-3 cursor-pointer">
            <div>
              <div className="text-xs font-semibold text-slate-100 uppercase tracking-wide">
                Modo Semi-Automático (Confirmación 90s)
              </div>
              <div className="text-[11px] text-slate-400">
                Pausa los BUY aprobados por GATE en PENDING_APPROVAL esperando tu clic.
              </div>
            </div>
            <input
              type="checkbox"
              checked={form.semi_auto_mode}
              onChange={(e) => setForm({ ...form, semi_auto_mode: e.target.checked })}
              className="h-4 w-4 accent-cyan-400"
            />
          </label>

          {/* Mode Switch with Dual Lock feedback */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 space-y-2.5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                <ShieldAlert className="w-4 h-4 text-rose-400" />
                Selector de Modo (Doble Candado)
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={saving || hud.mode === "TUTORIAL"}
                  onClick={() => handleModeSwitch("TUTORIAL")}
                  className={`px-3 py-1 rounded-md font-mono text-xs border transition cursor-pointer ${
                    hud.mode === "TUTORIAL"
                      ? "bg-cyan-500/20 border-cyan-400 text-cyan-200"
                      : "border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  TUTORIAL
                </button>
                <button
                  type="button"
                  disabled={saving || hud.mode === "LIVE"}
                  onClick={() => handleModeSwitch("LIVE")}
                  className={`px-3 py-1 rounded-md font-mono text-xs border transition cursor-pointer ${
                    hud.mode === "LIVE"
                      ? "bg-rose-500/25 border-rose-500 text-rose-200"
                      : "border-rose-500/40 text-rose-300 hover:bg-rose-950/50"
                  }`}
                >
                  REAL (LIVE)
                </button>
              </div>
            </div>
            {modeError && (
              <p className="text-xs text-rose-300 bg-rose-950/60 border border-rose-500/40 rounded-lg px-3 py-2">
                {modeError}
              </p>
            )}
          </div>

          {saveError && (
            <p className="text-xs text-rose-300 bg-rose-950/60 border border-rose-500/40 rounded-lg px-3 py-2">
              {saveError}
            </p>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-800">
            <button
              type="button"
              disabled={saving}
              onClick={handleReset}
              className={`px-3.5 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                confirmReset
                  ? "bg-amber-500/25 border-amber-400 text-amber-200"
                  : "border-slate-700 bg-slate-950 text-amber-300 hover:border-amber-500/50"
              }`}
            >
              <RotateCcw className="w-3.5 h-3.5" />
              {confirmReset
                ? `¿CONFIRMAR REINICIO A ${form.tutorial_initial_bankroll_sol} SOL?`
                : "Reiniciar Tutorial"}
            </button>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 rounded-lg border border-slate-700 text-xs text-slate-300 hover:bg-slate-800 cursor-pointer"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={saving}
                className="px-4 py-2 rounded-lg bg-cyan-500 hover:bg-cyan-400 text-slate-950 text-xs font-bold tracking-wide cursor-pointer disabled:opacity-40"
              >
                {saving ? "Guardando..." : "GUARDAR AJUSTES"}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

function NumericField({
  fieldKey,
  label,
  value,
  help,
  showHelp,
  onToggleHelp,
  onChange,
}: {
  fieldKey: string;
  label: string;
  value: number;
  step: string;
  help: FieldHelp;
  showHelp: boolean;
  onToggleHelp: () => void;
  onChange: (val: number) => void;
}) {
  const [text, setText] = useState(() => String(value));

  useEffect(() => {
    setText(String(value));
  }, [value, fieldKey]);

  function handleTextChange(raw: string) {
    setText(raw);
    const normalized = raw.replace(",", ".").trim();
    if (!normalized) return;
    const parsed = Number(normalized);
    if (Number.isFinite(parsed) && parsed >= 0) {
      onChange(parsed);
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-1">
        <span className="block text-[11px] uppercase tracking-wider text-slate-400">
          {label}
        </span>
        <button
          type="button"
          onClick={onToggleHelp}
          title={`¿Qué es ${label}?`}
          className={`p-0.5 rounded-full transition cursor-pointer ${
            showHelp ? "text-cyan-300 bg-cyan-500/20" : "text-slate-500 hover:text-cyan-300"
          }`}
        >
          <HelpCircle className="w-3.5 h-3.5" />
        </button>
      </div>
      <input
        type="text"
        inputMode="decimal"
        value={text}
        onChange={(e) => handleTextChange(e.target.value)}
        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-slate-100 focus:border-cyan-400 focus:outline-none"
      />
      {showHelp && (
        <div className="rounded-lg border border-cyan-500/30 bg-cyan-950/30 px-2.5 py-2 text-[11px] space-y-0.5">
          <div className="text-slate-200">{help.desc}</div>
          <div className="text-cyan-300">{help.impact}</div>
        </div>
      )}
    </div>
  );
}

interface PanicModalProps {
  open: boolean;
  openCount: number;
  mode: TradingMode;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}

export function PanicModal({
  open,
  openCount,
  mode,
  busy,
  onCancel,
  onConfirm,
}: PanicModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-3xl glass-panel border-rose-500/50 p-6">
        <div className="flex items-start gap-3 mb-4">
          <div className="h-10 w-10 rounded-xl bg-rose-500/20 border border-rose-500/50 flex items-center justify-center text-rose-400 shrink-0">
            <AlertTriangle className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-bold uppercase tracking-wider text-rose-200">
              Confirmar Pánico — Cerrar Todo
            </h2>
            <p className="text-xs text-slate-300 mt-1">
              Esta acción pausará inmediatamente el escáner y liquidará el 100% de{" "}
              <span className="font-mono font-bold text-rose-300">{openCount}</span> posición(es)
              abierta(s) en modo <span className="font-mono font-bold">{mode}</span> con slippage de
              emergencia.
            </p>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2.5 mt-6">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="px-4 py-2 rounded-lg border border-slate-700 text-xs font-medium text-slate-200 hover:bg-slate-800 cursor-pointer"
          >
            Cancelar
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onConfirm()}
            className="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold uppercase tracking-wide cursor-pointer disabled:opacity-40"
          >
            {busy ? "Liquidando..." : "SÍ, CERRAR TODO Y PAUSAR"}
          </button>
        </div>
      </div>
    </div>
  );
}
