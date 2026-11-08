// 起動時の GPU 確認（common.py の gpu_error と同じ判定。Python を入れる前に調べるので JS で持つ）
"use strict";
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const NON_NVIDIA_MSG = "NVIDIA 以外の GPUは利用できません";

/** NVIDIA GPU（NVIDIA Optical Flow 対応 = Turing 以降）が無ければ、その理由。使えれば null */
function gpuError() {
  const r = spawnSync("nvidia-smi", ["--query-gpu=name,compute_cap", "--format=csv,noheader"],
                      { encoding: "utf8", windowsHide: true, timeout: 20000 });
  if (r.error || r.status !== 0 || !r.stdout) return NON_NVIDIA_MSG;
  const caps = r.stdout.trim().split(/\r?\n/).map((l) => parseFloat(l.split(",").pop())).filter((x) => !isNaN(x));
  if (!caps.length) return NON_NVIDIA_MSG;
  if (Math.max(...caps) < 7.5) {
    return "この NVIDIA GPU は NVIDIA Optical Flow に対応していません（Turing / GeForce RTX 20 シリーズ以降が必要です）";
  }
  const sys32 = path.join(process.env.SystemRoot || "C:\\Windows", "System32");
  if (!fs.existsSync(path.join(sys32, "nvofapi64.dll"))) {
    return "NVIDIA ドライバーに NVIDIA Optical Flow が含まれていません。ドライバーを最新版に更新してください";
  }
  return null;
}

module.exports = { gpuError, NON_NVIDIA_MSG };
