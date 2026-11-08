// 画面（app/ui/app.js）は pywebview 版と共通。pywebview と同じ形（window.pywebview.api.〜 と pywebviewready）で処理側を呼べるようにする
"use strict";
const { contextBridge, ipcRenderer, webUtils } = require("electron");

const METHODS = ["init", "poll", "pick", "pick_output", "set_file", "clear_file", "set_option", "start", "stop",
                 "submit_points", "open_output", "open_folder", "dismiss", "is_busy", "licenses", "open_link"];
const api = {};
for (const m of METHODS) api[m] = (...args) => ipcRenderer.invoke("api", m, args);
contextBridge.exposeInMainWorld("pywebview", { api });

// 初回セットアップの画面（electron/setup.html）用
contextBridge.exposeInMainWorld("vrmSetup", {
  onStep: (cb) => ipcRenderer.on("setup:step", (_e, v) => cb(v)),
  onLog: (cb) => ipcRenderer.on("setup:log", (_e, v) => cb(v)),
  onError: (cb) => ipcRenderer.on("setup:error", (_e, v) => cb(v)),
  retry: () => ipcRenderer.invoke("setup:retry"),
  openLogs: () => ipcRenderer.invoke("setup:open-logs"),
});

// ドロップされたファイルのフルパスを処理側へ（pywebview 版は gui.py の on_drop が受ける）
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  if (!location.pathname.endsWith("/index.html")) return;
  for (const f of e.dataTransfer.files) {
    const p = webUtils.getPathForFile(f);
    if (p) ipcRenderer.invoke("api", "set_file", [p]);
  }
});

window.addEventListener("DOMContentLoaded", () => window.dispatchEvent(new Event("pywebviewready")));
