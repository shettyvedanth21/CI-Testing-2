"use client";

import { FormEvent, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-scaffold";
import {
  activateTariffVersion,
  CurrencyCode,
  getTariffConfig,
  getTariffHistory,
  saveTariffConfig,
  type TariffHistoryEntry,
} from "@/lib/settingsApi";
import { formatIST } from "@/lib/utils";

function formatTariff(rate: number | null, currency: CurrencyCode) {
  if (rate == null) return "Not configured";
  const symbol = currency === "INR" ? "₹" : currency === "USD" ? "$" : "€";
  return `${symbol}${rate.toFixed(2)} / kWh`;
}

function formatDate(value: string | null) {
  return formatIST(value, "Never");
}

export default function SettingsPage() {
  const [loading, setLoading] = useState(true);
  const [savingTariff, setSavingTariff] = useState(false);
  const [activatingVersionId, setActivatingVersionId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rateError, setRateError] = useState<string | null>(null);

  const [rateInput, setRateInput] = useState<string>("");
  const [currency, setCurrency] = useState<CurrencyCode>("INR");
  const [tariffHistory, setTariffHistory] = useState<TariffHistoryEntry[]>([]);
  const [currentTariff, setCurrentTariff] = useState<{
    rate: number | null;
    currency: CurrencyCode;
    updated_at: string | null;
  }>({
    rate: null,
    currency: "INR",
    updated_at: null,
  });

  async function loadTariff() {
    setLoading(true);
    setError(null);
    try {
      const tariff = await getTariffConfig();
      setCurrentTariff({
        rate: tariff.rate,
        currency: tariff.currency,
        updated_at: tariff.updated_at,
      });
      setCurrency(tariff.currency || "INR");
      setRateInput(tariff.rate == null ? "" : String(tariff.rate));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load settings");
    }

    try {
      const history = await getTariffHistory();
      setTariffHistory(history.versions);
    } catch {
      setTariffHistory([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadTariff();
  }, []);

  async function handleApplyTariff(e: FormEvent) {
    e.preventDefault();
    const normalizedInput = rateInput.trim();
    const parsed = Number(normalizedInput);
    if (!normalizedInput) {
      setRateError("Rate is required.");
      return;
    }
    if (!Number.isFinite(parsed)) {
      setRateError("Rate must be a valid number.");
      return;
    }
    if (parsed <= 0) {
      setRateError("Rate must be a valid positive number");
      return;
    }
    setSavingTariff(true);
    setError(null);
    setRateError(null);
    try {
      const saved = await saveTariffConfig({ rate: parsed, currency, updated_by: "settings-ui" });
      setCurrentTariff(saved);
      const history = await getTariffHistory();
      setTariffHistory(history.versions);
      setToast("Tariff updated");
      setTimeout(() => setToast(null), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update tariff");
    } finally {
      setSavingTariff(false);
    }
  }

  async function handleActivateVersion(versionId: string) {
    setActivatingVersionId(versionId);
    setError(null);
    try {
      const activated = await activateTariffVersion(versionId);
      setCurrentTariff(activated);
      setCurrency(activated.currency || "INR");
      setRateInput(activated.rate == null ? "" : String(activated.rate));
      const history = await getTariffHistory();
      setTariffHistory(history.versions);
      setToast("Tariff version activated");
      setTimeout(() => setToast(null), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to activate tariff version");
    } finally {
      setActivatingVersionId(null);
    }
  }

  if (loading) {
    return (
      <div className="py-5">
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="section-spacing">
      <PageHeader title="Settings" subtitle="Configure platform tariff" />
      <div className="mx-auto w-full max-w-4xl space-y-6">
        {toast && (
          <div className="rounded-xl border border-[var(--tone-success-border)] bg-[var(--tone-success-bg)] px-3 py-2 text-sm text-[var(--tone-success-text)]">
            {toast}
          </div>
        )}
        {error && (
          <div className="rounded-xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-3 py-2 text-sm text-[var(--tone-danger-text)]">
            {error}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Tariff Configuration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <form className="grid grid-cols-1 gap-3 md:grid-cols-3" onSubmit={handleApplyTariff}>
              <Input
                label="Energy Rate (per kWh)"
                type="text"
                inputMode="decimal"
                value={rateInput}
                onChange={(e) => {
                  setRateInput(e.target.value);
                  if (rateError) {
                    setRateError(null);
                  }
                }}
                placeholder="8.50"
                error={rateError ?? undefined}
                helperText="Enter a positive numeric rate. Non-numeric values are rejected before save."
              />
              <Select
                label="Currency"
                value={currency}
                onChange={(e) => setCurrency(e.target.value as CurrencyCode)}
                options={[
                  { value: "INR", label: "INR" },
                  { value: "USD", label: "USD" },
                  { value: "EUR", label: "EUR" },
                ]}
              />
              <div className="pt-6">
                <Button type="submit" disabled={savingTariff}>
                  {savingTariff ? "Applying..." : "Apply"}
                </Button>
              </div>
            </form>

            <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
              Current tariff: {formatTariff(currentTariff.rate, currentTariff.currency)}
              <br />
              Updated: {formatDate(currentTariff.updated_at)}
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">Tariff History</h3>
                  <p className="text-xs text-slate-500">Re-activate a historical tariff version to validate cost-boundary behavior.</p>
                </div>
              </div>
              {tariffHistory.length === 0 ? (
                <p className="mt-3 text-sm text-slate-500">No saved tariff history yet.</p>
              ) : (
                <div className="mt-4 space-y-3">
                  {tariffHistory.map((version) => (
                    <div key={version.id} className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 md:flex-row md:items-center md:justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-semibold text-slate-900">{formatTariff(version.rate, version.currency)}</p>
                          {version.is_active ? (
                            <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                              Active
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-1 text-xs text-slate-500">
                          Effective from {formatDate(version.effective_from)} • Updated {formatDate(version.updated_at)}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant={version.is_active ? "outline" : "secondary"}
                        disabled={version.is_active || activatingVersionId === version.id}
                        isLoading={activatingVersionId === version.id}
                        onClick={() => void handleActivateVersion(version.id)}
                      >
                        {version.is_active ? "Active version" : "Use this version"}
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              Alert recipients are now managed directly on each rule. Settings retains only the organisation tariff.
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
