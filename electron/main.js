// Replace2VRM 配布版（Electron）のメインプロセス。
//  - 通常起動: GPU 確認 → Python 環境の用意（初回のみダウンロード）→ 画面（app/ui）を開き、処理は Python（app/bridge.py）に任せる
//  - --render: 工程 4 のレンダー専用（Python の stages/s4_render.py から呼ばれる。electron/render.js）
"use strict";
const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");

const RENDER_MODE = process.argv.includes("--render");
const SELFTEST = process.env.VRM_REPLACE_SELFTEST || "";
const ROOT = app.isPackaged ? path.join(process.resourcesPath, "vrm_replace") : path.resolve(__dirname, "..");
const UV = app.isPackaged ? path.join(process.resourcesPath, "uv", "uv.exe") : path.join(__dirname, "vendor", "uv", "uv.exe");
// 書き込み先。配布版は %LOCALAPPDATA%\Replace2VRM、開発時（electron .）はプロジェクトのフォルダ（run.cmd 版と共用）
const DATA = path.resolve(process.env.VRM_REPLACE_DATA ||
  (app.isPackaged ? path.join(process.env.LOCALAPPDATA || app.getPath("appData"), "Replace2VRM") : ROOT));

app.setPath("userData", path.join(DATA, "electron-profile", RENDER_MODE ? "render" : "app"));
// レンダーは GPU（D3D11）で。見えないウィンドウでも止まらないようにする
app.commandLine.appendSwitch("use-angle", "d3d11");
app.commandLine.appendSwitch("ignore-gpu-blocklist");
app.commandLine.appendSwitch("disable-renderer-backgrounding");
app.commandLine.appendSwitch("disable-background-timer-throttling");
if (RENDER_MODE) app.commandLine.appendSwitch("force-device-scale-factor", "1");

if (RENDER_MODE) {
  const argv = process.argv.slice(process.argv.indexOf("--render") + 1);
  app.whenReady().then(async () => {
    try {
      await require("./render").runRender(ROOT, argv);
      app.exit(0);
    } catch (e) {
      process.stderr.write(String(e && e.stack || e) + "\n");
      app.exit(1);
    }
  });
  app.on("window-all-closed", () => {});
} else {
  startGui();
}

