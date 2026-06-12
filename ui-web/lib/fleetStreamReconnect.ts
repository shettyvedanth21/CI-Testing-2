export type FleetStreamConnectorDeps<TEvent extends { id?: string }> = {
  streamFetch: (input: string, init?: RequestInit) => Promise<Response>;
  refreshAccessToken: () => Promise<string | null>;
  clearSession: () => void;
  scheduleReconnect: (callback: () => void, delayMs: number) => unknown;
  clearScheduledReconnect: (handle: unknown) => void;
  createAbortController: () => AbortController;
  createTextDecoder: () => TextDecoder;
  parseEventChunk: (chunk: string) => TEvent | null;
};

export type FleetStreamParams<TEvent extends { id?: string }> = {
  streamUrl: string;
  onEvent: (payload: TEvent) => void;
  onError?: (error: unknown, retryCount: number) => void;
  onOpen?: () => void;
  onReconnectStart?: (reason: "stream_closed" | "stream_error", retryCount: number) => void;
  inactivityTimeoutMs?: number;
};

export function createFleetStreamConnector<TEvent extends { id?: string }>(
  deps: FleetStreamConnectorDeps<TEvent>,
): (params: FleetStreamParams<TEvent>) => () => void {
  return (params) => {
    let active = true;
    let retryCount = 0;
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    let controller: AbortController | null = null;
    let reconnectTimer: unknown = null;
    let inactivityTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAfterAbort = false;

    const clearInactivityTimer = () => {
      if (inactivityTimer !== null) {
        clearTimeout(inactivityTimer);
        inactivityTimer = null;
      }
    };

    const connect = async () => {
      controller = deps.createAbortController();
      let buffer = "";
      let reconnectReason: "stream_closed" | "stream_error" = "stream_closed";
      const scheduleInactivityTimer = () => {
        clearInactivityTimer();
        const timeoutMs = Math.max(1000, params.inactivityTimeoutMs ?? 7500);
        inactivityTimer = setTimeout(() => {
          if (!active || controller?.signal.aborted) {
            return;
          }
          reconnectReason = "stream_error";
          reconnectAfterAbort = true;
          params.onError?.(new Error("Fleet stream became idle"), retryCount + 1);
          controller?.abort();
        }, timeoutMs);
      };

      try {
        const response = await deps.streamFetch(params.streamUrl, {
          cache: "no-store",
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        if (!response.body) {
          throw new Error("Fleet stream response body missing");
        }

        retryCount = 0;
        params.onOpen?.();
        scheduleInactivityTimer();
        reader = response.body.getReader();
        const decoder = deps.createTextDecoder();

        while (active) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }
          buffer += decoder.decode(value, { stream: true });
          scheduleInactivityTimer();

          let separatorIndex = buffer.indexOf("\n\n");
          while (separatorIndex !== -1) {
            const eventChunk = buffer.slice(0, separatorIndex).trim();
            buffer = buffer.slice(separatorIndex + 2);
            if (eventChunk) {
              const payload = deps.parseEventChunk(eventChunk);
              if (payload) {
                params.onEvent(payload);
              }
            }
            separatorIndex = buffer.indexOf("\n\n");
          }
        }
      } catch (error) {
        if (!active || (controller?.signal.aborted && !reconnectAfterAbort)) {
          return;
        }
        reconnectReason = "stream_error";
        if (!reconnectAfterAbort) {
          params.onError?.(error, retryCount + 1);
        }
      } finally {
        clearInactivityTimer();
        try {
          await reader?.cancel();
        } catch {
          // Reader may already be closed during reconnect/cleanup.
        }
        reader = null;
        controller = null;
        reconnectAfterAbort = false;
      }

      if (!active) {
        return;
      }

      retryCount += 1;
      if (retryCount > 5) {
        return;
      }

      params.onReconnectStart?.(reconnectReason, retryCount);
      const reconnectDelayMs = retryCount === 1 ? 500 : 3000;
      reconnectTimer = deps.scheduleReconnect(() => {
        if (!active) {
          return;
        }

        void (async () => {
          const refreshedToken = await deps.refreshAccessToken();
          if (!refreshedToken) {
            active = false;
            deps.clearSession();
            return;
          }
          if (active) {
            void connect();
          }
        })();
      }, reconnectDelayMs);
    };

    void connect();

    return () => {
      active = false;
      if (reconnectTimer !== null) {
        deps.clearScheduledReconnect(reconnectTimer);
      }
      clearInactivityTimer();
      controller?.abort();
      void reader?.cancel().catch(() => undefined);
    };
  };
}
