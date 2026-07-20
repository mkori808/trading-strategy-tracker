// Semicircle speedometer gauge -- zone bands use ONLY the status color
// tokens (good/warning/critical), never the orange brand accent, so a
// gauge reading never gets confused with the app's own button/nav color.
// See CLAUDE.md's dataviz guidance: sequential/status color, not a rainbow.

const VIEW_WIDTH = 200;
const VIEW_HEIGHT = 130;
const CENTER = { x: 100, y: 105 };
const RADIUS = 78;
const BAND_WIDTH = 16;

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
}

// Angle convention: 180deg = left (value 0), 0deg = right (value 100),
// sweeping over the top -- a standard semicircle speedometer.
function arcPath(r: number, startDeg: number, endDeg: number): string {
  const start = polarToCartesian(CENTER.x, CENTER.y, r, startDeg);
  const end = polarToCartesian(CENTER.x, CENTER.y, r, endDeg);
  const largeArc = Math.abs(startDeg - endDeg) > 180 ? 1 : 0;
  // SVG sweep-flag=0 draws the arc going clockwise in this y-down system
  // for a decreasing angle, which is what we want sweeping left-to-right.
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 0 ${end.x} ${end.y}`;
}

function valueToAngle(value: number): number {
  const clamped = Math.max(0, Math.min(100, value));
  return 180 - (clamped / 100) * 180;
}

export function GaugeDial({
  value,
  label,
  invert = false,
  size = "full",
}: {
  value: number | null;
  label: string;
  invert?: boolean;
  size?: "full" | "mini";
}) {
  const zones = invert
    ? [
        { from: 0, to: 33.3, color: "var(--status-good)" },
        { from: 33.3, to: 66.6, color: "var(--status-warning)" },
        { from: 66.6, to: 100, color: "var(--status-critical)" },
      ]
    : [
        { from: 0, to: 33.3, color: "var(--status-critical)" },
        { from: 33.3, to: 66.6, color: "var(--status-warning)" },
        { from: 66.6, to: 100, color: "var(--status-good)" },
      ];

  const numberColor =
    value === null
      ? "var(--text-muted)"
      : (value >= 66.6) !== invert
        ? "var(--status-good)"
        : (value <= 33.3) !== invert
          ? "var(--status-critical)"
          : "var(--status-warning)";

  const needleAngle = value === null ? 90 : valueToAngle(value);
  const needleTip = polarToCartesian(CENTER.x, CENTER.y, RADIUS - BAND_WIDTH / 2 - 4, needleAngle);

  const width = size === "mini" ? 120 : 220;

  return (
    <div className="flex flex-col items-center" style={{ width }}>
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        width={width}
        height={(width * VIEW_HEIGHT) / VIEW_WIDTH}
        role="img"
        aria-label={`${label}: ${value === null ? "no data" : value.toFixed(0)}`}
      >
        {zones.map((z) => (
          <path
            key={z.from}
            d={arcPath(RADIUS, valueToAngle(z.from), valueToAngle(z.to))}
            fill="none"
            stroke={z.color}
            strokeWidth={BAND_WIDTH}
            opacity={value === null ? 0.35 : 1}
          />
        ))}
        {value !== null && (
          <>
            <line
              x1={CENTER.x}
              y1={CENTER.y}
              x2={needleTip.x}
              y2={needleTip.y}
              stroke="var(--text-primary)"
              strokeWidth={2.5}
              strokeLinecap="round"
            />
            <circle cx={CENTER.x} cy={CENTER.y} r={5} fill="var(--text-primary)" />
          </>
        )}
        <text
          x={CENTER.x}
          y={CENTER.y - 2}
          textAnchor="middle"
          fontSize={size === "mini" ? 22 : 32}
          fontWeight={700}
          fill={numberColor}
        >
          {value === null ? "—" : value.toFixed(0)}
        </text>
      </svg>
      <div
        className="-mt-1 text-center text-xs font-medium"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
      </div>
    </div>
  );
}
