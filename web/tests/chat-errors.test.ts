// Unit tests for humanizeError (chat.ts): maps the Task-3 unified error
// contract's machine codes ({message, detail, rid}) to copy a user can act on.
// Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { humanizeError } from "../src/lib/chat";

test("abort/cancel class is humanized", () => {
  assert.equal(humanizeError("cancelled"), "Response cancelled.");
  assert.equal(humanizeError("aborted"), "Response cancelled.");
  assert.match(humanizeError("interrupted"), /interrupted/);
});

test("network failure class is humanized", () => {
  assert.equal(
    humanizeError("Failed to fetch"),
    "Couldn't reach the Nyaya service. Check your connection and try again.",
  );
  assert.match(humanizeError("NetworkError when attempting to fetch resource."), /Check your connection/);
});

test("timeout class is humanized with actionable copy", () => {
  assert.equal(
    humanizeError("stream_timeout"),
    "Nyaya took too long to respond. Try resending your question.",
  );
});

test("server error codes surface the detail field (Task-3 contract)", () => {
  assert.equal(
    humanizeError("agent_unavailable", "model provider returned 502"),
    "Nyaya's assistant is temporarily unavailable. Please retry in a moment. (model provider returned 502)",
  );
  assert.match(humanizeError("503 Service Unavailable"), /temporarily unavailable/);
});

test("rate limiting is humanized distinctly", () => {
  assert.match(humanizeError("429 Too Many Requests"), /handling a lot of requests/);
  assert.match(humanizeError("rate_limit"), /handling a lot of requests/);
  assert.notEqual(humanizeError("429 Too Many Requests"), humanizeError("agent_error"));
});

test("opaque codes fall back to generic copy plus detail", () => {
  assert.equal(
    humanizeError("upstream_socket_error"),
    "Something went wrong while getting an answer. (upstream_socket_error)",
  );
});

test("human-phrased server messages pass through, with detail appended", () => {
  assert.equal(
    humanizeError("The verification layer timed out", "rid not yet assigned"),
    "The verification layer timed out (rid not yet assigned)",
  );
});

test("detail-less machine codes render without an empty parenthetical", () => {
  assert.equal(humanizeError("agent_error"), "Nyaya's assistant is temporarily unavailable. Please retry in a moment.");
});