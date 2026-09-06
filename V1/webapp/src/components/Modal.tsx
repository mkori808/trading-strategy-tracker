import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Selector for what counts as focusable when the focus trap wraps. */
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), details > summary, [tabindex]:not([tabindex="-1"])';

export type ModalSize = "md" | "lg" | "xl";

const MAX_WIDTH: Record<ModalSize, string> = {
  md: "36rem",
  lg: "56rem",
  xl: "76rem",
};

/** The dashboard's detail surface: every card opens one of these rather
 * than navigating away, so the user never loses the overview they were
 * reading. Built as a real dialog, not a floating div --
 *
 *  - portalled to <body>, so a card's `overflow` or stacking context can't
 *    clip it,
 *  - Escape and backdrop-click close it,
 *  - focus moves in on open and returns to the trigger on close, and Tab
 *    cycles inside instead of wandering into the page behind,
 *  - background scroll is locked, with the scrollbar's width replaced as
 *    padding so the page underneath doesn't visibly jump on open,
 *  - `aria-modal` + `aria-labelledby` so a screen reader announces it as a
 *    dialog with a name.
 *
 * The body scrolls, never the whole dialog, so the title and close button
 * stay pinned for long content (the screener's 94 rows, the run log). */
export function Modal({
  open,
  onClose,
  title,
  subtitle,
  size = "lg",
  headerAction,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: ReactNode;
  size?: ModalSize;
  /** Rendered next to the close button -- a Refresh button, a timestamp. */
  headerAction?: ReactNode;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useRef(`modal-title-${Math.random().toString(36).slice(2)}`).current;

  // Held in a ref, and NOT in the effect's dependency list. Callers pass an
  // inline `() => setPopup(null)`, whose identity changes on every render of
  // the page behind the dialog -- and that page re-renders often (the
  // trading view polls every 30s, and each poll updates shared state). With
  // `onClose` in the deps, every one of those renders tore the whole effect
  // down and re-ran it: the scroll lock released and re-applied, the
  // keydown listener detached and reattached, and focus yanked back out to
  // the trigger and then re-forced onto the panel -- mid-scroll,
  // mid-keystroke, every 30 seconds. Keep this list `[open]`.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;

    restoreFocusRef.current = document.activeElement as HTMLElement | null;

    // Replace the scrollbar's width with padding: locking overflow removes
    // the scrollbar, which would otherwise widen the page by ~15px behind
    // the backdrop and make the whole layout twitch on every open.
    const { body } = document;
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    const prevOverflow = body.style.overflow;
    const prevPadding = body.style.paddingRight;
    body.style.overflow = "hidden";
    if (scrollbarWidth > 0) body.style.paddingRight = `${scrollbarWidth}px`;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);

    // Focus the panel itself rather than its first control: auto-focusing a
    // button makes it look pressed, and on the trading popups that button
    // could be "Activate kill switch".
    panelRef.current?.focus();

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      body.style.overflow = prevOverflow;
      body.style.paddingRight = prevPadding;
      restoreFocusRef.current?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-6"
      style={{ background: "rgba(0, 0, 0, 0.55)" }}
      onMouseDown={(e) => {
        // mousedown, not click: a click that STARTED inside the panel (a
        // drag-select across a table that ended on the backdrop) must not
        // close the dialog.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="my-auto flex max-h-[90vh] w-full flex-col rounded-xl border shadow-2xl outline-none"
        style={{
          maxWidth: MAX_WIDTH[size],
          borderColor: "var(--border)",
          background: "var(--page)",
        }}
      >
        <div
          className="flex shrink-0 items-start justify-between gap-4 border-b px-5 py-4"
          style={{ borderColor: "var(--border)" }}
        >
          <div className="min-w-0">
            <h2
              id={titleId}
              className="truncate text-base font-semibold"
              style={{ color: "var(--text-primary)" }}
            >
              {title}
            </h2>
            {subtitle && (
              <div className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
                {subtitle}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {headerAction}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-md border px-2 py-1 text-sm leading-none transition-colors"
              style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
            >
              ✕
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
