// vrm_replace GUI（画面側）。状態は Python 側（app/backend.py）が持ち、ここは poll() の結果を描くだけ
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let api = null;
let ver = -1, logNext = 0, state = null, stagesDef = [];
const pick = { points: [], labels: [], key: null };

function fmtTime(sec) {
  if (sec == null || !isFinite(sec)) return "";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function fmtSize(b) {
  if (!b) return "";
  return b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : (b / 1e3).toFixed(0) + " KB";
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------------------------------------------------------------- render
function buildStages() {
  $("#stages").innerHTML = stagesDef.map((st) => `
    <div class="stage" data-id="${st.id}">
      <div class="idx">${st.id}</div>
      <div class="nm"><span class="j">${esc(st.label)}</span><span class="k mono">${esc(st.key)}</span></div>
      <div class="tm num"></div>
      <div class="bar"><i></i></div>
    </div>`).join("");
}

function renderFiles(s) {
  const busy = ["prep", "select", "run"].includes(s.job.phase);
  for (const kind of ["video", "vrm"]) {
    const el = $(`#drop-${kind}`), f = s.files[kind], err = s.errors[kind];
    el.classList.toggle("has", !!f);
    el.classList.toggle("err", !!err);
    const img = $("img", el);
    if (f && f.thumb) { if (img.src !== f.thumb) img.src = f.thumb; img.style.display = ""; }
    else { img.removeAttribute("src"); img.style.display = f ? "none" : ""; }
    if (f && !f.thumb) el.classList.add("nothumb"); else el.classList.remove("nothumb");
    $(".drop-empty", el).dataset.big = f ? (kind === "vrm" ? "VRM " + f.version : "VIDEO") : "";
    $("[data-f=name]", el).textContent = f ? (kind === "vrm" && f.title ? f.title : f.name) : (err ? "読み込めません" : "—");
    $("[data-f=name]", el).title = f ? f.path : "";
    let meta = "";
    if (err) meta = err;
    else if (f && kind === "video")
      meta = `${f.width}×${f.height} · ${f.fps} fps · ${f.frames} f · ${fmtTime(f.duration)} · ${fmtSize(f.size)}`;
    else if (f && kind === "vrm")
      meta = [f.author && `作者 ${f.author}`, f.title ? f.name : "", fmtSize(f.size)].filter(Boolean).join(" · ");
    $("[data-f=meta]", el).textContent = meta;
    if (kind === "vrm") {
      $("[data-f=ver]", el).textContent = f ? "VRM " + f.version : "";
      $("[data-f=terms]", el).innerHTML = f ? f.terms.map((t) =>
        `<span class="term ${t.ok === false ? "ng" : ""}"><span class="k">${esc(t.label)}</span><span class="v" title="${esc(t.value)}">${esc(t.value)}</span></span>`).join("") : "";
    }
    el.style.cursor = busy ? "default" : "pointer";
    $(".x", el).style.visibility = busy ? "hidden" : "";
  }
  $("#out-path").textContent = s.settings.out || "—";
  $("#out-path").title = s.settings.out || "";
}

function renderSettings(s) {
  const st = s.settings;
  $$(".mode[data-preset]").forEach((b) => b.classList.toggle("on", b.dataset.preset === st.preset));
  $$(".mode[data-format]").forEach((b) => b.classList.toggle("on", b.dataset.format === (st.output_format || "mp4")));
  $$(".tog").forEach((b) => b.classList.toggle("on", !!st[b.dataset.opt]));
  if (document.activeElement !== $("#max-frames")) $("#max-frames").value = st.max_frames || 0;
  $("#from-stage").value = String(st.from_stage || 0);
}

function renderJob(s) {
  const j = s.job, busy = ["prep", "select", "run"].includes(j.phase);
  for (const st of j.stages) {
    const el = $(`.stage[data-id="${st.id}"]`);
    el.className = "stage " + st.state;
    $(".bar i", el).style.width = ((st.state === "done" ? 1 : st.p) * 100).toFixed(1) + "%";
    let tm = "";
    if (st.state === "run") tm = fmtTime(st.elapsed) + (st.eta != null ? `  残り ${fmtTime(st.eta)}` : "");
    else if (st.state === "done") tm = fmtTime(st.elapsed);
    else if (st.state === "skip") tm = "SKIP";
    else if (st.state === "failed") tm = "FAILED";
    else if (st.state === "stopped") tm = "STOPPED";
    $(".tm", el).textContent = tm;
  }
  $("#shots").textContent = j.nshots > 1 && j.phase === "run" ? `SHOT ${j.shot + 1} / ${j.nshots}` : "";
  const chip = $("#phase");
  const P = { idle: ["READY", ""], prep: ["PREPARING", "run"], select: ["SELECT PERSON", "run"], run: ["RUNNING", "run"],
    done: ["DONE", "ok"], failed: ["FAILED", "ng"], stopped: ["STOPPED", ""] }[j.phase];
  chip.textContent = P[0]; chip.className = "chip mono " + P[1];
  $("#elapsed").textContent = fmtTime(j.elapsed) || "0:00";

  const ready = s.files.video && s.files.vrm && s.settings.out;
  const go = $("#btn-start");
  go.classList.toggle("disabled", busy || !ready);
  $("#go-sub").textContent = busy ? "実行中…" : !s.files.video ? "動画をドロップしてください" : !s.files.vrm ? "VRM をドロップしてください"
    : (s.settings.preset === "speed" ? "速度優先で実行" : "品質優先で実行");
  $("#btn-stop").classList.toggle("disabled", !busy);

  const status = $("#status");
  let msg = j.message;
  if (!msg) msg = j.phase === "idle" ? (ready ? "START を押すと、最初に人物を選ぶ画面が出ます（選んだ後は放置で完了します）" : "動画と VRM をウィンドウにドロップしてください")
    : j.phase === "prep" ? "ショットに分割しています…" : j.phase === "run" ? runMessage(j) : "";
  status.textContent = msg;
  status.className = "status " + (j.phase === "failed" ? "ng" : j.phase === "done" ? "ok" : "");

  const res = $("#result");
  if (j.result) {
    res.classList.add("show");
    const img = $("img", res);
    if (j.result.preview && img.src !== j.result.preview) img.src = j.result.preview;
    $("#result-name").textContent = `${j.result.name} · ${fmtSize(j.result.size)}`;
    $("#result-name").title = j.result.path;
  } else res.classList.remove("show");

  renderPick(j);
}

function runMessage(j) {
  const cur = j.stages.find((x) => x.state === "run");
  if (!cur) return "実行中…";
  return `${cur.label}…  ${Math.round(cur.p * 100)}%` + (cur.eta != null ? `  ·  この工程の残り ${fmtTime(cur.eta)}` : "");
}

function renderPick(j) {
  const ov = $("#ov-pick"), sel = j.select;
  if (j.phase !== "select" || !sel) { ov.classList.remove("show"); pick.key = null; return; }
  const key = sel.shot + ":" + sel.index;
  if (pick.key !== key) {
    pick.key = key; pick.points = []; pick.labels = [];
    $("img", $("#pick-frame")).src = sel.image;
    $("#pick-shot").textContent = sel.total > 1 ? `SHOT ${sel.index + 1} / ${sel.total}` : "";
    drawPts();
  }
  ov.classList.add("show");
}

function drawPts() {
  const sel = state && state.job.select;
  if (!sel) return;
  $("#pick-pts").innerHTML = pick.points.map(([x, y], i) =>
    `<div class="pt ${pick.labels[i] ? "" : "neg"}" style="left:${(x / sel.width) * 100}%;top:${(y / sel.height) * 100}%"></div>`).join("");
  $("#pick-ok").classList.toggle("disabled", !pick.labels.includes(1));
  $("#pick-msg").textContent = pick.labels.includes(1) ? `${pick.points.length} 点。Enter で確定` : "人物の胴体あたりを 1 回クリックすれば十分です";
}

function render(s) {
  state = s;
  renderFiles(s);
  renderSettings(s);
  renderJob(s);
}

// ---------------------------------------------------------------- log
function appendLog(lines, partial) {
  const pre = $("#log-text");
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
  if (lines.length) {
    const last = pre.lastChild;
    if (last && last.nodeType === 1 && last.classList.contains("partial")) last.remove();
    pre.append(document.createTextNode(lines.join("\n") + "\n"));
    while (pre.childNodes.length > 400) pre.firstChild.remove();
  }
  let p = pre.querySelector(".partial");
  if (partial) {
    if (!p) { p = document.createElement("span"); p.className = "partial"; pre.append(p); }
    p.textContent = partial;
  } else if (p) p.remove();
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

// ---------------------------------------------------------------- loop
async function tick() {
  try {
    const r = await api.poll(ver, logNext);
    if (r.state) render(r.state);
    ver = r.ver;
    if (r.log.length || r.partial !== undefined) appendLog(r.log, r.partial);
    logNext = r.log_next;
  } catch (e) { console.error(e); }
  setTimeout(tick, 200);
}

// ---------------------------------------------------------------- events
function bind() {
  for (const kind of ["video", "vrm"]) {
    const el = $(`#drop-${kind}`);
    el.addEventListener("click", (e) => {
      if (e.target.closest(".x")) return;
      if (state && ["prep", "select", "run"].includes(state.job.phase)) return;
      api.pick(kind);
    });
    $(".x", el).addEventListener("click", (e) => { e.stopPropagation(); api.clear_file(kind); });
  }
  $("#btn-out").addEventListener("click", () => api.pick_output());
  $$(".mode[data-preset]").forEach((b) => b.addEventListener("click", () => api.set_option("preset", b.dataset.preset)));
  $$(".mode[data-format]").forEach((b) => b.addEventListener("click", () => api.set_option("output_format", b.dataset.format)));
  $$(".tog").forEach((b) => b.addEventListener("click", () => api.set_option(b.dataset.opt, !b.classList.contains("on"))));
  $("#max-frames").addEventListener("change", (e) => api.set_option("max_frames", Math.max(0, parseInt(e.target.value) || 0)));
  $("#from-stage").addEventListener("change", (e) => api.set_option("from_stage", parseInt(e.target.value) || 0));
  $("#btn-start").addEventListener("click", async () => {
    if ($("#btn-start").classList.contains("disabled")) return;
    const r = await api.start();
    if (!r.ok) { $("#status").textContent = r.message; $("#status").className = "status ng"; }
  });
  $("#btn-stop").addEventListener("click", () => { if (!$("#btn-stop").classList.contains("disabled")) api.stop(); });
  $("#btn-play").addEventListener("click", () => api.open_output());
  $("#btn-folder").addEventListener("click", () => api.open_folder());
  $("#btn-log").addEventListener("click", () => { $("#log").classList.toggle("open"); $("#btn-log").classList.toggle("on"); });

  // ライセンス画面
  const about = $("#ov-about");
  const closeAbout = () => about.classList.remove("show");
  $("#btn-about").addEventListener("click", async () => {
    const r = await api.licenses(), tabs = $("#about-tabs");
    tabs.innerHTML = "";
    r.docs.forEach((d, i) => {
      const b = document.createElement("button");
      b.textContent = d.label;
      b.addEventListener("click", () => {
        tabs.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        $("#about-text").textContent = d.text;
        $("#about-text").scrollTop = 0;
      });
      tabs.appendChild(b);
      if (i === 0) b.click();
    });
    about.classList.add("show");
  });
  $("#about-close").addEventListener("click", closeAbout);
  about.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", () => api.open_link(b.dataset.link)));
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && about.classList.contains("show")) closeAbout(); });

  // ドラッグ中の表示（ドロップ自体は Python 側がフルパス付きで受け取る）
  // capture（true）で受けるのは、Python 側のドロップ処理より先に表示を戻すため
  const ov = $("#ov-drag");
  const busy = () => state && ["prep", "select", "run"].includes(state.job.phase);
  const hide = () => ov.classList.remove("show");
  window.addEventListener("dragenter", (e) => { e.preventDefault(); if (!busy()) ov.classList.add("show"); }, true);
  window.addEventListener("dragover", (e) => {
    e.preventDefault(); e.dataTransfer.dropEffect = busy() ? "none" : "copy";
    if (!busy()) ov.classList.add("show");
  }, true);
  // ウィンドウの外に出た（relatedTarget が無い）ときだけ消す。子要素の間を移るたびに消えないように
  window.addEventListener("dragleave", (e) => { if (!e.relatedTarget) hide(); }, true);
  window.addEventListener("drop", hide, true);
  window.addEventListener("dragend", hide, true);
  // 念のため: ドラッグが終わった後にマウスが動いたら消す（ドラッグ中は mousemove が来ない）
  window.addEventListener("mousemove", hide, true);
  window.addEventListener("blur", hide);

  // 人物の選択
  const frame = $("#pick-frame");
  const toImg = (e) => {
    const img = $("img", frame), r = img.getBoundingClientRect(), sel = state.job.select;
    return [((e.clientX - r.left) / r.width) * sel.width, ((e.clientY - r.top) / r.height) * sel.height];
  };
  frame.addEventListener("click", (e) => { pick.points.push(toImg(e)); pick.labels.push(1); drawPts(); });
  frame.addEventListener("contextmenu", (e) => { e.preventDefault(); pick.points.push(toImg(e)); pick.labels.push(0); drawPts(); });
  const ok = () => { if (pick.labels.includes(1)) api.submit_points(pick.points, pick.labels); };
  const reset = () => { pick.points = []; pick.labels = []; drawPts(); };
  $("#pick-ok").addEventListener("click", ok);
  $("#pick-reset").addEventListener("click", reset);
  $("#pick-cancel").addEventListener("click", () => api.stop());
  window.addEventListener("keydown", (e) => {
    if (!$("#ov-pick").classList.contains("show")) return;
    if (e.key === "Enter") ok();
    else if (e.key === "r" || e.key === "R") reset();
    else if (e.key === "Escape") api.stop();
  });
  document.addEventListener("contextmenu", (e) => { if (!e.target.closest("#pick-frame") && !e.target.closest(".log") && !e.target.closest(".about-text")) e.preventDefault(); });
}

window.addEventListener("pywebviewready", async () => {
  api = window.pywebview.api;
  const r = await api.init();
  stagesDef = r.stages;
  buildStages();
  bind();
  render(r.state);
  ver = r.ver;
  tick();
});
