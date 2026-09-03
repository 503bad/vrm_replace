"""
モーキャプ → shot_NNN/mocap.json（フォーマットは mocap_common.py 参照）

backend:
  mediapipe : MediaPipe Pose Landmarker（CPU, 即動く）。マスク bbox でクロップして推定。
              3D world landmark（メートル, 腰中心）から各ボーンの回転を組み立て、
              画像座標との再投影で腰のカメラ座標位置を解く
  smpl_npz  : 外部で作った shot_NNN/mocap.npz (pose (F,22,3), transl (F,3), K (3,3)) を変換
  gvhmr     : config の gvhmr_cmd で GVHMR demo.py を回し、hmr4d_results.pt を読んで変換（未検証）
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial.transform import Rotation as R, Slerp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_config, shot_dirs, done, mark_done, read_meta, frame_files, ffmpeg_exe  # noqa: E402
from mocap_common import (C, parents_for, rotation_between_frames, smooth_quats,  # noqa: E402
                          smpl_to_mocap, write_mocap, normalize, finger_bones, HAND_SEGMENTS,
                          FINGER_SUFFIXES)

# MediaPipe Hand Landmarker (21 点)
H_WRIST, H_IDX_MCP, H_MID_MCP, H_PINKY_MCP = 0, 5, 9, 17

# ---------------------------------------------------------------- MediaPipe
# 33 landmarks
NOSE, L_EAR, R_EAR = 0, 7, 8
L_SHO, R_SHO, L_ELB, R_ELB, L_WRI, R_WRI = 11, 12, 13, 14, 15, 16
L_PINKY, R_PINKY, L_INDEX, R_INDEX = 17, 18, 19, 20
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK = 23, 24, 25, 26, 27, 28
L_HEEL, R_HEEL, L_TOE, R_TOE = 29, 30, 31, 32
REPROJ_IDX = [NOSE, L_SHO, R_SHO, L_ELB, R_ELB, L_WRI, R_WRI, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK]

# カメラ座標系 (x右 y下 z前) で「カメラを向いて立つ T ポーズ」の基準ベクトル
UP = np.array([0.0, -1.0, 0.0])
LEFT = np.array([1.0, 0.0, 0.0])
FWD = np.array([0.0, 0.0, -1.0])   # 人物の正面（カメラ側）
BACK = -FWD


def default_K(w, h, focal_scale):
    f = focal_scale * max(w, h)
    return np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])


def mask_bbox(mask):
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def run_mediapipe(sd, meta, cfg):
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    opts = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=cfg["mediapipe_model"]),
        running_mode=vision.RunningMode.IMAGE, num_poses=3,
        min_pose_detection_confidence=0.4, min_pose_presence_confidence=0.4,
        output_segmentation_masks=False)
    landmarker = vision.PoseLandmarker.create_from_options(opts)
    use_fingers = bool(cfg.get("use_hands", True)) and bool(cfg.get("use_fingers", True))
    hand_lm = None
    if use_fingers:
        hopts = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=cfg["mediapipe_hand_model"]),
            running_mode=vision.RunningMode.IMAGE, num_hands=2,
            min_hand_detection_confidence=0.3, min_hand_presence_confidence=0.3)
        hand_lm = vision.HandLandmarker.create_from_options(hopts)
    hand_scale = float(cfg.get("hand_crop_scale", 3.2))
    hand_min = int(cfg.get("hand_min_crop_px", 256))

    frames = frame_files(sd)
    F = len(frames)
    W, H = meta["width"], meta["height"]
    img_lm = np.full((F, 33, 2), np.nan, np.float32)
    world_lm = np.full((F, 33, 3), np.nan, np.float32)
    vis = np.zeros((F, 33), np.float32)
    detected = np.zeros(F, bool)
    hand_world = np.full((F, 2, 21, 3), np.nan, np.float32)   # [:, 0]=左手 [:, 1]=右手
    hand_det = np.zeros((F, 2), bool)
    margin = float(cfg["crop_margin"])
    prev_box = None

    def detect_hand(img, side, pose_px, pose_vis):
        """pose の手首まわりを正方形クロップして Hand Landmarker。world landmarks (21,3) or None"""
        wri, idx, pky, thb = ((L_WRI, L_INDEX, L_PINKY, 21) if side == 0 else (R_WRI, R_INDEX, R_PINKY, 22))
        if pose_vis[wri] < 0.5:
            return None
        pw = pose_px[wri]
        size = max(np.linalg.norm(pose_px[i] - pw) for i in (idx, pky, thb)) * hand_scale
        size = float(max(size, 48))
        c = (pw + pose_px[idx] + pose_px[pky]) / 3
        x0, y0 = int(max(0, c[0] - size / 2)), int(max(0, c[1] - size / 2))
        x1, y1 = int(min(W, c[0] + size / 2)), int(min(H, c[1] + size / 2))
        cw, ch = x1 - x0, y1 - y0
        if cw < 16 or ch < 16:
            return None
        crop = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        up = max(1.0, hand_min / max(cw, ch))
        if up > 1.0:
            crop = cv2.resize(crop, (int(cw * up), int(ch * up)), interpolation=cv2.INTER_CUBIC)
        res = hand_lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)))
        best, best_d = None, 0.35 * size
        for lms, wl in zip(res.hand_landmarks, res.hand_world_landmarks):
            hw = np.array([x0 + lms[H_WRIST].x * cw, y0 + lms[H_WRIST].y * ch])
            d = np.linalg.norm(hw - pw)
            if d < best_d:
                best, best_d = wl, d
        if best is None:
            return None
        return np.array([[l.x, l.y, l.z] for l in best], np.float32)

    for i, fp in enumerate(frames):
        m = cv2.imread(str(sd / "mask" / f"{i:06d}.png"), 0)
        bb = mask_bbox(m) if m is not None else None
        if bb is None:
            prev_box = None
            continue
        x0, y0, x1, y1 = bb
        bw, bh = x1 - x0, y1 - y0
        side = max(bw, bh) * (1 + 2 * margin)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        X0, Y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        X1, Y1 = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
        img = cv2.imread(str(fp))
        crop = cv2.cvtColor(img[Y0:Y1, X0:X1], cv2.COLOR_BGR2RGB)
        cw, ch = X1 - X0, Y1 - Y0
        if cw < 32 or ch < 32:
            continue
        res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)))
        if not res.pose_landmarks:
            continue
        # 複数検出時: マスク内に最も多く乗っている人物を採用
        best, best_score = None, -1
        for k, lms in enumerate(res.pose_landmarks):
            px = np.array([[X0 + l.x * cw, Y0 + l.y * ch] for l in lms])
            xi = np.clip(px[:, 0].astype(int), 0, W - 1)
            yi = np.clip(px[:, 1].astype(int), 0, H - 1)
            score = float((m[yi, xi] > 0).mean())
            if score > best_score:
                best, best_score = k, score
        if best_score < 0.3:
            continue
        lms = res.pose_landmarks[best]
        wl = res.pose_world_landmarks[best]
        img_lm[i] = [[X0 + l.x * cw, Y0 + l.y * ch] for l in lms]
        world_lm[i] = [[l.x, l.y, l.z] for l in wl]
        vis[i] = [l.visibility for l in lms]
        detected[i] = True
        if hand_lm is not None:
            for side in (0, 1):
                hw = detect_hand(img, side, img_lm[i], vis[i])
                if hw is not None:
                    hand_world[i, side] = hw
                    hand_det[i, side] = True
        if i % 50 == 0:
            print(f"    frame {i}/{F}", flush=True)
    landmarker.close()
    if hand_lm is not None:
        hand_lm.close()
        print(f"    hands detected: L {int(hand_det[:, 0].sum())}/{F}  R {int(hand_det[:, 1].sum())}/{F}", flush=True)
    return img_lm, world_lm, vis, detected, hand_world, hand_det


def fill_gaps(arr, detected, max_gap):
    """検出できなかったフレームを線形補間。max_gap より長い穴は埋めない。valid を返す。"""
    F = len(arr)
    valid = detected.copy()
    idx = np.where(detected)[0]
    if len(idx) == 0:
        return arr, valid
    flat = arr.reshape(F, -1)
    for j in range(flat.shape[1]):
        flat[:, j] = np.interp(np.arange(F), idx, flat[idx, j])
    # 穴の長さで valid を決める
    i = 0
    while i < F:
        if detected[i]:
            i += 1
            continue
        j = i
        while j < F and not detected[j]:
            j += 1
        if (j - i) <= max_gap and i > 0 and j < F:
            valid[i:j] = True
        i = j
    return flat.reshape(arr.shape), valid


def solve_translation(P, p, w, K):
    """P: (N,3) 腰中心の世界座標 [m], p: (N,2) 画素, w: 重み → 腰中心のカメラ座標 t。
    fx (X+tx) = (u-cx)(Z+tz), fy (Y+ty) = (v-cy)(Z+tz) を t について線形最小二乗。"""
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    A, b = [], []
    for (X, Y, Z), (u, v), wi in zip(P, p, w):
        s = np.sqrt(max(wi, 1e-3))
        A.append(s * np.array([fx, 0, -(u - cx)])); b.append(s * ((u - cx) * Z - fx * X))
        A.append(s * np.array([0, fy, -(v - cy)])); b.append(s * ((v - cy) * Z - fy * Y))
    t, *_ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
    return t


def bone_rotations(P, use_hands):
    """P: (33,3) 平滑化済み world landmarks（カメラ座標系）→ {bone: 3x3 グローバル回転}"""
    mid_sho = (P[L_SHO] + P[R_SHO]) / 2
    mid_hip = (P[L_HIP] + P[R_HIP]) / 2
    mid_ear = (P[L_EAR] + P[R_EAR]) / 2
    torso_up = mid_sho - mid_hip
    hip_left = P[L_HIP] - P[R_HIP]
    sho_left = P[L_SHO] - P[R_SHO]
    Rb = {}
    Rb["hips"] = rotation_between_frames(UP, LEFT, torso_up, hip_left)
    Rb["chest"] = rotation_between_frames(UP, LEFT, torso_up, sho_left)
    Rb["spine"] = rotation_between_frames(UP, LEFT, torso_up, normalize(hip_left) + normalize(sho_left))
    # 頭: 耳と鼻から顔平面
    head_left = P[L_EAR] - P[R_EAR]
    head_fwd = P[NOSE] - mid_ear
    head_up = np.cross(head_fwd, head_left)
    Rb["head"] = rotation_between_frames(UP, LEFT, head_up, head_left)
    sl = Slerp([0, 1], R.from_matrix(np.stack([Rb["chest"], Rb["head"]])))
    Rb["neck"] = sl([0.5]).as_matrix()[0]

    body_fwd = Rb["chest"] @ FWD
    body_back = Rb["hips"] @ BACK

    def limb(side, sho, elb, wri, d_rest, fallback_ref):
        d_up = P[elb] - P[sho]
        d_lo = P[wri] - P[elb]
        axis_meas = np.cross(d_up, d_lo)
        bend = np.linalg.norm(axis_meas) / (np.linalg.norm(d_up) * np.linalg.norm(d_lo) + 1e-9)
        axis_fb = np.cross(d_up, fallback_ref)
        wgt = float(np.clip(bend / 0.35, 0, 1))
        axis = normalize(wgt * normalize(axis_meas) + (1 - wgt) * normalize(axis_fb))
        axis_rest = np.cross(d_rest, fallback_ref_rest[side])
        return (rotation_between_frames(d_rest, axis_rest, d_up, axis),
                rotation_between_frames(d_rest, axis_rest, d_lo, axis), axis, axis_rest)

    fallback_ref_rest = {"arm": FWD, "leg": BACK}
    for pre, sho, elb, wri, d_rest in (("left", L_SHO, L_ELB, L_WRI, LEFT), ("right", R_SHO, R_ELB, R_WRI, -LEFT)):
        up, lo, axis, axis_rest = limb("arm", sho, elb, wri, d_rest, body_fwd)
        Rb[pre + "UpperArm"], Rb[pre + "LowerArm"] = up, lo
        if use_hands:
            idx, pky = (L_INDEX, L_PINKY) if pre == "left" else (R_INDEX, R_PINKY)
            d_hand = (P[idx] + P[pky]) / 2 - P[wri]
            n_hand = np.cross(P[idx] - P[wri], P[pky] - P[wri])
            # rest: 指先 = 腕方向, 掌法線 = 左手 -y / 右手 +y（T ポーズで掌が下向き）
            n_rest = np.array([0, -1.0, 0]) if pre == "left" else np.array([0, 1.0, 0])
            Rb[pre + "Hand"] = rotation_between_frames(d_rest, n_rest, d_hand, n_hand)
    for pre, hip, knee, ank, heel, toe in (("left", L_HIP, L_KNEE, L_ANK, L_HEEL, L_TOE),
                                          ("right", R_HIP, R_KNEE, R_ANK, R_HEEL, R_TOE)):
        up, lo, axis, axis_rest = limb("leg", hip, knee, ank, -UP, body_back)
        Rb[pre + "UpperLeg"], Rb[pre + "LowerLeg"] = up, lo
        d_foot = P[toe] - P[heel]
        u_foot = P[knee] - P[ank]
        Rb[pre + "Foot"] = rotation_between_frames(FWD, UP, d_foot, u_foot)
    return Rb


def hand_rotations(Ph, side, max_splay_deg=20.0):
    """Ph: (21,3) Hand Landmarker の world landmarks（カメラ整列, m）。
    手と指 15 本のグローバル回転（基準 = 手の方向にまっすぐ伸びた指）を返す。
    親指以外は左右への開き（小指側軸 s 方向の成分）を ±max_splay_deg にクランプしてノイズを抑える。"""
    pre = "left" if side == 0 else "right"
    d_can = LEFT if side == 0 else -LEFT
    n_can = np.array([0, -1.0, 0]) if side == 0 else np.array([0, 1.0, 0])   # 基準の掌法線（左右で符号が逆）
    s_can = np.cross(n_can, d_can)                                             # 小指側（両手とも +z）
    d_palm = Ph[H_MID_MCP] - Ph[H_WRIST]
    n = np.cross(Ph[H_IDX_MCP] - Ph[H_WRIST], Ph[H_PINKY_MCP] - Ph[H_WRIST])
    s = np.cross(n, d_palm)
    s_hat = normalize(s - np.dot(s, normalize(d_palm)) * normalize(d_palm))
    lim = math.radians(max_splay_deg)
    out = {pre + "Hand": rotation_between_frames(d_can, s_can, d_palm, s)}
    for suf, (a, b) in HAND_SEGMENTS.items():
        d = Ph[b] - Ph[a]
        if not suf.startswith("Thumb") and max_splay_deg > 0:
            comp = float(np.dot(d, s_hat))
            perp = d - comp * s_hat
            ang = math.atan2(comp, np.linalg.norm(perp) + 1e-9)
            if abs(ang) > lim:
                d = normalize(perp) * math.cos(lim) + s_hat * math.sin(lim) * np.sign(ang)
        out[pre + suf] = rotation_between_frames(d_can, s_can, d, s)
    return out


def mediapipe_backend(sd, meta, cfg):
    W, H, fps = meta["width"], meta["height"], meta["fps"]
    K = default_K(W, H, float(cfg["focal_scale"]))
    img_lm, world_lm, vis, detected, hand_world, hand_det = run_mediapipe(sd, meta, cfg)
    F = len(detected)
    print(f"    detected {int(detected.sum())}/{F} frames", flush=True)
    if detected.sum() == 0:
        raise SystemExit(f"{sd.name}: no person detected")
    max_gap = int(round(fps))   # 1 秒までの欠損は補間
    img_lm, valid = fill_gaps(img_lm, detected, max_gap)
    world_lm, _ = fill_gaps(world_lm, detected, max_gap)
    vis, _ = fill_gaps(vis, detected, max_gap)
    sigma = float(cfg["mocap_smooth_sigma"])
    if sigma > 0 and F > 2:
        img_lm = gaussian_filter1d(img_lm, sigma, axis=0, mode="nearest")
        world_lm = gaussian_filter1d(world_lm, sigma, axis=0, mode="nearest")

    use_hands = bool(cfg.get("use_hands", True))
    use_fingers = use_hands and bool(cfg.get("use_fingers", True)) and hand_det.any()
    hand_valid = np.zeros((F, 2), bool)
    if use_fingers:
        for side in (0, 1):
            if hand_det[:, side].any():
                hw, hv = fill_gaps(hand_world[:, side], hand_det[:, side], max_gap // 2)
                fsig = float(cfg.get("finger_smooth_sigma", sigma * 2))
                if fsig > 0 and F > 2:
                    hw = gaussian_filter1d(hw, fsig, axis=0, mode="nearest")
                hand_world[:, side] = hw
                hand_valid[:, side] = hv

    bones = ["hips", "spine", "chest", "neck", "head",
             "leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm",
             "leftUpperLeg", "leftLowerLeg", "leftFoot", "rightUpperLeg", "rightLowerLeg", "rightFoot"]
    if use_hands:
        bones += ["leftHand", "rightHand"]
    if use_fingers:
        bones += finger_bones("left") + finger_bones("right")
    rot = np.zeros((F, len(bones), 4), np.float32)
    rot[:, :, 3] = 1.0
    hips = np.zeros((F, 3), np.float32)
    for f in range(F):
        P = world_lm[f]
        Rb = bone_rotations(P, use_hands)
        if use_fingers:
            for side in (0, 1):
                if hand_valid[f, side]:
                    Rb.update(hand_rotations(hand_world[f, side], side, float(cfg.get("finger_max_splay_deg", 20.0))))
        rot[f] = R.from_matrix(np.stack([Rb[b] if b in Rb else np.eye(3) for b in bones])).as_quat()
        # world landmark の原点は腰中心。腰中心のカメラ座標を再投影で解く
        t = solve_translation(P[REPROJ_IDX], img_lm[f][REPROJ_IDX], vis[f][REPROJ_IDX], K)
        t[2] = float(np.clip(t[2], 0.3, 100.0))
        hips[f] = t
    rot = smooth_quats(rot, sigma)
    if sigma > 0 and F > 2:
        hips = gaussian_filter1d(hips, sigma, axis=0, mode="nearest")
    np.save(sd / "pose2d.npy", img_lm)   # (F,33,2) 画素座標。デバッグ・可視化用
    # この人物の腰の高さ（脚長 + 足首高さ）。VRM をこれに合わせてスケールすると足元が合う
    det = world_lm[detected]
    leg = np.zeros(len(det))
    for hip, knee, ank in ((L_HIP, L_KNEE, L_ANK), (R_HIP, R_KNEE, R_ANK)):
        leg += np.linalg.norm(det[:, hip] - det[:, knee], axis=1) + np.linalg.norm(det[:, knee] - det[:, ank], axis=1)
    hip_height = float(np.median(leg / 2) + 0.08)
    print(f"    estimated hip height {hip_height:.3f} m", flush=True)
    return {
        "backend": "mediapipe", "fps": fps, "width": W, "height": H, "n_frames": F,
        "K": K.tolist(), "bones": bones, "parents": parents_for(bones),
        "rot": rot, "hips": hips, "valid": valid.tolist(), "hip_height": hip_height,
        # 指データの有無（[左, 右]）。無いフレームは s4 が VRM のレスト（自然な手）のままにする
        "fingers_valid": hand_valid.tolist(),
    }


# ---------------------------------------------------------------- SMPL 系
def smpl_npz_backend(sd, meta, cfg, npz_path):
    d = np.load(npz_path)
    K = d["K"] if "K" in d else default_K(meta["width"], meta["height"], float(cfg["focal_scale"]))
    return smpl_to_mocap(d["pose"], d["transl"], K, meta["fps"], meta["width"], meta["height"],
                         arm_offset_deg=float(cfg.get("arm_offset_deg", 0.0)))


def make_person_only(sd, meta):
    """人物以外を黒くした動画（GVHMR 入力。対象人物だけを見せる）"""
    out = sd / "person_only.mp4"
    if out.exists():
        return out
    po = sd / "person_only"
    po.mkdir(exist_ok=True)
    for i, f in enumerate(frame_files(sd)):
        img = cv2.imread(str(f))
        m = cv2.imread(str(sd / "mask" / f"{i:06d}.png"), 0)
        m = cv2.dilate(m, np.ones((15, 15), np.uint8))
        img[m == 0] = 0
        cv2.imwrite(str(po / f.name), img)
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(meta["fps"]),
                    "-i", str(po / "%06d.jpg"), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    shutil.rmtree(po, ignore_errors=True)
    return out


def gvhmr_backend(sd, meta, cfg, work_name):
    import torch
    if not cfg.get("gvhmr_cmd") or not cfg.get("gvhmr_result"):
        raise SystemExit("config.yaml の gvhmr_cmd / gvhmr_result を設定してください")
    video = make_person_only(sd, meta)
    name = f"{work_name}_{sd.name}"
    cmd = cfg["gvhmr_cmd"].format(video=str(video), name=name)
    print("  $", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)
    res_path = Path(cfg["gvhmr_result"].format(name=name, video=str(video)))
    res = torch.load(res_path, map_location="cpu", weights_only=False)
    p = res["smpl_params_incam"]
    go = p["global_orient"].numpy().reshape(-1, 1, 3)
    bp = p["body_pose"].numpy().reshape(-1, 21, 3)
    pose = np.concatenate([go, bp], axis=1)
    transl = p["transl"].numpy()
    Kf = res["K_fullimg"]
    K = Kf[0].numpy() if Kf.ndim == 3 else Kf.numpy()
    np.savez(sd / "mocap.npz", pose=pose.astype(np.float32), transl=transl.astype(np.float32),
             K=K.astype(np.float32))
    return smpl_npz_backend(sd, meta, cfg, sd / "mocap.npz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    cfg = load_config()
    backend = a.backend or cfg["mocap_backend"]

    for sd in shot_dirs(a.work):
        if done(sd, 2):
            continue
        meta = read_meta(sd)
        print(f"  {sd.name}: {backend}", flush=True)
        if backend == "mediapipe":
            m = mediapipe_backend(sd, meta, cfg)
        elif backend == "smpl_npz":
            m = smpl_npz_backend(sd, meta, cfg, sd / "mocap.npz")
        elif backend == "gvhmr":
            m = gvhmr_backend(sd, meta, cfg, Path(a.work).name)
        else:
            raise SystemExit(f"unknown backend: {backend}")
        n = meta["n_frames"]
        if m["n_frames"] != n:
            print(f"    warning: mocap frames {m['n_frames']} != video frames {n}; padding/trimming", flush=True)
            for k in ("rot", "hips"):
                arr = np.asarray(m[k])
                if len(arr) < n:
                    arr = np.concatenate([arr, np.repeat(arr[-1:], n - len(arr), axis=0)])
                m[k] = arr[:n]
            v = list(m["valid"])[:n]
            m["valid"] = v + [False] * (n - len(v))
            m["n_frames"] = n
        write_mocap(sd / "mocap.json", m)
        mark_done(sd, 2)
        print(f"  {sd.name}: mocap ok ({m['n_frames']} frames)", flush=True)


if __name__ == "__main__":
    main()
