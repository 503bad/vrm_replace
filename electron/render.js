// 工程 4（配布版）: three-vrm を Electron の見えないウィンドウ（GPU）で動かし、shot_NNN/render/f%04d.png を書き出す。
// 開発版の render/s4_threevrm.js（Node + Puppeteer + Chrome）と同じ page.html / renderer.js を使い、引数も同じ。
//   Replace2VRM.exe --render --work <dir> --vrm <file> [--shot shot_000] [--max-frames N] ...
"use strict";
const { BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const log = (s) => process.stdout.write(s + "\n");

async function runRender(root, argv) {
  const { parseArgs, startServer, listShots, writePngs } = await import(pathToFileURL(path.join(root, "render", "server.js")).href);
  const args = parseArgs(argv);
  if (!args.gpuTest && (!args.work || !args.vrm)) throw new Error("--work と --vrm が必要です");
  if (args.gpuTest && !args.vrm) args.vrm = path.join(root, "render", "page.html");
  if (args.gpuTest && !args.work) args.work = path.join(root, "render");

  const shots = listShots(args);
  if (!args.gpuTest && shots.length === 0) { log("  nothing to render"); return; }

  const { server, port } = await startServer(args);
  const win = new BrowserWindow({
    show: false, width: 640, height: 360, useContentSize: true,
    webPreferences: { backgroundThrottling: false, contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  const js = (code) => win.webContents.executeJavaScript(code, true);
  const call = (fn, ...a) => js(`${fn}(...${JSON.stringify(a)})`);
  try {
    win.webContents.on("console-message", (e) => {
      const t = e.message || "";
      if (!t.startsWith("THREE.") && !t.includes("404") && !t.startsWith("Electron Security Warning")) log("  [page] " + t);
    });
    await win.loadURL(`http://127.0.0.1:${port}/page.html`);
    for (let i = 0; !(await js("window.vrmReplaceReady === true")); i++) {
      if (i > 600) throw new Error("renderer.js が読み込めませんでした");
      await new Promise((r) => setTimeout(r, 50));
    }
    const gpu = await call("window.probeGPU");
    log(`  WebGL renderer: ${gpu.renderer} (${gpu.vendor}) webgl2=${gpu.webgl2}`);
    if (/swiftshader|software|llvmpipe/i.test(gpu.renderer) && !args.allowSoftware) {
      throw new Error("GPU レンダリングが効いていません（SwiftShader）。NVIDIA ドライバーを確認してください");
    }
    if (args.gpuTest) { log("  gpu test ok"); return; }

    const initInfo = await call("window.initRenderer", {
      vrmUrl: "/vrm", hipHeight: args.hipHeight, lightDir: args.lightDir,
      lightIntensity: args.lightIntensity, ambient: args.ambient,
    });
    log(`  VRM loaded: spec=${initInfo.specVersion} hipHeight=${initInfo.hipHeight.toFixed(3)} scale=${initInfo.scale.toFixed(3)} springs=${initInfo.springs} bones=${initInfo.bones.length}`);

    for (const shot of shots) {
      const sd = path.join(args.work, shot);
      const meta = JSON.parse(fs.readFileSync(path.join(sd, "meta.json"), "utf8"));
      const outDir = path.join(sd, "render");
      fs.mkdirSync(outDir, { recursive: true });
      win.setContentSize(meta.width, meta.height);
      const info = await call("window.loadShot", {
        mocapUrl: `/shot/${shot}/mocap.json`, width: meta.width, height: meta.height, warmup: args.warmup,
      });
      const n = args.maxFrames > 0 ? Math.min(args.maxFrames, info.nFrames) : info.nFrames;
      log(`  ${shot}: ${n} frames ${meta.width}x${meta.height} fps=${info.fps.toFixed(3)} bones(used)=${info.usedBones} hipHeight=${info.hipHeight.toFixed(3)} scale=${info.scale.toFixed(3)}`);
      const t0 = Date.now();
      let pending = [];
      for (let f = 0; f < n; f += args.batch) {
        const to = Math.min(n, f + args.batch);
        pending.push(...writePngs(outDir, f, await call("window.renderBatch", f, to)));
        if (pending.length > 64) { await Promise.all(pending); pending = []; }
        if (to % 120 === 0 || to === n) log(`    ${to}/${n}  ${(to / ((Date.now() - t0) / 1000)).toFixed(1)} fps`);
      }
      await Promise.all(pending);
      if (!args.maxFrames) fs.writeFileSync(path.join(sd, ".done_4"), "");
      log(`  ${shot}: rendered ${n} frames`);
    }
  } finally {
    win.destroy();
    server.close();
  }
}

module.exports = { runRender };
