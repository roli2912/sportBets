import { Pool } from "pg";

declare global {
  // eslint-disable-next-line no-var
  var pgPool: Pool | undefined;
}

export const pool: Pool =
  global.pgPool ??
  new Pool({
    connectionString:
      process.env.DATABASE_URL ?? "postgresql://localhost:5432/sportbets_dev",
    max: 5,
  });

if (!global.pgPool) global.pgPool = pool;

export function fmtUtc(d: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d) + " UTC";
}

export function pct(x: number | string, digits = 1): string {
  const n = Number(x);
  return `${n >= 0 ? "+" : ""}${(n * 100).toFixed(digits)}%`;
}
