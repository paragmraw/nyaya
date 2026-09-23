// SSE block parsing and rAF token batching. Verbatim moves from the old
// lib/chat.ts.

// Parse one SSE block (lines separated by \n, blocks by \n\n) into its event
// name and data payload. Per the SSE spec: multiple `data:` lines are joined
// with a literal newline, and only the single leading space after `data:` is
// stripped (`data: x` → `x`, but `data:  x` keeps the second space). The
// backend's encoder emits single-line JSON `data:` payloads, so this is a
// hardening for well-formed multi-line events, not a behavior change.
export function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) {
      const d = line.slice(5);
      dataLines.push(d.startsWith(" ") ? d.slice(1) : d);
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

// ─── rAF token batching ───────────────────────────────────────────
// Streamed `token` SSE events can arrive far more often than once per frame;
// updating React state per token re-renders the markdown bubble per token
// (O(n²) cumulative work over a long answer). createFrameBatcher coalesces
// any number of schedule() calls between two frames into a single flush().
// Exported for unit testing (tests/raf-batch.test.ts); raf/caf are injectable
// so the batching contract is testable in node (no requestAnimationFrame there).
export type FrameBatcher = { schedule: () => void; cancel: () => void };

export function createFrameBatcher(
  flush: () => void,
  raf: (cb: () => void) => number = (cb) => requestAnimationFrame(cb),
  caf: (handle: number) => void = (h) => cancelAnimationFrame(h),
): FrameBatcher {
  let handle: number | null = null;
  return {
    schedule() {
      if (handle !== null) return; // a frame is already pending
      handle = raf(() => {
        handle = null;
        flush();
      });
    },
    cancel() {
      if (handle !== null) {
        caf(handle);
        handle = null;
      }
    },
  };
}