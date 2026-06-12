import test from "node:test";
import assert from "node:assert/strict";

import { readResponseError } from "../../lib/responseError.ts";

test("readResponseError returns structured message from JSON body", async () => {
  const response = new Response(
    JSON.stringify({ detail: { message: "Rule service unavailable" } }),
    {
      status: 503,
      headers: { "Content-Type": "application/json" },
    },
  );

  const message = await readResponseError(response);

  assert.equal(message, "Rule service unavailable");
});

test("readResponseError returns nested backend error message from detail.error", async () => {
  const response = new Response(
    JSON.stringify({
      detail: {
        error: {
          code: "DEVICE_ID_ALLOCATION_FAILED",
          message: "Unable to allocate a unique device ID",
        },
      },
    }),
    {
      status: 503,
      headers: { "Content-Type": "application/json" },
    },
  );

  const message = await readResponseError(response);

  assert.equal(message, "Unable to allocate a unique device ID");
});

test("readResponseError returns plain text body without re-reading the stream", async () => {
  const response = new Response("Internal Server Error", {
    status: 500,
    headers: { "Content-Type": "text/plain" },
  });

  const message = await readResponseError(response);

  assert.equal(message, "Internal Server Error");
});
