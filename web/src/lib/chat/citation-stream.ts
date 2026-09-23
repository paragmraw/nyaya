// Incremental citation-marker stripping for the streaming path.
//
// The old streaming flush re-ran stripCitationMarkers on the ENTIRE
// accumulated text every animation frame — quadratic cumulative work over a
// long answer. StreamingCitationStripper scans only the new delta: it converts
// complete [[act: X, ref: Y]] markers to [X · Y] chips as they close, and
// holds back a tail that could still grow into a marker (a trailing "[" or an
// incomplete "[[act:…"). flush() emits whatever is still held, literally —
// a tail that never completed is plain text, matching stripCitationMarkers.
//
// The holdback logic mirrors the CITE_RE bounds (act ≤ 512, ref ≤ 2048, ≤ 16
// whitespace chars between "," and "ref:"): a held tail longer than a legal
// marker can ever be is emitted literally, so nothing can stall the stream.

// Keep in sync with the bounds in chat/citations.ts (imported there; the
// duplicated literals below avoid a circular import — both modules are leaf
// pure modules re-exported by the facade).
const MARKER_PREFIX = "[[act:";

function mightBecomeMarker(s: string): boolean {
  // `s` starts with "[[". True when s is a prefix of some string matching
  // /\[\[act:([^,\]]{1,512}),\s{0,16}ref:([^\]]{1,2048})\]\]/.
  if (MARKER_PREFIX.startsWith(s)) return true; // "[", "[[", "[[a", …
  if (!s.startsWith(MARKER_PREFIX)) return false;
  const rest = s.slice(MARKER_PREFIX.length);
  const comma = rest.indexOf(",");
  const bracket = rest.indexOf("]");
  if (bracket !== -1 && (comma === -1 || bracket < comma)) return false; // "]" before "," — dead
  if (comma === -1) return rest.length <= 512; // act part still growing (≥1 char comes later)
  const act = rest.slice(0, comma);
  if (act.length < 1 || act.length > 512) return false;
  const after = rest.slice(comma + 1);
  const refIdx = after.indexOf("ref:");
  if (refIdx !== -1) {
    const ws = after.slice(0, refIdx);
    if (ws.length > 16 || /\S/.test(ws)) return false;
    let ref = after.slice(refIdx + 4);
    // A trailing "]" may be the marker's OWN first closing bracket with the
    // second one still in flight (a feed boundary can fall between the two
    // "]"s of "]]") — that is holdable. Any "]" BEFORE the trailing one is a
    // real bracket inside the ref text: dead.
    if (ref.endsWith("]")) {
      const body = ref.slice(0, -1);
      if (body.includes("]")) return false;
      // With the closing bracket accounted for, the ref body must still be
      // within bounds and non-empty ([^\]]{1,2048}).
      return body.length >= 1 && body.length <= 2048;
    }
    return ref.length <= 2048; // ref may still be empty — holdable
  }
  // No "ref:" yet: allow whitespace run (≤16) plus a prefix of "ref:".
  if (after.length > 16 + 3) return false;
  const wsRun = /^[ \t\n\r\f\v]*/.exec(after)![0];
  const tail = after.slice(wsRun.length);
  return tail === "" || ["r", "re", "ref"].includes(tail);
}

export class StreamingCitationStripper {
  private buf = "";   // raw text not yet transformed
  private pos = 0;    // raw length already emitted (or held-and-emitted)

  /** Append a delta and return the display text now safe to show. */
  feed(delta: string): string {
    this.buf += delta;
    return this._drain(false);
  }

  /** Emit everything still held; incomplete markers render literally. */
  flush(): string {
    return this._drain(true);
  }

  /** Rebase onto corrected/authoritative text: nothing further to emit. */
  restart(text: string): void {
    this.buf = text;
    this.pos = text.length;
  }

  private _drain(final: boolean): string {
    let out = "";
    while (this.pos < this.buf.length) {
      const open = this.buf.indexOf("[[", this.pos);
      if (open === -1) {
        // No marker start; hold a trailing single "[" (could pair with the
        // next delta) unless we're flushing.
        let end = this.buf.length;
        if (!final && this.buf.endsWith("[")) end -= 1;
        if (end > this.pos) {
          out += this.buf.slice(this.pos, end);
          this.pos = end;
        }
        return out;
      }
      if (open > this.pos) {
        out += this.buf.slice(this.pos, open);
        this.pos = open;
      }
      // this.buf.startsWith("[[", this.pos)
      const candidate = this.buf.slice(this.pos);
      const m = /^\[\[act:([^,\]]{1,512}),\s{0,16}ref:([^\]]{1,2048})\]\]/.exec(candidate);
      if (m) {
        out += `[${m[1].trim()} · ${m[2].trim()}]`;
        this.pos += m[0].length;
        continue;
      }
      if (!final && mightBecomeMarker(candidate)) {
        return out; // hold; more text needed
      }
      // Not a marker (or flushing): emit the leading "[" literally and rescan.
      out += this.buf[this.pos];
      this.pos += 1;
    }
    return out;
  }
}