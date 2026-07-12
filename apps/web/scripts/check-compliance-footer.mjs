#!/usr/bin/env node
/**
 * Compliance-footer render check (CLAUDE.md §2.5 — CI gate).
 *
 * Discovers every public route from app/ * * /page.tsx, fetches each from a
 * running server (BASE_URL, default http://localhost:3000), and asserts the
 * rendered HTML contains the compliance marker. New pages are covered
 * automatically; removing the footer from any route fails CI.
 *
 * Usage: node scripts/check-compliance-footer.mjs   (server must be running)
 */
import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const MARKER = 'data-compliance="compliance-footer"';
const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";
const APP_DIR = new URL("../app", import.meta.url).pathname;

function findPages(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...findPages(p));
    else if (name === "page.tsx" || name === "page.jsx") out.push(p);
  }
  return out;
}

function toRoute(pageFile) {
  const rel = relative(APP_DIR, pageFile).split(sep).slice(0, -1);
  const parts = rel.filter((seg) => !(seg.startsWith("(") && seg.endsWith(")")));
  if (parts.some((seg) => seg.startsWith("["))) return null; // dynamic: needs params, checked via its static siblings' shared layout
  return "/" + parts.join("/");
}

const routes = [...new Set(findPages(APP_DIR).map(toRoute).filter(Boolean))];
if (routes.length === 0) {
  console.error("compliance-footer: no routes discovered under app/ — check APP_DIR");
  process.exit(1);
}

let failed = false;
for (const route of routes) {
  const url = `${BASE_URL}${route}`;
  let res;
  try {
    res = await fetch(url);
  } catch (err) {
    console.error(`FAIL ${route}: fetch error ${err}`);
    failed = true;
    continue;
  }
  const html = await res.text();
  if (!res.ok) {
    console.error(`FAIL ${route}: HTTP ${res.status}`);
    failed = true;
  } else if (!html.includes(MARKER)) {
    console.error(`FAIL ${route}: compliance footer marker missing`);
    failed = true;
  } else {
    console.log(`ok   ${route}`);
  }
}

if (failed) {
  console.error("compliance-footer: FAILED — §2.5 requires the footer on every public surface");
  process.exit(1);
}
console.log(`compliance-footer: all ${routes.length} routes carry the marker`);
