import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Check,
  Copy,
  ExternalLink,
  KeyRound,
  Plus,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wallet,
  X,
} from "lucide-react";
import { api } from "../api";
import { fmtSol, fmtUsd, shortAddr } from "../format";
import type { WalletStatusResponse } from "../types";

interface LiveWalletModalProps {
  open: boolean;
  onClose: () => void;
  onNotify: (kind: "ok" | "err", text: string) => void;
}

export function LiveWalletModal({ open, onClose, onNotify }: LiveWalletModalProps) {
  const [status, setStatus] = useState<WalletStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const [privateKeyInput, setPrivateKeyInput] = useState("");
  const [destAddr, setDestAddr] = useState("");
  const [destLabel, setDestLabel] = useState("");
  const [withdrawDest, setWithdrawDest] = useState("");
  const [withdrawAmount, setWithdrawAmount] = useState("0.1");
  const [lastTxUrl, setLastTxUrl] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.walletStatus();
      setStatus(res);
      if (res.withdrawal_wallets.length > 0 && !withdrawDest) {
        setWithdrawDest(res.withdrawal_wallets[0].address);
      }
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "Error cargando billetera.");
    } finally {
      setLoading(false);
    }
  }, [onNotify, withdrawDest]);

  useEffect(() => {
    if (open) {
      setPrivateKeyInput("");
      setLastTxUrl(null);
      void loadStatus();
    }
  }, [open, loadStatus]);

  if (!open) return null;

  function copyPub() {
    if (!status?.public_address) return;
    navigator.clipboard?.writeText(status.public_address).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  async function handleSaveKey(e: FormEvent) {
    e.preventDefault();
    if (!privateKeyInput.trim()) return;
    setBusy(true);
    try {
      const res = await api.configureWallet({ private_key: privateKeyInput.trim() });
      setPrivateKeyInput("");
      onNotify("ok", `Billetera configurada en .env (${shortAddr(res.public_address ?? "", 6, 4)}).`);
      await loadStatus();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "Clave privada inválida.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateWallet() {
    setBusy(true);
    try {
      const res = await api.generateWallet();
      onNotify("ok", `Nueva billetera dedicada creada: ${shortAddr(res.public_address, 6, 4)}.`);
      await loadStatus();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "No se pudo generar billetera.");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleLiveLock() {
    if (!status) return;
    setBusy(true);
    try {
      const next = !status.enable_live_execution;
      await api.configureWallet({ enable_live_execution: next });
      onNotify(
        "ok",
        next ? "Doble candado LIVE habilitado en .env." : "Doble candado LIVE bloqueado.",
      );
      await loadStatus();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "Error actualizando candado.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddWhitelist(e: FormEvent) {
    e.preventDefault();
    if (!destAddr.trim()) return;
    setBusy(true);
    try {
      await api.addWithdrawalWallet(destAddr.trim(), destLabel.trim());
      setDestAddr("");
      setDestLabel("");
      onNotify("ok", "Wallet de retiro registrada en lista blanca.");
      await loadStatus();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "Dirección Solana inválida.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveWhitelist(address: string) {
    setBusy(true);
    try {
      await api.removeWithdrawalWallet(address);
      onNotify("ok", "Wallet eliminada de destinos de retiro.");
      await loadStatus();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "Error eliminando destino.");
    } finally {
      setBusy(false);
    }
  }

  async function handleWithdraw(e: FormEvent) {
    e.preventDefault();
    const amt = parseFloat(withdrawAmount.replace(",", "."));
    if (!withdrawDest || !Number.isFinite(amt) || amt <= 0) return;
    setBusy(true);
    setLastTxUrl(null);
    try {
      const res = await api.withdrawSol(withdrawDest, amt);
      setLastTxUrl(res.solscan_url);
      onNotify("ok", `Retiro enviado: ${res.amount_sol.toFixed(4)} SOL → ${res.destination_label}`);
      await loadStatus();
    } catch (err) {
      onNotify("err", err instanceof Error ? err.message : "No se pudo completar el retiro.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-3 sm:p-5"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[90vh] rounded-3xl glass-panel border-emerald-400/35 overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-white/10 bg-white/[0.03] flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl border border-emerald-400/40 bg-emerald-500/15 flex items-center justify-center text-emerald-300">
              <Wallet className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-sm sm:text-base font-bold text-white">
                Billetera Real Solana (LIVE) y Retiros Autorizados
              </h2>
              <p className="text-[11px] text-zinc-400">
                Tu clave privada se guarda únicamente en tu <code>.env</code> local — nunca sale al navegador.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-xl bg-white/[0.06] hover:bg-white/15 text-zinc-300 cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 overflow-y-auto space-y-5">
          {/* Security Notice */}
          <div className="rounded-2xl border border-amber-400/35 bg-amber-500/10 p-3.5 text-xs text-amber-100 leading-relaxed">
            <strong>Regla de oro de seguridad:</strong> en Solana <strong>NUNCA</strong> uses la frase semilla de 12 palabras de tu billetera principal. Crea una billetera dedicada solo para trading (puedes generarla abajo en 1 clic o exportar la <strong>Clave Privada Base58</strong> de una cuenta secundaria de Phantom/Solflare) y fondea únicamente el capital que estés dispuesto a operar.
          </div>

          {/* Current Wallet Status */}
          <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-zinc-200 flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-emerald-300" /> Estado de tu Billetera LIVE
              </span>
              {status && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleToggleLiveLock()}
                  className={`px-3 py-1 rounded-xl border text-[11px] font-bold cursor-pointer transition ${
                    status.enable_live_execution
                      ? "border-emerald-400/60 bg-emerald-500/20 text-emerald-200"
                      : "border-rose-400/50 bg-rose-500/15 text-rose-200"
                  }`}
                >
                  {status.enable_live_execution
                    ? "Doble Candado LIVE: HABILITADO"
                    : "Doble Candado LIVE: BLOQUEADO"}
                </button>
              )}
            </div>

            {loading && !status ? (
              <div className="text-xs text-zinc-400">Consultando saldo en Helius RPC...</div>
            ) : status?.configured && status.public_address ? (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="sm:col-span-2 rounded-xl border border-white/10 bg-black/50 p-3 space-y-1.5">
                  <div className="text-[10px] text-zinc-400 uppercase">Dirección Pública (Para Depositar SOL)</div>
                  <div className="text-xs font-mono text-white break-all">{status.public_address}</div>
                  <div className="flex flex-wrap gap-2 pt-1">
                    <button
                      type="button"
                      onClick={copyPub}
                      className="px-2.5 py-1 rounded-lg bg-white/10 hover:bg-white/15 text-[11px] text-white inline-flex items-center gap-1 cursor-pointer"
                    >
                      {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                      {copied ? "Copiada" : "Copiar dirección"}
                    </button>
                    {status.solscan_url && (
                      <a
                        href={status.solscan_url}
                        target="_blank"
                        rel="noreferrer"
                        className="px-2.5 py-1 rounded-lg bg-cyan-500/15 border border-cyan-400/35 text-[11px] text-cyan-200 inline-flex items-center gap-1"
                      >
                        Solscan <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                  </div>
                </div>
                <div className="rounded-xl border border-emerald-400/30 bg-emerald-950/20 p-3">
                  <div className="text-[10px] text-emerald-300 uppercase">Saldo Real (Helius RPC)</div>
                  <div className="text-xl font-extrabold text-white tabular-nums mt-1">
                    {status.sol_balance !== null ? fmtSol(status.sol_balance, 4) : "—"}
                  </div>
                  <div className="text-xs text-emerald-300 tabular-nums">
                    {status.usd_balance !== null ? fmtUsd(status.usd_balance) : "—"}
                  </div>
                  <div className="text-[10px] text-zinc-400 mt-1">
                    Tope trade: {status.max_live_trade_sol} SOL · Gas min: {status.min_live_gas_reserve_sol} SOL
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-xs text-zinc-400">
                Aún no hay una billetera real configurada. Importa una clave privada Base58 abajo o genera una billetera trading dedicada.
              </div>
            )}

            {/* Import Base58 Key or Generate New Dedicated Wallet */}
            <form onSubmit={handleSaveKey} className="grid grid-cols-1 sm:grid-cols-12 gap-2 pt-1">
              <div className="sm:col-span-7 relative">
                <KeyRound className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="password"
                  value={privateKeyInput}
                  onChange={(e) => setPrivateKeyInput(e.target.value)}
                  placeholder="Pegar Clave Privada Base58 de Phantom/Solflare (solo escritura)..."
                  className="w-full rounded-xl border border-white/15 bg-black/60 pl-9 pr-3 py-2 text-xs text-white focus:border-emerald-400 focus:outline-none"
                />
              </div>
              <button
                type="submit"
                disabled={busy || !privateKeyInput.trim()}
                className="sm:col-span-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold py-2 cursor-pointer disabled:opacity-40"
              >
                Guardar
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void handleGenerateWallet()}
                className="sm:col-span-3 rounded-xl border border-cyan-400/50 bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-200 text-xs font-semibold py-2 inline-flex items-center justify-center gap-1 cursor-pointer disabled:opacity-40"
              >
                <Sparkles className="w-3.5 h-3.5" /> Crear Nueva
              </button>
            </form>
          </section>

          {/* Whitelisted Withdrawal Wallets & Send SOL */}
          <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 space-y-3.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-cyan-200 flex items-center gap-1.5">
                <Send className="w-4 h-4 text-cyan-300" /> Retiros Seguros (Solo Destinos Registrados)
              </span>
              <span className="text-[10px] text-zinc-400">
                También funciona en Telegram con <code>/retirar</code>
              </span>
            </div>

            {/* Register New Destination */}
            <form onSubmit={handleAddWhitelist} className="grid grid-cols-1 sm:grid-cols-12 gap-2">
              <input
                type="text"
                value={destAddr}
                onChange={(e) => setDestAddr(e.target.value)}
                placeholder="Dirección Solana destino (ej. tu Phantom o Binance)..."
                className="sm:col-span-6 rounded-xl border border-white/15 bg-black/60 px-3 py-2 text-xs text-white focus:border-cyan-400 focus:outline-none"
              />
              <input
                type="text"
                value={destLabel}
                onChange={(e) => setDestLabel(e.target.value)}
                placeholder="Alias (ej. Mi Phantom)"
                className="sm:col-span-4 rounded-xl border border-white/15 bg-black/60 px-3 py-2 text-xs text-white focus:border-cyan-400 focus:outline-none"
              />
              <button
                type="submit"
                disabled={busy || destAddr.trim().length < 20}
                className="sm:col-span-2 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-black text-xs font-bold py-2 inline-flex items-center justify-center gap-1 cursor-pointer disabled:opacity-40"
              >
                <Plus className="w-3.5 h-3.5" /> Registrar
              </button>
            </form>

            {/* Registered List */}
            {status && status.withdrawal_wallets.length > 0 && (
              <div className="space-y-1.5">
                {status.withdrawal_wallets.map((w) => (
                  <div
                    key={w.address}
                    className="rounded-xl border border-white/10 bg-black/45 px-3 py-2 flex items-center justify-between gap-2 text-xs"
                  >
                    <div className="min-w-0">
                      <span className="font-semibold text-white">{w.label}</span>{" "}
                      <span className="font-mono text-zinc-400">{shortAddr(w.address, 8, 6)}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleRemoveWhitelist(w.address)}
                      className="p-1.5 rounded-lg text-zinc-400 hover:text-rose-300 hover:bg-rose-500/15 cursor-pointer"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Execute SOL Withdrawal */}
            {status && status.withdrawal_wallets.length > 0 && status.configured && (
              <form
                onSubmit={handleWithdraw}
                className="grid grid-cols-1 sm:grid-cols-12 gap-2 pt-2 border-t border-white/10"
              >
                <select
                  value={withdrawDest}
                  onChange={(e) => setWithdrawDest(e.target.value)}
                  className="sm:col-span-6 rounded-xl border border-white/15 bg-black/70 px-3 py-2 text-xs text-white focus:border-emerald-400 focus:outline-none"
                >
                  {status.withdrawal_wallets.map((w) => (
                    <option key={w.address} value={w.address}>
                      {w.label} ({shortAddr(w.address, 6, 4)})
                    </option>
                  ))}
                </select>
                <input
                  type="text"
                  inputMode="decimal"
                  value={withdrawAmount}
                  onChange={(e) => setWithdrawAmount(e.target.value)}
                  placeholder="SOL a enviar"
                  className="sm:col-span-3 rounded-xl border border-white/15 bg-black/70 px-3 py-2 text-xs text-white tabular-nums focus:border-emerald-400 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={busy}
                  className="sm:col-span-3 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold py-2 inline-flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-40"
                >
                  <Send className="w-3.5 h-3.5" /> {busy ? "Enviando..." : "Enviar SOL"}
                </button>
              </form>
            )}

            {lastTxUrl && (
              <div className="rounded-xl border border-emerald-400/40 bg-emerald-500/15 px-3 py-2 text-xs text-emerald-200 flex items-center justify-between">
                <span>Retiro confirmado en Solana Mainnet.</span>
                <a
                  href={lastTxUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="underline font-bold inline-flex items-center gap-1"
                >
                  Ver en Solscan <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
