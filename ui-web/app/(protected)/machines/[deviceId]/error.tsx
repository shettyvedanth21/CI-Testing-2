"use client";

import Link from "next/link";
import { useEffect } from "react";

type ErrorPageProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function MachineDetailsError({ error, reset }: ErrorPageProps) {
  useEffect(() => {
    console.error("Machines/[deviceId] route error:", error);
  }, [error]);

  return (
    <div className="p-8">
      <div className="mx-auto max-w-3xl rounded-2xl border border-red-200 bg-red-50 p-6">
        <h2 className="text-xl font-semibold text-red-900">Machine page failed to load</h2>
        <p className="mt-2 text-sm text-red-700">
          A client-side error occurred while rendering this machine. Try loading the page again.
        </p>
        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={reset}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            Try again
          </button>
          <Link
            href="/machines"
            className="rounded-lg border border-red-300 bg-white px-4 py-2 text-sm font-medium text-red-800 hover:bg-red-100"
          >
            Back to Machines
          </Link>
        </div>
      </div>
    </div>
  );
}
