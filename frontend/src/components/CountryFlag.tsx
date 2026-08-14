import { countryName } from "../lib/countries";

type Props = {
  code: string | null | undefined;
  /** Flag height in px (width ~ 4:3). */
  size?: number;
  className?: string;
  title?: string;
};

/**
 * Image flag (not emoji) — Windows often renders ISO regional indicators as plain "BG"/"DE".
 */
export function CountryFlag({ code, size = 14, className = "", title }: Props) {
  const c = (code ?? "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(c)) return null;

  const h = size;
  const w = Math.round((size * 4) / 3);
  const name = countryName(c);
  const tip = title ?? `${name} (${c})`;

  return (
    <img
      src={`https://flagcdn.com/h20/${c.toLowerCase()}.png`}
      srcSet={`https://flagcdn.com/h40/${c.toLowerCase()}.png 2x`}
      alt={c}
      width={w}
      height={h}
      title={tip}
      aria-label={name}
      loading="lazy"
      decoding="async"
      className={`inline-block shrink-0 rounded-[2px] object-cover ring-1 ring-[var(--border)] ${className}`}
      style={{ width: w, height: h }}
    />
  );
}
