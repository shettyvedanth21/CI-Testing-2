export async function readResponseError(res: Response): Promise<string> {
  const fallback = `HTTP ${res.status}`;

  let text: string;
  try {
    text = await res.text();
  } catch {
    return fallback;
  }

  if (!text) {
    return fallback;
  }

  try {
    const body = JSON.parse(text);
    if (typeof body?.message === "string") return body.message;
    if (typeof body?.error?.message === "string") return body.error.message;
    if (typeof body?.detail?.error?.message === "string") return body.detail.error.message;
    if (typeof body?.detail?.message === "string") return body.detail.message;
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // Non-JSON body; fall through to raw text.
  }

  return text || fallback;
}
