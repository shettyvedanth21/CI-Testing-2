export function isActivityHistoryAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function isTransientActivityHistoryError(error: unknown): boolean {
  if (isActivityHistoryAbortError(error)) {
    return true;
  }

  if (!(error instanceof Error)) {
    return false;
  }

  const message = error.message.toLowerCase();
  return (
    message.includes("failed to fetch")
    || message.includes("fetch failed")
    || message.includes("networkerror")
    || message.includes("network request failed")
    || message.includes("load failed")
    || message.includes("err_failed")
  );
}

export function getActivityHistoryDegradedMessage(hasCachedEvents: boolean): string {
  return hasCachedEvents
    ? "Activity history may be temporarily out of date. Showing the last loaded events."
    : "Activity history is temporarily unavailable. The rest of the machine page is still live.";
}
