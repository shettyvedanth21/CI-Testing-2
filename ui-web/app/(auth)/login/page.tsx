"use client";

import Image from "next/image";
import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/authContext";

function Spinner() {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="10" className="opacity-20" stroke="currentColor" strokeWidth="4" />
      <path
        d="M22 12a10 10 0 0 1-10 10"
        className="opacity-90"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function WarningIcon() {
  return (
    <svg aria-hidden="true" className="h-4 w-4 shrink-0" viewBox="0 0 20 20" fill="none">
      <path
        d="M10 2.75 18 16.5H2L10 2.75Z"
        className="fill-current"
        fillOpacity="0.18"
      />
      <path
        d="M10 6.5v4.25"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="10" cy="13.75" r="0.9" fill="currentColor" />
    </svg>
  );
}

export default function LoginPage() {
  const { login, isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      router.replace("/machines");
    }
  }, [isAuthenticated, isLoading, router]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      await login(email, password);
      router.push("/machines");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading || isAuthenticated) {
    return null;
  }

  const submitDisabled = isSubmitting;

  return (
    <div className="min-h-screen bg-[#07111f] text-slate-100">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(56,189,248,0.14),transparent_30%),radial-gradient(circle_at_bottom_left,rgba(245,158,11,0.12),transparent_28%),linear-gradient(160deg,#07111f_0%,#0b1625_50%,#08111d_100%)]" />
      <div className="relative flex min-h-screen items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex flex-col items-center text-center">
            <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-cyan-300/35 bg-[linear-gradient(180deg,rgba(15,23,42,0.92),rgba(2,6,23,0.88))] shadow-[0_0_34px_rgba(14,165,233,0.18)]">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-white/40 bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(226,232,240,0.86))] shadow-[inset_0_1px_0_rgba(255,255,255,0.85),0_10px_18px_rgba(15,23,42,0.28)]">
                <Image
                  src="/shivex-login-icon.png"
                  alt="Shivex icon"
                  width={315}
                  height={335}
                  className="h-8 w-8 object-contain drop-shadow-[0_1px_2px_rgba(15,23,42,0.18)]"
                  priority
                />
              </div>
            </div>
            <p className="text-xs font-semibold uppercase tracking-[0.26em] text-cyan-300/80">
              Shivex
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-[-0.03em] text-white">
              Industrial operations platform
            </h1>
            <p className="mt-2 text-sm text-slate-300">
              Sign in to monitor plants, machines, reports, and operations.
            </p>
          </div>

          <div className="rounded-[1.5rem] border border-white/10 bg-white/5 p-6 shadow-[0_24px_80px_rgba(2,6,23,0.45)] backdrop-blur-xl">
            {error ? (
              <div className="mb-5 flex items-start gap-3 rounded-2xl border border-red-400/35 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                <WarningIcon />
                <span>{error}</span>
              </div>
            ) : null}

            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="space-y-1.5">
                <label htmlFor="email" className="block text-sm font-medium text-slate-200">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@company.com"
                  required
                  disabled={isSubmitting}
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  className="block h-12 w-full rounded-xl border border-white/10 bg-slate-950/45 px-3 text-sm text-slate-100 shadow-sm transition placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] disabled:cursor-not-allowed disabled:opacity-60"
                />
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between gap-3">
                  <label htmlFor="password" className="block text-sm font-medium text-slate-200">
                    Password
                  </label>
                  <Link
                    href="/forgot-password"
                    className="text-xs font-medium text-cyan-300 transition hover:text-cyan-200"
                  >
                    Forgot password?
                  </Link>
                </div>
                <input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="Password"
                  required
                  disabled={isSubmitting}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="block h-12 w-full rounded-xl border border-white/10 bg-slate-950/45 px-3 text-sm text-slate-100 shadow-sm transition placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] disabled:cursor-not-allowed disabled:opacity-60"
                />
              </div>

              <button
                type="submit"
                disabled={submitDisabled}
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-transparent bg-[linear-gradient(135deg,#f59e0b,#f97316)] px-4 text-sm font-semibold text-slate-950 shadow-[0_12px_28px_rgba(249,115,22,0.28)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmitting ? <Spinner /> : null}
                <span>{isSubmitting ? "Signing in..." : "Sign in"}</span>
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
