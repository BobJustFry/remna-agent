import { memo, useMemo, useState } from "react";

type Props = {
  name: string;
  faviconData?: string | null;
  size?: number;
  className?: string;
};

function letterColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;
  return `hsl(${hue} 45% 42%)`;
}

export const HostingLogo = memo(function HostingLogo({
  name,
  faviconData,
  size = 18,
  className = "",
}: Props) {
  const [imgFailed, setImgFailed] = useState(false);
  const letter = (name.trim().charAt(0) || "?").toUpperCase();
  const bg = useMemo(() => letterColor(name || "?"), [name]);
  const showImg = Boolean(faviconData) && !imgFailed;

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-md ${className}`}
      style={{ width: size, height: size, background: showImg ? "transparent" : bg }}
      title={name}
    >
      {showImg ? (
        <img
          src={faviconData!}
          alt=""
          width={size}
          height={size}
          className="h-full w-full object-contain"
          onError={() => setImgFailed(true)}
        />
      ) : (
        <span
          className="font-semibold leading-none text-white"
          style={{ fontSize: Math.max(10, Math.round(size * 0.55)) }}
        >
          {letter}
        </span>
      )}
    </span>
  );
});
