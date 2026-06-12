function toErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  if (typeof error === "string" && error) {
    return error;
  }
  return fallback;
}

function isRetryableFleetLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  if (error.name === "AbortError") {
    return true;
  }
  const message = error.message.toLowerCase();
  return message.includes("timed out") || message.includes("timeout");
}

export interface MachinesInitialLoadResult {
  fatalError: string | null;
  summaryPromise: Promise<unknown>;
}

export async function loadMachinesInitialChannels({
  loadFleet,
  loadSummary,
  fallbackError = "Failed to fetch machines",
}: {
  loadFleet: () => Promise<unknown>;
  loadSummary: () => Promise<unknown>;
  fallbackError?: string;
}): Promise<MachinesInitialLoadResult> {
  const summaryPromise = Promise.resolve()
    .then(loadSummary)
    .catch(() => undefined);

  try {
    await loadFleet();
    return {
      fatalError: null,
      summaryPromise,
    };
  } catch (error) {
    if (isRetryableFleetLoadError(error)) {
      try {
        await loadFleet();
        return {
          fatalError: null,
          summaryPromise,
        };
      } catch (retryError) {
        return {
          fatalError: toErrorMessage(retryError, fallbackError),
          summaryPromise,
        };
      }
    }
    return {
      fatalError: toErrorMessage(error, fallbackError),
      summaryPromise,
    };
  }
}
