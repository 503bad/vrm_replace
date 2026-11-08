#!/usr/bin/env node
/**
 * Stage 4: three-vrm を ヘッドレス Chrome（GPU）で動かし、shot_NNN/mocap.json のモーションを
 * VRM に適用して shot_NNN/render/f%04d.png（RGBA 透過）を書き出す。
 *
 *   node s4_threevrm.js --work <dir> --vrm <file> [--shot shot_000] [--max-frames N]
 *                       [--hip-height 0.93] [--warmup 45] [--light-dir x,y,z]
 *                       [--light-intensity 1.6] [--ambient 0.9] [--chrome <exe>]
 *                       [--allow-software] [--no-skip] [--gpu-test]
 */
import fs from "node:fs";
import path from "node:path";
import puppeteer from "puppeteer";
import { HERE, parseArgs, startServer, listShots, writePngs } from "./server.js";

async function launchBrowser(args, width, height) {
  const flags = [
    "--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", "--enable-gpu-rasterization",
    "--enable-webgl", "--enable-unsafe-webgpu", "--disable-gpu-sandbox", "--no-sandbox",
    "--disable-dev-shm-usage", "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding", "--disable-features=CalculateNativeWinOcclusion",
    `--window-size=${width},${height}`,
  ];
  const opts = { headless: true, args: flags, protocolTimeout: 0 };
  if (args.chrome) opts.executablePath = args.chrome;
  return puppeteer.launch(opts);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.gpuTest && (!args.work || !args.vrm)) throw new Error("--work と --vrm が必要です");
  if (args.gpuTest && !args.vrm) args.vrm = path.join(HERE, "..", "sample", "dummy.vrm");
  if (args.gpuTest && !args.work) args.work = HERE;

  const shots = listShots(args);
  if (!args.gpuTest && shots.length === 0) { console.log("  nothing to render"); return; }

  const firstMeta = args.gpuTest ? { width: 640, height: 360 } :
    JSON.parse(fs.readFileSync(path.join(args.work, shots[0], "meta.json"), "utf8"));
  const { server, port } = await startServer(args);
  const browser = await launchBrowser(args, firstMeta.width, firstMeta.height);
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(0);
    page.on("console", (m) => { const t = m.text(); if (!t.startsWith("THREE.") && !t.includes("404")) console.log("  [page]", t); });
    page.on("pageerror", (e) => console.error("  [pageerror]", e.message));
    await page.setViewport({ width: firstMeta.width, height: firstMeta.height });
    await page.goto(`http://127.0.0.1:${port}/page.html`, { waitUntil: "load" });
    await page.waitForFunction(() => window.vrmReplaceReady === true);

    const gpu = await page.evaluate(() => window.probeGPU());
    console.log(`  WebGL renderer: ${gpu.renderer} (${gpu.vendor}) webgl2=${gpu.webgl2}`);
    const software = /swiftshader|software|llvmpipe/i.test(gpu.renderer);
    if (software && !args.allowSoftware) {
      throw new Error("GPU レンダリングが効いていません（SwiftShader）。config の chrome_exe にインストール済み Chrome を指定するか、allow_software_gl: true で続行");
    }
    if (args.gpuTest) { console.log("  gpu test ok"); return; }

    const initInfo = await page.evaluate((o) => window.initRenderer(o), {
      vrmUrl: "/vrm", hipHeight: args.hipHeight, lightDir: args.lightDir,
      lightIntensity: args.lightIntensity, ambient: args.ambient,
    });
    console.log(`  VRM loaded: spec=${initInfo.specVersion} hipHeight=${initInfo.hipHeight.toFixed(3)} scale=${initInfo.scale.toFixed(3)} springs=${initInfo.springs} bones=${initInfo.bones.length}`);

    for (const shot of shots) {
      const sd = path.join(args.work, shot);
      const meta = JSON.parse(fs.readFileSync(path.join(sd, "meta.json"), "utf8"));
      const outDir = path.join(sd, "render");
      fs.mkdirSync(outDir, { recursive: true });
      await page.setViewport({ width: meta.width, height: meta.height });
      const info = await page.evaluate((o) => window.loadShot(o), {
        mocapUrl: `/shot/${shot}/mocap.json`, width: meta.width, height: meta.height, warmup: args.warmup,
      });
      const n = args.maxFrames > 0 ? Math.min(args.maxFrames, info.nFrames) : info.nFrames;
      console.log(`  ${shot}: ${n} frames ${meta.width}x${meta.height} fps=${info.fps.toFixed(3)} bones(used)=${info.usedBones} hipHeight=${info.hipHeight.toFixed(3)} scale=${info.scale.toFixed(3)}`);
      const t0 = Date.now();
      let pending = [];
      for (let f = 0; f < n; f += args.batch) {
        const to = Math.min(n, f + args.batch);
        const pngs = await page.evaluate((a, b) => window.renderBatch(a, b), f, to);
        pending.push(...writePngs(outDir, f, pngs));
        if (pending.length > 64) { await Promise.all(pending); pending = []; }
        if (to % 120 === 0 || to === n) {
          const el = (Date.now() - t0) / 1000;
          console.log(`    ${to}/${n}  ${(to / el).toFixed(1)} fps`);
        }
      }
      await Promise.all(pending);
      if (!args.maxFrames) fs.writeFileSync(path.join(sd, ".done_4"), "");
      console.log(`  ${shot}: rendered ${n} frames`);
    }
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
