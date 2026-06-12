"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { authApi, type ActionTokenStatus } from "@/lib/authApi";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [status, setStatus] = useState<ActionTokenStatus | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let active = true;

    async function loadStatus(): Promise<void> {
      const nextToken = new URLSearchParams(window.location.search).get("token") ?? "";
      setToken(nextToken);
      if (!nextToken) {
        setStatus({ status: "invalid", action_type: null, email: null, full_name: null });
        setIsLoading(false);
        return;
      }
      try {
        const nextStatus = await authApi.getActionTokenStatus(nextToken);
        if (active) {
          setStatus(nextStatus);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to validate reset link");
        }
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    }

    void loadStatus();
    return () => {
      active = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    setIsSubmitting(true);
    try {
      await authApi.resetPassword(token, password, confirmPassword);
      setSuccess("Password reset successfully. Redirecting to sign in...");
      window.setTimeout(() => router.push("/login"), 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reset password");
    } finally {
      setIsSubmitting(false);
    }
  }

  const isValidReset = status?.status === "valid" && status.action_type === "password_reset";

  return (
    <div className="min-h-screen bg-[#07111f] text-slate-100">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(56,189,248,0.14),transparent_30%),radial-gradient(circle_at_bottom_left,rgba(245,158,11,0.12),transparent_28%),linear-gradient(160deg,#07111f_0%,#0b1625_50%,#08111d_100%)]" />
      <div className="relative flex min-h-screen items-center justify-center px-4 py-10">
        <div className="w-full max-w-md rounded-[1.5rem] border border-white/10 bg-white/5 p-6 shadow-[0_24px_80px_rgba(2,6,23,0.45)] backdrop-blur-xl">
          <h1 className="text-2xl font-semibold tracking-[-0.03em] text-white">Choose a new password</h1>
          <p className="mt-2 text-sm text-slate-300">
            Reset your password securely and sign back in.
          </p>

          {isLoading ? (
            <div className="mt-6 text-sm text-slate-300">Checking your reset link...</div>
          ) : !isValidReset ? (
            <div className="mt-6 space-y-4">
              <div className="rounded-2xl border border-red-400/35 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                {status?.status === "expired"
                  ? "This reset link has expired."
                  : status?.status === "used"
                    ? "This reset link has already been used."
                    : "This reset link is invalid."}
              </div>
              <Link href="/forgot-password" className="text-sm font-medium text-cyan-300 transition hover:text-cyan-200">
                Request a new reset link
              </Link>
            </div>
          ) : (
            <form className="mt-6 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
              {status.email ? (
                <div className="rounded-2xl border border-cyan-400/25 bg-cyan-500/10 px-4 py-3 text-sm text-cyan-100">
                  Resetting password for <span className="font-medium">{status.email}</span>
                </div>
              ) : null}

              <div className="space-y-1.5">
                <label htmlFor="password" className="block text-sm font-medium text-slate-200">
                  New password
                </label>
                <input
                  id="password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  disabled={isSubmitting}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="block h-12 w-full rounded-xl border border-white/10 bg-slate-950/45 px-3 text-sm text-slate-100 shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="confirmPassword" className="block text-sm font-medium text-slate-200">
                  Confirm password
                </label>
                <input
                  id="confirmPassword"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  disabled={isSubmitting}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  className="block h-12 w-full rounded-xl border border-white/10 bg-slate-950/45 px-3 text-sm text-slate-100 shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
                />
              </div>

              {error ? (
                <div className="rounded-2xl border border-red-400/35 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                  {error}
                </div>
              ) : null}

              {success ? (
                <div className="rounded-2xl border border-emerald-400/35 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
                  {success}
                </div>
              ) : null}

              <button
                type="submit"
                disabled={isSubmitting || !password || !confirmPassword}
                className="inline-flex h-12 w-full items-center justify-center rounded-xl border border-transparent bg-[linear-gradient(135deg,#f59e0b,#f97316)] px-4 text-sm font-semibold text-slate-950 shadow-[0_12px_28px_rgba(249,115,22,0.28)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmitting ? "Saving..." : "Reset password"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
