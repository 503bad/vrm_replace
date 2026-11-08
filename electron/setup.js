// 配布版の Python 環境の用意。初回起動時に、同梱の uv で Python 3.12 と依存パッケージ・モデルを各配布元から取得する。
// exe には Python・PyTorch・ffmpeg などを入れない（各ライセンスの条件どおり、利用者の環境へ配布元から導入する）。
"use strict";
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

function venvPython(data) {
  return path.join(data, "venv", "Scripts", "python.exe");
}

function pyEnv(paths) {
  return {
    ...process.env,
    VRM_REPLACE_DATA: paths.data,
    VRM_REPLACE_UV: paths.uv,
    UV_PYTHON_INSTALL_DIR: path.join(paths.data, "python"),
    UV_CACHE_DIR: path.join(paths.data, "cache", "uv"),
    UV_NO_PROGRESS: "1",
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  };
}

/** コマンドを実行し、出力を onLog に流す。終了コードを返す */
function exec(cmd, args, opts, onLog) {
  return new Promise((resolve) => {
    onLog(`$ ${path.basename(cmd)} ${args.join(" ")}\n`);
    const p = spawn(cmd, args, { ...opts, windowsHide: true });
    p.stdout.on("data", (d) => onLog(d.toString("utf8")));
    p.stderr.on("data", (d) => onLog(d.toString("utf8")));
    p.on("error", (e) => { onLog(String(e) + "\n"); resolve(-1); });
    p.on("close", (code) => resolve(code));
  });
}

/**
 * Python 環境を用意して、その python.exe を返す。失敗したら例外。
 * paths: { root（Python のコード）, data（書き込み先）, uv（uv.exe） }
 * 環境変数 VRM_REPLACE_PYTHON があれば、それをそのまま使う（開発・動作確認用。導入は行わない）
 */
async function ensurePython(paths, onStep, onLog) {
  if (process.env.VRM_REPLACE_PYTHON) return process.env.VRM_REPLACE_PYTHON;
  fs.mkdirSync(paths.data, { recursive: true });
  const env = pyEnv(paths);
  const py = venvPython(paths.data);
  if (!fs.existsSync(py)) {
    onStep("Python 3.12 を用意しています（初回のみ）");
    const code = await exec(paths.uv, ["venv", path.join(paths.data, "venv"), "--python", "3.12",
                                       "--python-preference", "only-managed", "--seed"], { env, cwd: paths.data }, onLog);
    if (code !== 0 || !fs.existsSync(py)) throw new Error("Python の用意に失敗しました");
  }
  onStep("必要なものを確認しています（初回は PyTorch など約 4GB をダウンロードします）");
  const code = await exec(py, ["-u", path.join(paths.root, "setup_env.py"), "--app"], { env, cwd: paths.root }, onLog);
  if (code !== 0) throw new Error("セットアップに失敗しました");
  return py;
}

module.exports = { ensurePython, pyEnv };
