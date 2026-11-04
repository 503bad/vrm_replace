"""人物を消した背景 → cleanplate/%06d.png（元解像度）

オプティカルフロー（NVIDIA Optical Flow = GPU 内蔵の専用ハードウェア）+ OpenCV の穴埋め
（ShiftMap / Telea）。どれも学習データを使わない。

  1. 人物マスクを太らせたものを「使えない画素」、そこからアバターの不透明部分を除いたものを「埋める画素」とする
     （cleanplate_avatar_band。アバターに隠れる部分は埋めなくてよい。工程 4 のレンダーが必要）
  2. 隣のフレームとのフローを求め、人物の上のフローは周囲から補間する
  3. 前のフレームから順に、各画素の「元になる画素（どのフレームのどこか）」を運ぶ（座標で運ぶのでぼけが溜まらない）
     cleanplate_bidirectional なら後ろからも運び、時間的に近い方を使う
  4. どのフレームにも映っていない穴は、キーフレーム（cleanplate_key_interval ごと）で人物の穴全体を
     OpenCV の ShiftMap（周囲の模様をずらして貼る）で埋め、それを以降のフレームへ運ぶ。
     それでも残る穴は各フレームで埋める（小さければ Telea）
  5. 縮小して処理し、埋めた領域だけを元解像度のフレームに貼り戻す

長尺対策: cleanplate_segment_frames ごとに分割して処理する。
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_config, shot_dirs, done, mark_done, read_meta, frame_files, stage_sig  # noqa: E402

DEV = "cuda"
FILL_PENALTY = 100000   # 穴埋めで作った画素より、遠くても実際に映った背景を優先する


# ------------------------------------------------------------------ models
class Flow:
    """オプティカルフロー = NVIDIA Optical Flow（stages/nvof.py、Turing 以降の NVIDIA GPU の専用ハードウェア）。
    NVIDIA 以外の GPU は対象外（起動時に gui.py / run.py が止める）"""

    def __init__(self, cfg, width, height):
        import nvof
        perf = cfg.get("cleanplate_flow_perf", "slow")
        try:
            self.nv = nvof.NvOF(width, height, perf, int(cfg.get("cleanplate_flow_grid", 1)))
        except Exception as e:
            raise SystemExit(f"NVIDIA Optical Flow を初期化できませんでした: {e}")
        self.desc = f"nvof perf={perf} grid={self.nv.grid}"

    @staticmethod
    def _gray(x):
        """(3,H,W) uint8 BGR → (H,W) uint8"""
        return (x[0].float() * 0.114 + x[1].float() * 0.587 + x[2].float() * 0.299).round().clamp(0, 255).byte()

    @torch.inference_mode()
    def __call__(self, a, b):
        """a, b: (B,3,H,W) uint8 BGR → a の各画素が b のどこへ動いたか (B,2,H,W)"""
        return torch.stack([self.nv.flow(self._gray(a[i]), self._gray(b[i])) for i in range(a.shape[0])])

    def close(self):
        self.nv.close()


class Filler:
    """穴埋め。学習データを使わない OpenCV の従来手法だけを使う。
    shiftmap: 周囲の模様をずらして貼る（opencv-contrib の xphoto。大きな穴向き、遅い）
    telea:    周囲から滑らかに伸ばす（OpenCV 本体。速いが大きな穴ではにじむ）"""

    def __init__(self, cfg):
        self.method = cfg.get("cleanplate_fill", "shiftmap")
        self.min_px = int(cfg["cleanplate_fill_min_px"])
        self.calls = 0
        if self.method == "shiftmap" and not hasattr(getattr(cv2, "xphoto", None), "inpaint"):
            print("  [!!] OpenCV contrib（cv2.xphoto.inpaint）が使えないので Telea で埋めます。"
                  "opencv-python と opencv-contrib-python が両方入っていると起きます（setup_env.py を参照）", flush=True)
            self.method = "telea"

    def fill(self, img, hole):
        """img: (H,W,3) uint8 BGR, hole: (H,W) bool → 穴を埋めた img"""
        if not hole.any():
            return img
        self.calls += 1
        if self.method == "telea" or hole.sum() < self.min_px:
            return cv2.inpaint(img, hole.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
        # 穴のまわりだけ切り出す（ShiftMap は面積に比例して遅い）
        H, W = hole.shape
        ys, xs = np.nonzero(hole)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        pad = max(48, (max(y1 - y0, x1 - x0)) // 2)
        y0, y1, x0, x1 = max(0, y0 - pad), min(H, y1 + pad), max(0, x0 - pad), min(W, x1 + pad)
        crop, m = img[y0:y1, x0:x1], hole[y0:y1, x0:x1]
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2Lab)
        dst = lab.copy()
        cv2.xphoto.inpaint(lab, (~m).astype(np.uint8) * 255, dst, cv2.xphoto.INPAINT_SHIFTMAP)   # mask: 0 が穴
        out = cv2.cvtColor(dst, cv2.COLOR_Lab2BGR)
        res = img.copy()
        res[y0:y1, x0:x1][m] = out[m]
        return res


# ------------------------------------------------------------------ flow helpers
def complete_flow(flow, invalid, extra_px=4, scale=4):
    """人物の上（invalid）のフローは人物の動きなので、周囲の背景のフローから補間して置き換える。
    flow: (B,2,H,W), invalid: (B,H,W) bool"""
    B, _, H, W = flow.shape
    sw, sh = max(8, W // scale), max(8, H // scale)
    fs = F.interpolate(flow, size=(sh, sw), mode="area").cpu().numpy()
    ms = F.interpolate(invalid[:, None].float(), size=(sh, sw), mode="area").cpu().numpy()[:, 0] > 0
    k = np.ones((2 * extra_px // scale + 1,) * 2, np.uint8)
    out = []
    for b in range(B):
        m = cv2.dilate(ms[b].astype(np.uint8), k)
        if not m.any():
            out.append(fs[b])
            continue
        if m.all():
            out.append(np.zeros_like(fs[b]))
            continue
        out.append(np.stack([cv2.inpaint(fs[b, c].astype(np.float32), m, 3, cv2.INPAINT_TELEA) for c in range(2)]))
    filled = F.interpolate(torch.from_numpy(np.stack(out)).to(flow.device), size=(H, W), mode="bilinear",
                           align_corners=False)
    return torch.where(invalid[:, None], filled, flow)


def gather(frames, idx, x, y):
    """frames: (N,H,W,3) uint8, idx/x/y: (K,) → (K,3) float の双線形サンプル"""
    N, H, W, _ = frames.shape
    x = x.clamp(0, W - 1.001)
    y = y.clamp(0, H - 1.001)
    x0, y0 = x.floor().long(), y.floor().long()
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    flat = frames.view(-1, 3)
    base = idx.long() * H * W

    def at(yy, xx):
        return flat[base + yy * W + xx].float()
    return (at(y0, x0) * (1 - fx) * (1 - fy) + at(y0, x0 + 1) * fx * (1 - fy)
            + at(y0 + 1, x0) * (1 - fx) * fy + at(y0 + 1, x0 + 1) * fx * fy)


class Propagator:
    """各画素の「元になる画素」(フレーム番号, x, y) を 1 フレームずつ運ぶ"""

    def __init__(self, H, W):
        yy, xx = torch.meshgrid(torch.arange(H, device=DEV, dtype=torch.float32),
                                torch.arange(W, device=DEV, dtype=torch.float32), indexing="ij")
        self.px, self.py = xx, yy
        self.H, self.W = H, W
        self.state = None   # (4,H,W): idx, x, y, valid

    def step(self, t, known, flow):
        """known: (H,W) bool（このフレームで実際に背景が見えている画素）, flow: (2,H,W) t → 前に処理したフレーム"""
        H, W = self.H, self.W
        st = torch.stack([torch.full((H, W), float(t), device=DEV), self.px, self.py, known.float()])
        if self.state is not None:
            qx, qy = self.px + flow[0], self.py + flow[1]
            grid = torch.stack([qx / (W - 1) * 2 - 1, qy / (H - 1) * 2 - 1], -1)[None]
            prev = self.state.clone()
            prev[0] = torch.where(prev[3] > 0, prev[0], torch.full_like(prev[0], -1e4))   # 無効は区別できる値に
            bil = F.grid_sample(prev[None], grid, mode="bilinear", padding_mode="zeros", align_corners=True)[0]
            near = F.grid_sample(prev[None], grid, mode="nearest", padding_mode="zeros", align_corners=True)[0]
            inside = (qx >= 0) & (qx <= W - 1) & (qy >= 0) & (qy <= H - 1)
            # 4 近傍が同じ元フレームなら座標を双線形で、境目なら最近傍で運ぶ
            same = ((bil[0] - near[0]).abs() < 1e-3) & (bil[3] > 0.999)
            valid = inside & (near[3] > 0.5)
            xy = torch.where(same, bil[1:3], near[1:3])
            hole = ~known
            st[0] = torch.where(hole, near[0], st[0])
            st[1:3] = torch.where(hole, xy, st[1:3])
            st[3] = torch.where(hole, valid.float(), st[3])
        self.state = st
        return st

    def mark_known(self, t, mask):
        st = self.state
        st[0][mask] = float(t)
        st[1][mask] = self.px[mask]
        st[2][mask] = self.py[mask]
        st[3][mask] = 1.0


def fetch(frames, fill_mask, st, t, need):
    """need の画素について、運んできた元画素の色と優先度（小さいほど良い）を返す"""
    sel = need & (st[3] > 0.5)
    color = torch.zeros((int(need.sum()), 3), device=DEV)
    score = torch.full((int(need.sum()),), 1e9, device=DEV)
    pos = sel[need]
    if pos.any():
        idx, x, y = st[0][sel], st[1][sel], st[2][sel]
        color[pos] = gather(frames, idx, x, y)
        H, W = need.shape
        li = fill_mask.view(-1)[idx.long() * H * W + y.round().long().clamp(0, H - 1) * W
                                + x.round().long().clamp(0, W - 1)]
        score[pos] = (idx - t).abs() + li.float() * FILL_PENALTY
    return color, score


# ------------------------------------------------------------------ segment
def process_segment(cfg, flow_net, filler, frames_np, invalid_np, need_np):
    """frames_np: (N,H,W,3) uint8 BGR（処理解像度）, invalid/need: (N,H,W) bool → 埋めた (N,H,W,3)"""
    N, H, W, _ = frames_np.shape
    frames = torch.from_numpy(frames_np).to(DEV)   # (N,H,W,3)
    invalid = torch.from_numpy(invalid_np).to(DEV)
    need = torch.from_numpy(need_np).to(DEV)
    fill_mask = torch.zeros((N, H, W), dtype=torch.bool, device=DEV)   # 穴埋めで作った画素
    bidir = bool(cfg["cleanplate_bidirectional"])
    interval = int(cfg["cleanplate_key_interval"])
    bs = int(cfg["cleanplate_flow_batch"])
    out = frames_np.copy()

    def flows(order):
        """order の順に処理するときの (t, t → 直前に処理したフレーム のフロー)。バッチで先読みする"""
        pairs = list(zip(order[1:], order[:-1]))
        yield order[0], None
        for i in range(0, len(pairs), bs):
            chunk = pairs[i:i + bs]
            a = torch.stack([frames[t] for t, _ in chunk]).permute(0, 3, 1, 2)
            b = torch.stack([frames[s] for _, s in chunk]).permute(0, 3, 1, 2)
            fl = complete_flow(flow_net(a, b), torch.stack([invalid[t] for t, _ in chunk]))
            for j, (t, _) in enumerate(chunk):
                yield t, fl[j]

    # 前から運ぶ（穴埋めのキーフレームもここで作る）
    fwd_color = [None] * N
    fwd_score = [None] * N
    prop = Propagator(H, W)
    last_key = -10 ** 9
    for t, fl in flows(list(range(N))):
        known = ~invalid[t]
        st = prop.step(t, known, fl)
        nosrc = st[3] < 0.5
        if (need[t] & nosrc).sum() >= filler.min_px and t - last_key >= interval:
            # 埋める必要がある画素に穴が残っていたら、アバターに隠れる部分も含めて人物の穴全体を埋める
            # （隠れる部分も埋めておかないと、アバターが動いて見えてきたときに以降のフレームへ運べない）
            remain = invalid[t] & nosrc
            img = frames_np[t].copy()
            c, _ = fetch(frames, fill_mask, st, t, invalid[t])
            img[invalid_np[t]] = c.round().clamp(0, 255).byte().cpu().numpy()
            r = remain.cpu().numpy()
            filled = filler.fill(img, r)
            frames_np[t][r] = filled[r]
            frames[t] = torch.from_numpy(frames_np[t]).to(DEV)
            fill_mask[t] |= remain
            prop.mark_known(t, remain)
            last_key = t
        fwd_color[t], fwd_score[t] = fetch(frames, fill_mask, prop.state, t, need[t])

    # 後ろから運ぶ（キーフレームで埋めた画素も「見えている」扱い）
    if bidir:
        prop = Propagator(H, W)
        for t, fl in flows(list(range(N - 1, -1, -1))):
            st = prop.step(t, ~invalid[t] | fill_mask[t], fl)
            c, s = fetch(frames, fill_mask, st, t, need[t])
            better = s < fwd_score[t]
            fwd_color[t] = torch.where(better[:, None], c, fwd_color[t])
            fwd_score[t] = torch.minimum(s, fwd_score[t])

    # 組み立て。どこからも運べなかった画素は各フレームで埋める
    leftover = 0
    for t in range(N):
        img = frames_np[t].copy()
        img[need_np[t]] = fwd_color[t].round().clamp(0, 255).byte().cpu().numpy()
        rem = (fwd_score[t] >= 1e9).cpu().numpy()
        if rem.any():
            hole = np.zeros((H, W), bool)
            hole[need_np[t]] = rem
            leftover += int(hole.sum())
            img = filler.fill(img, hole)
        out[t] = img
    return out, leftover


# ------------------------------------------------------------------ main
def avatar_cover(render_png, margin_px, size):
    """アバターの不透明部分を margin_px だけ内側に縮めたもの（ここは埋めなくてよい）"""
    rgba = cv2.imread(str(render_png), cv2.IMREAD_UNCHANGED)
    if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4:
        return None
    a = (rgba[:, :, 3] >= 250).astype(np.uint8)
    if margin_px > 0:
        a = cv2.erode(a, np.ones((2 * margin_px + 1,) * 2, np.uint8))
    return cv2.resize(a, size, interpolation=cv2.INTER_AREA) >= 1   # 縮小後も全部覆われている画素だけ


def render_newer(sd):
    """レンダー（アバター）が前回のクリーンプレートより新しい → 作り直しが必要"""
    d = sd / ".done_3"
    r = sd / ".done_4"
    return d.exists() and r.exists() and r.stat().st_mtime > d.stat().st_mtime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    cfg = load_config(a.work)
    k = np.ones((cfg["mask_dilate_px"], cfg["mask_dilate_px"]), np.uint8)
    seg_len = int(cfg["cleanplate_segment_frames"])
    band = bool(cfg["cleanplate_avatar_band"])
    print(f"  preset={cfg['preset']} max_side={cfg['cleanplate_max_side']}"
          f"/{cfg['cleanplate_flow_perf']} bidirectional={cfg['cleanplate_bidirectional']} avatar_band={band}"
          f" fill={cfg['cleanplate_fill']}", flush=True)
    flow_net = filler = None
    pool = ThreadPoolExecutor(4)

    for sd in shot_dirs(a.work):
        if done(sd, 3) and not (band and render_newer(sd)):
            continue
        out_dir = sd / "cleanplate"
        out_dir.mkdir(exist_ok=True)
        # 途中まで出来た結果は、同じ設定・同じレンダーで作ったものだけ使い回す（区間単位で再開）
        sig_f = out_dir / "_sig.txt"
        sig = stage_sig(cfg, 3)
        if done(sd, 3) or not sig_f.exists() or sig_f.read_text(encoding="utf-8") != sig:
            if done(sd, 3):
                print(f"  {sd.name}: render changed, redoing cleanplate", flush=True)
                (sd / ".done_3").unlink()
            for f in out_dir.glob("*.png"):
                f.unlink()
            sig_f.write_text(sig, encoding="utf-8")
        meta = read_meta(sd)
        frames = frame_files(sd)
        n = len(frames)
        w, h = meta["width"], meta["height"]
        s = min(1.0, cfg["cleanplate_max_side"] / max(w, h))
        rw, rh = max(8, int(w * s) // 8 * 8), max(8, int(h * s) // 8 * 8)
        if flow_net is None or (flow_net.size != (rw, rh)):
            if flow_net is not None:
                flow_net.close()
            flow_net = Flow(cfg, rw, rh)
            flow_net.size = (rw, rh)
            print(f"  {sd.name}: flow {flow_net.desc} at {rw}x{rh}", flush=True)
        if filler is None:
            filler = Filler(cfg)
        margin = int(cfg["cleanplate_band_margin_px"])
        use_band = band and (sd / "render" / "f0000.png").exists()
        if band and not use_band:
            print(f"  {sd.name}: render/ が無いのでアバターで隠れる部分も埋めます", flush=True)

        t0 = time.time()
        for seg_start in range(0, n, seg_len):
            seg_end = min(n, seg_start + seg_len)
            if all((out_dir / f"{i:06d}.png").exists() for i in range(seg_start, seg_end)):
                continue
            ts = time.time()
            idx = list(range(seg_start, seg_end))
            src = list(pool.map(lambda i: cv2.imread(str(frames[i])), idx))
            dmask = list(pool.map(lambda i: cv2.dilate(cv2.imread(str(sd / "mask" / f"{i:06d}.png"), 0), k), idx))
            fr = np.stack([cv2.resize(x, (rw, rh), interpolation=cv2.INTER_AREA) for x in src])
            inv = np.stack([cv2.resize(m, (rw, rh), interpolation=cv2.INTER_AREA) > 0 for m in dmask])
            need = inv.copy()
            if use_band:
                for j, i in enumerate(idx):
                    cov = avatar_cover(sd / "render" / f"f{i:04d}.png", margin, (rw, rh))
                    if cov is not None:
                        need[j] &= ~cov
            filled, leftover = process_segment(cfg, flow_net, filler, fr, inv, need)

            # 埋めた領域だけを元フレームに貼り戻す（縮小処理による背景全体の劣化を防ぐ）
            def paste(j):
                i = idx[j]
                o = src[j]
                inp = cv2.resize(filled[j], (w, h), interpolation=cv2.INTER_CUBIC)
                m = cv2.resize(need[j].astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_LINEAR)
                m = np.maximum(m, cv2.GaussianBlur(cv2.dilate(m, np.ones((7, 7), np.uint8)), (0, 0), 3))
                m = m.astype(np.float32)[:, :, None] / 255.0
                comp = (o * (1 - m) + inp * m).round().astype(np.uint8)
                cv2.imwrite(str(out_dir / f"{i:06d}.png"), comp, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            list(pool.map(paste, range(len(idx))))
            dt = time.time() - ts
            print(f"  {sd.name}: cleanplate {seg_start}-{seg_end}/{n}  {dt / len(idx):.3f}s/frame"
                  f"  fill {need.sum() / inv.sum() * 100 if inv.sum() else 0:.0f}% of mask"
                  f"  fill {filler.method} x{filler.calls}  leftover {leftover}px"
                  f"  vram {torch.cuda.max_memory_allocated() / 1e9:.1f}GB", flush=True)
            torch.cuda.empty_cache()
        mark_done(sd, 3, cfg)
        print(f"  {sd.name}: cleanplate ok ({time.time() - t0:.1f}s)", flush=True)

    # CUDA のモデルを使ったあと、モデルの破棄やプロセス終了時の後片付けで異常終了する（0xC0000409 / 127）
    # ことがあった（LaMa の TorchScript で発生）。結果はすべて書き出し済みなので、後片付けを飛ばして正常終了する
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
