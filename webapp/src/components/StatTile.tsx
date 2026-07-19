export function StatTile({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div
      className="rounded-lg border px-4 py-3 transition-shadow hover:shadow-sm"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        {label}
      </div>
      <div
        className="mt-1.5 text-2xl font-semibold"
        style={{ color: valueColor ?? "var(--text-primary)" }}
      >
        {value}
      </div>
    </div>
  );
}
