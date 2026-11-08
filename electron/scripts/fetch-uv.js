// ビルド前: uv（Python と依存パッケージを導入するツール。MIT / Apache-2.0）を GitHub の公式リリースから取得して vendor/uv に置く。
// SHA-256 を公式の .sha256 と照合する。ライセンス文も一緒に置く（exe に同梱するため）。
"use strict";
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const VERSION = "0.13.7";
const ASSET = "uv-x86_64-pc-windows-msvc.zip";
const BASE = `https://github.com/astral-sh/uv/releases/download/${VERSION}/`;
const RAW = `https://raw.githubusercontent.com/astral-sh/uv/${VERSION}/`;
const OUT = path.join(__dirname, "..", "vendor", "uv");

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
}

(async () => {
  const stamp = path.join(OUT, "VERSION");
  if (fs.existsSync(stamp) && fs.readFileSync(stamp, "utf8").trim() === VERSION && fs.existsSync(path.join(OUT, "uv.exe"))) {
    console.log(`uv ${VERSION}: already fetched`);
    return;
  }
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
  console.log(`uv ${VERSION}: downloading ${ASSET}`);
  const zip = await get(BASE + ASSET);
  const want = (await get(BASE + ASSET + ".sha256")).toString("utf8").trim().split(/\s+/)[0].toLowerCase();
  const got = crypto.createHash("sha256").update(zip).digest("hex");
  if (got !== want) throw new Error(`SHA-256 mismatch: ${got} != ${want}`);
  const zipPath = path.join(OUT, ASSET);
  fs.writeFileSync(zipPath, zip);
  execFileSync("powershell", ["-NoProfile", "-Command", `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${OUT}' -Force`]);
  fs.rmSync(zipPath);
  for (const f of fs.readdirSync(OUT)) if (!["uv.exe"].includes(f)) fs.rmSync(path.join(OUT, f), { recursive: true, force: true });
  for (const lic of ["LICENSE-MIT", "LICENSE-APACHE"]) fs.writeFileSync(path.join(OUT, lic), await get(RAW + lic));
  fs.writeFileSync(stamp, VERSION + "\n");
  console.log(`uv ${VERSION}: ok (${OUT})`);
})().catch((e) => { console.error(e); process.exit(1); });
