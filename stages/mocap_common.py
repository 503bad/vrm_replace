"""
モーキャプ結果の共通フォーマット（mocap.json）と、そのための数学。

mocap.json
  backend   : 文字列
  fps, width, height, n_frames
  K         : 3x3 カメラ内部パラメータ [px]
  bones     : VRM humanoid ボーン名のリスト（J 個）
  parents   : bones 内の親インデックス（-1 = ルート）
  rot       : F x J x 4 (x, y, z, w)。OpenCV カメラ座標系 (x右 y下 z前) での **グローバル** 回転。
              「カメラの方を向いて立った T ポーズ」からの回転。
  hips      : F x 3 hips の位置（カメラ座標系, m）
  valid     : F 個の bool（人物が見つからないフレームは false → 透明で出力）

s4 側では three.js 座標へ C = diag(1,-1,-1) で変換し、親からの相対回転にして正規化ボーンへ流し込む。
"""
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial.transform import Rotation as R

# VRM humanoid の親子関係（存在しないボーンは飛ばして最も近い祖先に繋ぐ）
HUMANOID_PARENT = {
    "hips": None, "spine": "hips", "chest": "spine", "upperChest": "chest", "neck": "upperChest",
    "head": "neck", "leftShoulder": "upperChest", "leftUpperArm": "leftShoulder",
    "leftLowerArm": "leftUpperArm", "leftHand": "leftLowerArm",
    "rightShoulder": "upperChest", "rightUpperArm": "rightShoulder",
    "rightLowerArm": "rightUpperArm", "rightHand": "rightLowerArm",
    "leftUpperLeg": "hips", "leftLowerLeg": "leftUpperLeg", "leftFoot": "leftLowerLeg",
    "leftToes": "leftFoot", "rightUpperLeg": "hips", "rightLowerLeg": "rightUpperLeg",
    "rightFoot": "rightLowerLeg", "rightToes": "rightFoot",
}

# OpenCV カメラ座標 ↔ SMPL/three 系 (y 上, z 手前) の変換。C = C⁻¹
C = np.diag([1.0, -1.0, -1.0])

# SMPL 22 関節: index → (parent, VRM humanoid bone name)
SMPL = {
    0: (-1, "hips"), 1: (0, "leftUpperLeg"), 2: (0, "rightUpperLeg"), 3: (0, "spine"),
    4: (1, "leftLowerLeg"), 5: (2, "rightLowerLeg"), 6: (3, "chest"),
    7: (4, "leftFoot"), 8: (5, "rightFoot"), 9: (6, "upperChest"),
    10: (7, "leftToes"), 11: (8, "rightToes"), 12: (9, "neck"),
    13: (9, "leftShoulder"), 14: (9, "rightShoulder"), 15: (12, "head"),
    16: (13, "leftUpperArm"), 17: (14, "rightUpperArm"),
    18: (16, "leftLowerArm"), 19: (17, "rightLowerArm"),
    20: (18, "leftHand"), 21: (19, "rightHand"),
}
# SMPL 標準体型の pelvis 関節位置（betas=0, SMPL 座標系）。transl はメッシュ全体の平行移動なので
# pelvis のカメラ座標 = J0 + transl。近似値（本来は betas 依存）
SMPL_J0 = np.array([-0.0018, -0.2233, 0.0282])


def parents_for(bones):
    idx = {b: i for i, b in enumerate(bones)}
    out = []
    for b in bones:
        p = HUMANOID_PARENT[b]
        while p is not None and p not in idx:
            p = HUMANOID_PARENT[p]
        out.append(-1 if p is None else idx[p])
    return out


def normalize(v, eps=1e-9):
    n = np.linalg.norm(v)
    return v / n if n > eps else v


def frame_matrix(d, u):
    """d を第1軸、u を第2軸方向（直交化）とする右手系の直交行列（列ベクトル）。"""
    e1 = normalize(d)
    u2 = u - np.dot(u, e1) * e1
    if np.linalg.norm(u2) < 1e-6:
        # u が d と平行: 適当な直交ベクトル
        tmp = np.array([1.0, 0, 0]) if abs(e1[0]) < 0.9 else np.array([0, 1.0, 0])
        u2 = tmp - np.dot(tmp, e1) * e1
    e2 = normalize(u2)
    e3 = np.cross(e1, e2)
    return np.stack([e1, e2, e3], axis=1)


def rotation_between_frames(d_rest, u_rest, d_cur, u_cur):
    """rest フレームを cur フレームに写す回転行列。"""
    return frame_matrix(d_cur, u_cur) @ frame_matrix(d_rest, u_rest).T


def smooth_quats(q, sigma):
    """q: (F, J, 4)。符号を揃えてからガウス平滑化して正規化。"""
    if sigma <= 0 or len(q) < 3:
        return q
    q = q.copy()
    for f in range(1, len(q)):
        flip = np.sum(q[f] * q[f - 1], axis=-1) < 0
        q[f][flip] *= -1
    q = gaussian_filter1d(q, sigma, axis=0, mode="nearest")
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def smpl_to_mocap(pose, transl, K, fps, width, height, arm_offset_deg=0.0, pelvis_offset=SMPL_J0):
    """SMPL (F,22,3) axis-angle + transl (F,3) [camera coords] → mocap dict。"""
    F = len(pose)
    bones = [SMPL[j][1] for j in range(22)]
    off_l = R.from_euler("z", arm_offset_deg, degrees=True).as_matrix()
    off_r = R.from_euler("z", -arm_offset_deg, degrees=True).as_matrix()
    rot = np.zeros((F, 22, 4), np.float32)
    for f in range(F):
        L = R.from_rotvec(pose[f]).as_matrix()   # (22,3,3)
        L[16] = L[16] @ off_l
        L[17] = L[17] @ off_r
        G = [None] * 22
        for j in range(22):
            p = SMPL[j][0]
            G[j] = L[j] if p < 0 else G[p] @ L[j]
        # 我々の「カメラ向き T ポーズ」基準は SMPL 基準姿勢を C で回したものなので W = G · C
        rot[f] = R.from_matrix(np.stack(G) @ C).as_quat()
    hips = np.asarray(transl, np.float32) + np.asarray(pelvis_offset, np.float32)
    return {
        "backend": "smpl", "fps": fps, "width": width, "height": height, "n_frames": F,
        "K": np.asarray(K, float).tolist(), "bones": bones, "parents": parents_for(bones),
        "rot": rot, "hips": hips, "valid": [True] * F,
    }


def write_mocap(path, m):
    d = dict(m)
    d["rot"] = np.round(np.asarray(m["rot"], np.float64), 5).tolist()
    d["hips"] = np.round(np.asarray(m["hips"], np.float64), 4).tolist()
    d["valid"] = [bool(v) for v in m["valid"]]
    Path(path).write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
