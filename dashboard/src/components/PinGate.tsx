import { useState, type FormEvent } from "react";
import { Lock, Radar } from "lucide-react";

interface PinGateProps {
  onLogin: (pin: string) => Promise<void>;
}

export function PinGate({ onLogin }: PinGateProps) {
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!pin.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await onLogin(pin.trim());
    } catch {
      setError("PIN incorrecto. Acceso denegado.");
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-4">
      <div className="w-full max-w-sm rounded-3xl glass-panel p-7">
        <div className="flex items-center gap-3 mb-6">
          <div className="h-11 w-11 rounded-xl bg-cyan-500/15 border border-cyan-500/40 flex items-center justify-center text-cyan-400">
            <Radar className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-base font-bold tracking-wider uppercase text-slate-100">
              METEORO TRADER
            </h1>
            <p className="text-xs text-slate-400">Solana Judge Desk Command Center</p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wider text-slate-400 mb-2">
              PIN de Acceso
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                type="password"
                inputMode="numeric"
                autoFocus
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                placeholder="••••"
                className="w-full rounded-xl border border-slate-700 bg-slate-950 pl-10 pr-4 py-3 font-mono text-lg tracking-[0.35em] text-slate-100 focus:border-cyan-400 focus:outline-none"
              />
            </div>
          </div>

          {error && (
            <p className="text-xs text-rose-400 bg-rose-950/50 border border-rose-500/30 rounded-lg px-3 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !pin.trim()}
            className="w-full rounded-xl bg-cyan-500 hover:bg-cyan-400 disabled:opacity-40 text-slate-950 font-semibold py-2.5 text-sm tracking-wide transition cursor-pointer"
          >
            {busy ? "Verificando..." : "DESBLOQUEAR RADAR"}
          </button>
        </form>
      </div>
    </div>
  );
}
