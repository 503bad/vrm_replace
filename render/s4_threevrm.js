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
import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const a = { hipHeight: 0.93, warmup: 45, lightDir: [0.4, 1.0, 0.8], lightIntensity: 1.6,
              ambient: 0.9, maxFrames: 0, shot: null, chrome: "", allowSoftware: false,
              noSkip: false, gpuTest: false, batch: 6 };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], v = argv[i + 1];
    switch (k) {
      case "--work": a.work = path.resolve(v); i++; break;
      case "--vrm": a.vrm = path.resolve(v); i++; break;
      case "--shot": a.shot = v; i++; break;
      case "--max-frames": a.maxFrames = +v; i++; break;
      case "--hip-height": a.hipHeight = v === "auto" ? "auto" : +v; i++; break;
      case "--warmup": a.warmup = +v; i++; break;
      case "--light-dir": a.lightDir = v.split(",").map(Number); i++; break;
      case "--light-intensity": a.lightIntensity = +v; i++; break;
      case "--ambient": a.ambient = +v; i++; break;
      case "--chrome": a.chrome = v; i++; break;
      case "--batch": a.batch = +v; i++; break;
      case "--allow-software": a.allowSoftware = true; break;
      case "--no-skip": a.noSkip = true; break;
      case "--gpu-test": a.gpuTest = true; break;
      default: throw new Error("unknown arg " + k);
    }
  }
  return a;
}

const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
               ".json": "application/json", ".vrm": "application/octet-stream", ".glb": "application/octet-stream" };

/** page.html / renderer.js / node_modules / vrm / mocap.json を配る小さな HTTP サーバー */
function startServer(args) {
  const routes = async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    let file;
    if (url.pathname === "/vrm") file = args.vrm;
    else if (url.pathname.startsWith("/shot/")) {
      const [, , shot, name] = url.pathname.split("/");
      file = path.join(args.work, shot, name);
    } else if (url.pathname.startsWith("/node_modules/")) file = path.join(HERE, url.pathname);
    else file = path.join(HERE, url.pathname === "/" ? "page.html" : url.pathname);
    // ディレクトリトラバーサル防止
    const rel = path.relative(HERE, file);
    if (!(file === args.vrm || file.startsWith(args.work) || !rel.startsWith(".."))) {
      res.writeHead(403); res.end(); return;
    }
    try {
      const st = await fsp.stat(file);
      res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream",
                           "Content-Length": st.size, "Cache-Control": "no-store" });
      fs.createReadStream(file).pipe(res);
    } catch (e) {
      res.writeHead(404); res.end("not found: " + file);
    }
  };
  const server = http.createServer(routes);
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port })));
}

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

  const shots = args.gpuTest ? [] : fs.readdirSync(args.work).filter((d) => /^shot_\d+$/.test(d)).sort()
    .filter((d) => !args.shot || d === args.shot)
    .filter((d) => args.noSkip || !fs.existsSync(path.join(args.work, d, ".done_4")));
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
        for (let i = 0; i < pngs.length; i++) {
          const buf = Buffer.from(pngs[i].slice(pngs[i].indexOf(",") + 1), "base64");
          pending.push(fsp.writeFile(path.join(outDir, `f${String(f + i).padStart(4, "0")}.png`), buf));
        }
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
