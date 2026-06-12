export const DEPLOY_RECOVERY_STORAGE_KEY = "factoryops_deploy_recovery_last_attempt_at";
export const DEPLOY_RECOVERY_COOLDOWN_MS = 30_000;

const RECOVERABLE_DEPLOY_ERROR_PATTERNS = [
  /chunkloaderror/i,
  /loading chunk [^ ]+ failed/i,
  /failed to fetch dynamically imported module/i,
  /failed to fetch rsc payload/i,
  /server action/i,
  /actionqueuecontext/i,
  /failed to fetch server response/i,
  /failed to fetch update manifest/i,
];

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  return value as Record<string, unknown>;
}

export function extractDeployRecoveryMessage(reason: unknown): string {
  if (typeof reason === "string") {
    return reason;
  }

  if (reason instanceof Error) {
    return reason.message || String(reason);
  }

  const record = asRecord(reason);
  if (!record) {
    return "";
  }

  const message = record.message;
  if (typeof message === "string") {
    return message;
  }

  const reasonValue = record.reason;
  if (typeof reasonValue === "string") {
    return reasonValue;
  }
  if (reasonValue instanceof Error) {
    return reasonValue.message || String(reasonValue);
  }

  return "";
}

export function isRecoverableDeployError(reason: unknown): boolean {
  const message = extractDeployRecoveryMessage(reason).trim();
  if (!message) {
    return false;
  }
  return RECOVERABLE_DEPLOY_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

export function shouldAttemptAutomaticDeployRecovery(
  lastAttemptAt: number | null,
  now: number,
  cooldownMs = DEPLOY_RECOVERY_COOLDOWN_MS,
): boolean {
  if (lastAttemptAt === null || !Number.isFinite(lastAttemptAt)) {
    return true;
  }
  return (now - lastAttemptAt) > cooldownMs;
}
