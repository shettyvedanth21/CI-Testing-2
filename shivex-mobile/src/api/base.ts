import { mobileFetch } from "./authApi";
import { API_CONFIG } from "../constants/api";

export function getBaseHost() {
  return API_CONFIG.DEVICE_SERVICE.replace(/:\d+$/, "");
}

export function buildServiceUrl(port: number, path: string) {
  return `${getBaseHost()}:${port}${path}`;
}

export function rewriteLocalhostUrl(url: string) {
  try {
    const parsed = new URL(url);

    if (parsed.hostname !== "localhost" && parsed.hostname !== "127.0.0.1") {
      return url;
    }

    const base = new URL(getBaseHost());
    parsed.protocol = base.protocol;
    parsed.hostname = base.hostname;
    return parsed.toString();
  } catch {
    return url;
  }
}

export async function readJson<T>(input: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await mobileFetch(input, init);

    if (!response.ok) {
      console.error("[shivex api]", input, response.status);
      return null;
    }

    return (await response.json()) as T;
  } catch (error) {
    console.error("[shivex api]", error);
    return null;
  }
}
