// Follow-the-stream auto-scroll for the chat transcript.
//
// The old ChatPanel effect read scrollHeight/clientHeight on every message
// flush — a forced layout per animation frame while tokens streamed. The
// scroller instead keeps a near-bottom flag updated by a PASSIVE scroll
// listener (no forced layout, never blocks the scroll thread); the per-flush
// hook only writes scrollTop when the user is still at the bottom.

export type ScrollElement = {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
  addEventListener(
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: AddEventListenerOptions,
  ): void;
  removeEventListener(
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: EventListenerOptions,
  ): void;
};

export type AutoScroller = {
  attach: (el: ScrollElement) => void;
  detach: () => void;
  /** Call after new content lands; scrolls only when the user is near the bottom. */
  onContentChanged: () => void;
};

export function createAutoScroller(threshold = 120): AutoScroller {
  let el: ScrollElement | null = null;
  let nearBottom = true;
  const onScroll = () => {
    if (!el) return;
    nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  };
  return {
    attach(target) {
      el = target;
      nearBottom = true; // a fresh attach follows the stream by default
      target.addEventListener("scroll", onScroll, { passive: true });
    },
    detach() {
      if (el) el.removeEventListener("scroll", onScroll);
      el = null;
    },
    onContentChanged() {
      if (el && nearBottom) el.scrollTop = el.scrollHeight;
    },
  };
}