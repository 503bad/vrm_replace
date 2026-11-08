/**
 * 工程 4 のレンダーで共有する部品（Node + Puppeteer 版 s4_threevrm.js と、配布版の electron/render.js）
 *  - parseArgs: コマンドライン引数
 *  - startServer: page.html / renderer.js / three / VRM / mocap.json を配る小さな HTTP サーバー
 *  - listShots: レンダーするショット
 */
import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const HERE = path.dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
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

export const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
               ".json": "application/json", ".vrm": "application/octet-stream", ".glb": "application/octet-stream" };

/** page.html / renderer.js / node_modules / vrm / mocap.json を配る小さな HTTP サーバー */
export function startServer(args) {
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

export function listShots(args) {
  if (args.gpuTest) return [];
  return fs.readdirSync(args.work).filter((d) => /^shot_\d+$/.test(d)).sort()
    .filter((d) => !args.shot || d === args.shot)
    .filter((d) => {
      if (args.noSkip) return true;
      const doneF = path.join(args.work, d, ".done_4"), mocapF = path.join(args.work, d, "mocap.json");
      if (!fs.existsSync(doneF)) return true;
      // mocap.json が .done_4 より新しい（オプション変更で s2 をやり直した）なら再レンダー
      return fs.existsSync(mocapF) && fs.statSync(mocapF).mtimeMs > fs.statSync(doneF).mtimeMs;
    });
}

/** dataURL（PNG）の配列をファイルに書く */
export function writePngs(outDir, first, pngs) {
  return pngs.map((u, i) => fsp.writeFile(path.join(outDir, `f${String(first + i).padStart(4, "0")}.png`),
                                         Buffer.from(u.slice(u.indexOf(",") + 1), "base64")));
}
