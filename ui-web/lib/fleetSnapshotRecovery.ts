export type VersionedFleetDevice = {
  id: string;
  version: number;
};

type FleetSnapshotRecoveryOptions<TDevice extends VersionedFleetDevice> = {
  fetchSnapshot: () => Promise<TDevice[]>;
  applySnapshot?: (devices: TDevice[]) => void | Promise<void>;
  timeoutMs?: number;
  pollIntervalMs?: number;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
};

function snapshotSignature<TDevice extends VersionedFleetDevice>(devices: TDevice[]): string {
  return JSON.stringify(
    devices.map((device) => [device.id, Number.isFinite(device.version) ? device.version : 0]),
  );
}

export async function recoverStableFleetSnapshot<TDevice extends VersionedFleetDevice>(
  options: FleetSnapshotRecoveryOptions<TDevice>,
): Promise<TDevice[]> {
  const {
    fetchSnapshot,
    applySnapshot,
    timeoutMs = 12_000,
    pollIntervalMs = 1_000,
    now = () => Date.now(),
    sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms)),
  } = options;

  const deadline = now() + timeoutMs;
  let previousSignature: string | null = null;
  let latestDevices: TDevice[] = [];

  while (true) {
    const devices = await fetchSnapshot();
    latestDevices = devices;
    await applySnapshot?.(devices);

    const currentSignature = snapshotSignature(devices);
    if (devices.length > 0 && previousSignature === currentSignature) {
      return devices;
    }

    if (now() >= deadline) {
      return latestDevices;
    }

    previousSignature = currentSignature;
    await sleep(pollIntervalMs);
  }
}
