"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { authApi } from "@/lib/authApi";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    setIsSubmitting(true);
    try {
      await authApi.requestPasswordReset(email.trim());
      setSuccess("If that email is registered, a password reset link has been sent.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to request password reset");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#07111f] text-slate-100">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(56,189,248,0.14),transparent_30%),radial-gradient(circle_at_bottom_left,rgba(245,158,11,0.12),transparent_28%),linear-gradient(160deg,#07111f_0%,#0b1625_50%,#08111d_100%)]" />
      <div className="relative flex min-h-screen items-center justify-center px-4 py-10">
        <div className="w-full max-w-md rounded-[1.5rem] border border-white/10 bg-white/5 p-6 shadow-[0_24px_80px_rgba(2,6,23,0.45)] backdrop-blur-xl">
          <h1 className="text-2xl font-semibold tracking-[-0.03em] text-white">Reset your password</h1>
          <p className="mt-2 text-sm text-slate-300">
            Enter your email and we&apos;ll send a secure reset link.
          </p>

          <form className="mt-6 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
            <div className="space-y-1.5">
              <label htmlFor="email" className="block text-sm font-medium text-slate-200">
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                disabled={isSubmitting}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="block h-12 w-full rounded-xl border border-white/10 bg-slate-950/45 px-3 text-sm text-slate-100 shadow-sm transition placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] disabled:cursor-not-allowed disabled:opacity-60"
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
              disabled={isSubmitting || !email}
              className="inline-flex h-12 w-full items-center justify-center rounded-xl border border-transparent bg-[linear-gradient(135deg,#f59e0b,#f97316)] px-4 text-sm font-semibold text-slate-950 shadow-[0_12px_28px_rgba(249,115,22,0.28)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? "Sending..." : "Send reset link"}
            </button>
          </form>

          <div className="mt-6 text-sm text-slate-300">
            <Link href="/login" className="font-medium text-cyan-300 transition hover:text-cyan-200">
              Back to sign in
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
