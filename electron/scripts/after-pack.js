// electron-builder の afterPack: ffmpeg.dll を、H.264 / AAC のデコーダーを含まない版に差し替える。
// （ffmpeg.dll 単体の話。Electron 本体は Windows・GPU のデコード機能で H.264 を再生できる。2026-11-08 に実測）
// このアプリは画面で動画を再生しない（サムネイルは Python 側で静止画にしている）ので、差し替えても影響はない。
// 差し替え用の ffmpeg.dll は Electron の公式リリースにある ffmpeg-v<version>-win32-x64.zip（SHASUMS256.txt と照合）。
"use strict";
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
}

exports.default = async function afterPack(context) {
  const version = context.packager.config.electronVersion || require("electron/package.json").version;
  const name = `ffmpeg-v${version}-win32-x64.zip`;
  const base = `https://github.com/electron/electron/releases/download/v${version}/`;
  const cacheDir = path.join(__dirname, "..", "vendor", "ffmpeg");
  const zipPath = path.join(cacheDir, name);
  if (!fs.existsSync(zipPath)) {
    fs.mkdirSync(cacheDir, { recursive: true });
    console.log(`  • ffmpeg (no proprietary codecs): downloading ${name}`);
    const zip = await get(base + name);
    const sums = (await get(base + "SHASUMS256.txt")).toString("utf8");
    const line = sums.split(/\r?\n/).find((l) => l.trim().endsWith("*" + name) || l.trim().endsWith(" " + name));
    if (!line) throw new Error(`SHASUMS256.txt に ${name} がありません`);
    const want = line.trim().split(/\s+/)[0].toLowerCase();
    const got = crypto.createHash("sha256").update(zip).digest("hex");
    if (got !== want) throw new Error(`SHA-256 mismatch for ${name}`);
    fs.writeFileSync(zipPath, zip);
  }
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "vrm-ffmpeg-"));
  execFileSync("powershell", ["-NoProfile", "-Command", `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${tmp}' -Force`]);
  const dll = path.join(tmp, "ffmpeg.dll");
  if (!fs.existsSync(dll)) throw new Error(`${name} に ffmpeg.dll がありません`);
  fs.copyFileSync(dll, path.join(context.appOutDir, "ffmpeg.dll"));
  fs.rmSync(tmp, { recursive: true, force: true });
  console.log("  • ffmpeg.dll replaced (no H.264 / AAC decoders)");
  await bundleFfmpegSource(context, version, cacheDir);
};

// ffmpeg.dll は LGPL-2.1。対応するソースコードを配布物に同梱する（LGPL-2.1 第 6 項）。
//  - Chromium の third_party/ffmpeg（Electron が使う Chromium の DEPS で固定されたリビジョン）
//  - Electron がそれに当てているパッチ（electron/electron の patches/ffmpeg）
async function bundleFfmpegSource(context, version, cacheDir) {
  const { execFileSync: run } = require("node:child_process");
  const electronExe = require("electron");
  const chrome = run(electronExe, ["-p", "process.versions.chrome"], { env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" } })
    .toString().trim();
  const deps = Buffer.from((await get(`https://chromium.googlesource.com/chromium/src/+/refs/tags/${chrome}/DEPS?format=TEXT`))
    .toString("utf8"), "base64").toString("utf8");
  const m = deps.match(/'ffmpeg_revision':\s*'([0-9a-f]{40})'/);
  if (!m) throw new Error(`Chromium ${chrome} の DEPS に ffmpeg_revision がありません`);
  const rev = m[1];
  const tarName = `chromium-ffmpeg-${rev}.tar.gz`;
  const tarCache = path.join(cacheDir, tarName);
  if (!fs.existsSync(tarCache)) {
    console.log(`  • ffmpeg source: downloading ${tarName}`);
    fs.writeFileSync(tarCache, await get(`https://chromium.googlesource.com/chromium/third_party/ffmpeg/+archive/${rev}.tar.gz`));
  }
  const out = path.join(context.appOutDir, "resources", "third_party_sources", "ffmpeg");
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(path.join(out, "electron-patches"), { recursive: true });
  fs.copyFileSync(tarCache, path.join(out, tarName));
  const raw = `https://raw.githubusercontent.com/electron/electron/v${version}/patches/ffmpeg/`;
  const list = (await get(raw + ".patches")).toString("utf8").split(/\r?\n/).filter(Boolean);
  fs.writeFileSync(path.join(out, "electron-patches", ".patches"), list.join("\n") + "\n");
  for (const f of list) fs.writeFileSync(path.join(out, "electron-patches", f), await get(raw + f));
  fs.writeFileSync(path.join(out, "README.txt"), [
    "Source code of ffmpeg.dll (FFmpeg, GNU Lesser General Public License v2.1)",
    "",
    `ffmpeg.dll in this application is the ffmpeg.dll published with the official Electron v${version} release`,
    `(https://github.com/electron/electron/releases/download/v${version}/ffmpeg-v${version}-win32-x64.zip).`,
    "It does not contain the H.264 and AAC decoders (the ffmpeg.dll bundled with Electron by default does).",
    "It is built from the following source code, included in this folder:",
    "",
    `  ${tarName}`,
    `    Chromium's FFmpeg (https://chromium.googlesource.com/chromium/third_party/ffmpeg) at revision ${rev},`,
    `    as pinned by Chromium ${chrome} (DEPS: ffmpeg_revision).`,
    "  electron-patches/",
    `    Patches applied by Electron v${version} (https://github.com/electron/electron/tree/v${version}/patches/ffmpeg),`,
    "    in the order listed in .patches.",
    "",
    "The archive includes Chromium's build configuration for FFmpeg (BUILD.gn, ffmpeg_generated.gni,",
    "ffmpeg_options.gni, chromium/config/Chromium/win/x64/). The rest of the build system used to compile it",
    `(GN build files and toolchain of Chromium ${chrome}) is available from the Chromium source code:`,
    `  https://chromium.googlesource.com/chromium/src/+/refs/tags/${chrome}`,
    `  (build instructions: https://chromium.googlesource.com/chromium/src/+/refs/tags/${chrome}/docs/windows_build_instructions.md)`,
    `and Electron's build setup: https://github.com/electron/electron/tree/v${version} (docs/development/build-instructions-gn.md).`,
    "",
    "The license texts are in the archive (COPYING.LGPLv2.1, LICENSE.md) and in LICENSES.chromium.html.",
    "You may replace ffmpeg.dll with your own build of this library.",
    "",
  ].join("\r\n"));
  console.log(`  • ffmpeg source bundled (Chromium ${chrome}, ffmpeg ${rev.slice(0, 12)})`);
}
