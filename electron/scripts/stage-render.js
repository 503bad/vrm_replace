// ビルド前: レンダー（render/page.html）が読み込む three.js と three-vrm（どちらも MIT）を vendor/render_modules にコピーする。
// electron-builder は node_modules という名前のフォルダを extraResources から除くので、別名で置いて同梱時に node_modules へ戻す。
// 実行時に使うファイルと、ライセンス文だけを入れる。
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "..", "render", "node_modules");
const OUT = path.join(__dirname, "..", "vendor", "render_modules");
const SKIP = /\.(map|d\.ts|ts)$/;

function copy(rel) {
  const s = path.join(SRC, rel), d = path.join(OUT, rel);
  if (!fs.existsSync(s)) throw new Error(`見つかりません: ${s}（render で npm install を実行してください）`);
  fs.cpSync(s, d, { recursive: true, filter: (p) => !SKIP.test(p) });
}

fs.rmSync(OUT, { recursive: true, force: true });
for (const f of ["three/package.json", "three/LICENSE", "three/build/three.module.js", "three/build/three.core.js",
                 "three/examples/jsm"]) copy(f);
for (const pkg of fs.readdirSync(path.join(SRC, "@pixiv"))) {
  const dir = path.join("@pixiv", pkg);
  for (const f of fs.readdirSync(path.join(SRC, dir))) {
    if (["package.json", "LICENSE", "lib"].includes(f) || /^LICENSE/i.test(f)) copy(path.join(dir, f));
  }
}
console.log(`render modules staged: ${OUT}`);
