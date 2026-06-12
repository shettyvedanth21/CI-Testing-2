"use client";

import { useEffect, useRef } from "react";
import { getLastObservedBackendSession } from "@/lib/api";

type UseAdaptivePollingOptions = {
  onBackendRestart?: () => void | Promise<void>;
  getBackendSession?: () => string | null;
};

export function useAdaptivePolling(
  task: () => void | Promise<void>,
  activeMs: number,
  hiddenMs: number,
  options?: UseAdaptivePollingOptions,
) {
  const taskRef = useRef(task);
  const backendSessionRef = useRef<string | null>(null);
  const onBackendRestartRef = useRef(options?.onBackendRestart);
  const getBackendSessionRef = useRef(options?.getBackendSession ?? getLastObservedBackendSession);

  useEffect(() => {
    taskRef.current = task;
  }, [task]);

  useEffect(() => {
    onBackendRestartRef.current = options?.onBackendRestart;
    getBackendSessionRef.current = options?.getBackendSession ?? getLastObservedBackendSession;
    const latestKnownSession = getBackendSessionRef.current?.() ?? null;
    if (latestKnownSession) {
      backendSessionRef.current = latestKnownSession;
    }
  }, [options?.getBackendSession, options?.onBackendRestart]);

  useEffect(() => {
    let timer: number | null = null;
    let stopped = false;
    const schedule = () => {
      if (stopped) return;
      const delay = document.hidden ? hiddenMs : activeMs;
      timer = window.setTimeout(async () => {
        await taskRef.current();

        const backendSession = getBackendSessionRef.current?.() ?? null;
        if (backendSession) {
          const previousSession = backendSessionRef.current;
          backendSessionRef.current = backendSession;
          if (previousSession && previousSession !== backendSession) {
            await onBackendRestartRef.current?.();
          }
        }

        schedule();
      }, delay);
    };
    schedule();

    const onVisibility = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
      }
      schedule();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [activeMs, hiddenMs]);
}