// ------------------------------------------------------------------ GUI
function startGui() {
  if (!app.requestSingleInstanceLock()) { app.quit(); return; }
  let win = null, bridge = null, quitting = false;

  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

  const logDir = path.join(DATA, "logs");
  const send = (ch, v) => { if (win && !win.isDestroyed()) win.webContents.send(ch, v); };

  async function boot() {
    const { gpuError } = require("./gpu");
    const err = gpuError();
    if (err) { dialog.showErrorBox("Replace2VRM", err); app.exit(1); return; }

    win = new BrowserWindow({
      width: 1280, height: 860, minWidth: 1100, minHeight: 720, backgroundColor: "#0F1114", title: "Replace2VRM", icon: path.join(__dirname, "build", "icon.png"),
      show: false, autoHideMenuBar: true, ...(SELFTEST ? { paintWhenInitiallyHidden: true } : {}),
      // backgroundThrottling: 最小化中も進み具合の表示を止めない
      webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, spellcheck: false,
                        backgroundThrottling: false },
    });
    Menu.setApplicationMenu(null);
    if (!SELFTEST) win.once("ready-to-show", () => win.show());
    // ドロップしたファイルへ画面が移動したり、新しいウィンドウが開いたりしないようにする
    win.webContents.on("will-navigate", (e) => e.preventDefault());
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.on("close", onClose);
    await win.loadFile(path.join(__dirname, "setup.html"));
    await setupAndStart();
  }

  async function setupAndStart() {
    fs.mkdirSync(logDir, { recursive: true });
    const logFile = fs.createWriteStream(path.join(logDir, "setup.log"), { flags: "a" });
    try {
      const py = await require("./setup").ensurePython({ root: ROOT, data: DATA, uv: UV },
        (s) => send("setup:step", s), (t) => { logFile.write(t); send("setup:log", t); });
      send("setup:step", "起動しています");
      bridge = await startBridge(py);
      await win.loadFile(path.join(ROOT, "app", "ui", "index.html"));
      if (SELFTEST) await selftest();
    } catch (e) {
      send("setup:error", String(e.message || e));
    } finally {
      logFile.end();
    }
  }

  function startBridge(py) {
    const { pyEnv } = require("./setup");
    const env = {
      ...pyEnv({ data: DATA, uv: UV }),
      VRM_REPLACE_ELECTRON: process.execPath,
      VRM_REPLACE_OUT: process.env.VRM_REPLACE_OUT || path.join(app.getPath("videos"), "Replace2VRM"),
    };
    if (!app.isPackaged) env.VRM_REPLACE_ELECTRON_APP = __dirname;   // 開発時は electron.exe にアプリのフォルダを渡す
    const proc = spawn(py, ["-u", path.join(ROOT, "app", "bridge.py")], { cwd: ROOT, env, windowsHide: true });
    const errLog = fs.createWriteStream(path.join(logDir, "bridge.log"), { flags: "a" });
    proc.stderr.pipe(errLog);
    const pending = new Map();
    let nextId = 1;
    return new Promise((resolve, reject) => {
      const rl = readline.createInterface({ input: proc.stdout });
      rl.on("line", (line) => {
        let msg;
        try { msg = JSON.parse(line); } catch { return; }
        if (msg.id === 0) { resolve(b); return; }
        const p = pending.get(msg.id);
        if (!p) return;
        pending.delete(msg.id);
        if ("error" in msg) p.reject(new Error(msg.error)); else p.resolve(msg.result);
      });
      proc.on("exit", (code) => {
        for (const p of pending.values()) p.reject(new Error("処理側が終了しました"));
        pending.clear();
        reject(new Error(`処理側（Python）を起動できませんでした（終了コード ${code}）。${path.join(logDir, "bridge.log")} を確認してください`));
        if (!quitting && win && !win.isDestroyed()) {
          dialog.showErrorBox("Replace2VRM", `処理側（Python）が終了しました。ログ: ${path.join(logDir, "bridge.log")}`);
        }
      });
      const b = {
        proc,
        call(method, args = []) {
          const id = nextId++;
          return new Promise((res, rej) => {
            pending.set(id, { resolve: res, reject: rej });
            proc.stdin.write(JSON.stringify({ id, method, args }) + "\n");
          });
        },
      };
    });
  }

  // 画面からの呼び出し。ファイル選択ダイアログだけはこちらで出す
  ipcMain.handle("api", async (_e, method, args) => {
    if (!bridge) throw new Error("処理側がまだ起動していません");
    if (method === "pick") {
      const kind = args[0];
      const info = await bridge.call("dialog_info", [kind]);
      const filters = kind === "video" ? [{ name: "動画", extensions: ["mp4", "mov", "avi", "mkv", "webm", "m4v"] }]
                                       : [{ name: "VRM", extensions: ["vrm"] }];
      const r = await dialog.showOpenDialog(win, { defaultPath: info.dir, filters, properties: ["openFile"] });
      if (!r.canceled && r.filePaths[0]) return bridge.call("set_file", [r.filePaths[0]]);
      return null;
    }
    if (method === "pick_output") {
      const info = await bridge.call("dialog_info", ["out"]);
      const r = await dialog.showSaveDialog(win, { defaultPath: path.join(info.dir, info.name),
                                                   filters: [{ name: info.ext, extensions: [info.ext] }] });
      if (!r.canceled && r.filePath) return bridge.call("set_output", [r.filePath]);
      return null;
    }
    return bridge.call(method, args);
  });
  ipcMain.handle("setup:retry", () => setupAndStart());
  ipcMain.handle("setup:open-logs", () => shell.openPath(logDir));

  async function onClose(e) {
    if (quitting) return;
    e.preventDefault();
    let busy = false;
    try { busy = bridge ? await bridge.call("is_busy") : false; } catch { busy = false; }
    if (busy) {
      const r = await dialog.showMessageBox(win, { type: "question", title: "Replace2VRM", message: "実行中です。停止して終了しますか？",
                                                   buttons: ["停止して終了", "キャンセル"], defaultId: 1, cancelId: 1 });
      if (r.response !== 0) return;
      try { await bridge.call("stop"); } catch { /* 終了するので無視 */ }
    }
    quitting = true;
    win.close();
  }

  // 動作確認用（環境変数 VRM_REPLACE_SELFTEST=<出力フォルダ>）: 画面を出さずに起動し、ページの画像と API の結果を保存して終わる
  async function selftest() {
    const out = SELFTEST, wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const shot = async (name) => fs.writeFileSync(path.join(out, name), (await win.webContents.capturePage()).toPNG());
    const res = { console: [] };
    win.webContents.on("console-message", (e) => res.console.push(`${e.level}: ${e.message} (${e.sourceId}:${e.lineNumber})`));
    try {
      fs.mkdirSync(out, { recursive: true });
      await wait(2500);
      await shot("1_idle.png");
      res.init = await win.webContents.executeJavaScript("window.pywebview.api.init().then(r => ({ phase: r.state.job.phase, stages: r.stages.length }))");
      for (const f of (process.env.VRM_REPLACE_SELFTEST_FILES || "").split(";").filter(Boolean)) {
        res["set_file:" + path.basename(f)] = await bridge.call("set_file", [f]);
      }
      await wait(2000);
      await shot("2_files.png");
      await win.webContents.executeJavaScript('document.querySelector("#btn-about").click()');
      await wait(1200);
      await shot("3_license.png");
      const pt = (process.env.VRM_REPLACE_SELFTEST_RUN || "").split(",").map(Number);
      if (pt.length === 2) {     // START → 人物を選ぶ → 完了まで（VRM_REPLACE_SELFTEST_RUN=x,y）
        await win.webContents.executeJavaScript('document.querySelector("#about-close").click()');
        await bridge.call("set_option", ["max_frames", 60]);
        if (process.env.VRM_REPLACE_SELFTEST_FORMAT) await bridge.call("set_option", ["output_format", process.env.VRM_REPLACE_SELFTEST_FORMAT]);
        res.start = await bridge.call("start");
        const phase = async () => (await bridge.call("init")).state.job;
        let j;
        for (let i = 0; i < 600 && (j = await phase()).phase === "prep"; i++) await wait(500);
        if (j.phase === "select") { await shot("4_select.png"); await bridge.call("submit_points", [[pt], [1]]); }
        for (let i = 0; i < 3600 && ["select", "run"].includes((j = await phase()).phase); i++) await wait(500);
        res.job = { phase: j.phase, message: j.message, result: j.result && { ...j.result, preview: !!j.result.preview },
                    stages: j.stages.map((s) => `${s.id}:${s.state}:${s.elapsed && s.elapsed.toFixed(1)}`) };
        await wait(1000);
        await shot("5_done.png");
      }
      res.ok = true;
    } catch (e) {
      res.error = String(e && e.stack || e);
    }
    fs.writeFileSync(path.join(out, "selftest.json"), JSON.stringify(res, null, 2));
    quitting = true;
    app.quit();
  }

  app.on("window-all-closed", () => app.quit());
  app.on("will-quit", () => {
    quitting = true;
    if (bridge && bridge.proc.exitCode === null) {
      bridge.proc.stdin.end();     // bridge.py は標準入力が閉じると実行中の処理を止めて終わる
      setTimeout(() => { try { bridge.proc.kill(); } catch { /* 終了済み */ } }, 3000).unref();
    }
  });
  app.whenReady().then(boot);
}
