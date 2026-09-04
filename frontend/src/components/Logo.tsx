/**
 * Remna Agent mark — "Пульс".
 *
 * The shape is the cf_204 sparkline the dashboard already draws on every tile:
 * a flat trace, one spike, and a bright dot on the latest sample. It is drawn
 * without a background plate so it can sit on the sidebar, a dialog, or a
 * button; the favicon file carries its own plate because a browser tab has no
 * guaranteed ground.
 */
export function Logo({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      role="img"
      aria-label="Remna Agent"
    >
      <polyline
        points="3.2,14.5 7.6,14.5 10.4,6.2 13.4,18.6 15.7,12.2 18,12.2"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="19.4" cy="12.2" r="2.2" fill="var(--accent)" />
    </svg>
  );
}
