import Link from "next/link";
import { SuperAdminSummaryCards } from "@/components/home/SuperAdminSummaryCards";
import { PageHeader, SectionCard } from "@/components/ui/page-scaffold";

export default function HomePage() {
  return (
    <div className="section-spacing">
      <PageHeader
        title="Shivex"
        subtitle="Industrial monitoring, diagnostics, and automation in one workspace."
      />
      <SuperAdminSummaryCards />
      <SectionCard title="Start Here" subtitle="Navigate to live operations dashboards">
        <div className="py-8 text-center sm:py-12">
          <h1 className="mb-3 text-4xl font-bold tracking-[-0.03em] text-slate-900">Operate. Diagnose. Optimize.</h1>
          <p className="mx-auto mb-8 max-w-2xl text-base text-slate-500">
            Unified machine operations experience with fleet visibility, analytics, and production-grade controls.
          </p>
          <Link
            href="/machines"
            className="inline-flex h-11 items-center justify-center rounded-xl bg-[linear-gradient(135deg,#0ea5e9,#2563eb)] px-6 text-sm font-semibold text-white shadow-sm transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
          >
            View Machines
          </Link>
        </div>
        <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
          <div className="surface-panel p-6 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-blue-100">
              <svg className="w-6 h-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
              </svg>
            </div>
            <h3 className="font-semibold text-slate-900 mb-2">Machines</h3>
            <p className="text-sm text-slate-500">Monitor and manage your industrial equipment</p>
          </div>

          <div className="surface-panel p-6 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-green-100">
              <svg className="w-6 h-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            </div>
            <h3 className="font-semibold text-slate-900 mb-2">Analytics</h3>
            <p className="text-sm text-slate-500">AI-powered anomaly detection and forecasting</p>
          </div>

          <div className="surface-panel p-6 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-amber-100">
              <svg className="w-6 h-6 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h3 className="font-semibold text-slate-900 mb-2">Rules</h3>
            <p className="text-sm text-slate-500">Configure alerts and monitoring rules</p>
          </div>
        </div>
      </SectionCard>
    </div>
  );
}
