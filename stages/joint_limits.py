"""
人間の可動域による関節制限。

mocap.json のグローバル回転（OpenCV カメラ座標, T ポーズ基準）を親相対の回転に直し、
ボーン軸まわりの swing/twist に分解して角度をクランプ、FK でグローバルに戻す。
子ボーンは「親相対の角度」を保つので、親がクランプされれば子もついて動く。

各ボーンの定義（T ポーズ, cv 座標: x=人物の左, y=下, z=後ろ）
  d    : ボーンのレスト方向
  e    : 曲げ角 bend が正のときにボーン先端が向かう方向（d に直交）
  h    : e × d。side はこの方向への持ち上げ角
  bend : (min, max) [deg]   side: (min, max)   twist: (min, max) ボーン軸まわり
"""
import numpy as np
from scipy.spatial.transform import Rotation as R

X, Y, Z = np.eye(3)
FWD, BACK, DOWN, UP = -Z, Z, Y, -Y

LIMITS = {}


def _lim(name, d, e, bend, side, twist):
    LIMITS[name] = (np.asarray(d, float), np.asarray(e, float),
                    np.radians(bend), np.radians(side), np.radians(twist))


# 体幹・首・頭（bend 正 = 前屈）
_lim("spine", UP, FWD, (-20, 35), (-25, 25), (-35, 35))
_lim("chest", UP, FWD, (-20, 35), (-25, 25), (-35, 35))
_lim("upperChest", UP, FWD, (-15, 20), (-15, 15), (-20, 20))
_lim("neck", UP, FWD, (-25, 30), (-25, 25), (-40, 40))
_lim("head", UP, FWD, (-25, 30), (-25, 25), (-40, 40))
for side, sx in (("left", X), ("right", -X)):
    # 肩: 手（腕の先端）が前へ向かう bend が正。後ろは 60° まで。twist は上腕の内外旋
    _lim(f"{side}Shoulder", sx, FWD, (-20, 20), (-20, 30), (-15, 15))
    _lim(f"{side}UpperArm", sx, FWD, (-60, 175), (-95, 95), (-100, 100))
    # 肘: 前へ曲がる。逆方向（過伸展）は 5° まで
    _lim(f"{side}LowerArm", sx, FWD, (-5, 150), (-15, 15), (-90, 90))
    # 手首: 掌側（下）へ曲がるのが正。twist は前腕の回内外を吸収するので広め
    _lim(f"{side}Hand", sx, DOWN, (-70, 80), (-30, 30), (-110, 110))
    # 股関節: 前へ上げるのが正。side は h = e×d = (1,0,0) 方向（左脚は外転、右脚は内転）
    ab = (-30, 60) if side == "left" else (-60, 30)
    _lim(f"{side}UpperLeg", DOWN, FWD, (-30, 130), ab, (-50, 50))
    # 膝: 後ろへ曲がる
    _lim(f"{side}LowerLeg", DOWN, BACK, (-5, 150), (-15, 15), (-30, 30))
    # 足首: つま先が下がるのが正
    _lim(f"{side}Foot", FWD, DOWN, (-30, 50), (-20, 20), (-25, 25))
    _lim(f"{side}Toes", FWD, DOWN, (-40, 40), (-10, 10), (-10, 10))
    # 指（基準 = 手の方向にまっすぐ）。握る向き = 掌側（下）
    for fn in ("Index", "Middle", "Ring", "Little"):
        _lim(f"{side}{fn}Proximal", sx, DOWN, (-15, 100), (-20, 20), (-15, 15))
        _lim(f"{side}{fn}Intermediate", sx, DOWN, (-5, 110), (-6, 6), (-6, 6))
        _lim(f"{side}{fn}Distal", sx, DOWN, (-5, 90), (-6, 6), (-6, 6))
    # 親指: 中手骨は前（h 方向）へ大きく開く
    _lim(f"{side}ThumbMetacarpal", sx, DOWN, (-25, 80), (-10, 85), (-45, 45))
    _lim(f"{side}ThumbProximal", sx, DOWN, (-15, 90), (-25, 25), (-25, 25))
    _lim(f"{side}ThumbDistal", sx, DOWN, (-10, 90), (-15, 15), (-15, 15))


def _rot_between(a, b):
    """単位ベクトル a → b の最小回転行列"""
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    s = np.linalg.norm(v)
    if s < 1e-9:
        if c > 0:
            return np.eye(3)
        # 180°: a に直交する任意軸
        axis = np.cross(a, X if abs(a[0]) < 0.9 else Y)
        axis /= np.linalg.norm(axis)
        return R.from_rotvec(axis * np.pi).as_matrix()
    return R.from_rotvec(v / s * np.arctan2(s, c)).as_matrix()


def clamp_local(Rl, d, e, bend_lim, side_lim, twist_lim):
    """親相対回転 Rl を可動域にクランプして返す。(Rl', 変更されたか)"""
    h = np.cross(e, d)
    dp = Rl @ d
    x, y, z = float(dp @ d), float(dp @ e), float(dp @ h)
    bend = np.arctan2(y, x)
    side = np.arcsin(np.clip(z, -1.0, 1.0))
    swing = _rot_between(d, dp)
    tw = swing.T @ Rl                       # d まわりの回転
    rv = R.from_matrix(tw).as_rotvec()
    twist = float(np.dot(rv, d))
    nb = float(np.clip(bend, *bend_lim))
    ns = float(np.clip(side, *side_lim))
    nt = float(np.clip(twist, *twist_lim))
    if abs(nb - bend) < 1e-6 and abs(ns - side) < 1e-6 and abs(nt - twist) < 1e-6:
        return Rl, False
    dp2 = np.cos(ns) * (np.cos(nb) * d + np.sin(nb) * e) + np.sin(ns) * h
    swing2 = _rot_between(d, dp2)
    twist2 = R.from_rotvec(d * nt).as_matrix()
    return swing2 @ twist2, True


def topo_order(parents):
    order, seen = [], set()

    def visit(j):
        if j in seen:
            return
        if parents[j] >= 0:
            visit(parents[j])
        seen.add(j)
        order.append(j)
    for j in range(len(parents)):
        visit(j)
    return order


def apply_joint_limits(rot, bones, parents, skip=()):
    """rot: (F, J, 4) xyzw グローバル回転 → クランプ後の (F, J, 4)。戻り値 2 つ目はボーン別のクランプ回数"""
    rot = np.asarray(rot, np.float64)
    F, J = rot.shape[:2]
    order = topo_order(parents)
    lims = [LIMITS.get(b) if b not in skip else None for b in bones]
    out = rot.copy()
    counts = np.zeros(J, int)
    for f in range(F):
        G = R.from_quat(rot[f]).as_matrix()
        Gn = G.copy()
        for j in order:
            p = parents[j]
            if p < 0:
                continue
            Rl = G[p].T @ G[j]
            if lims[j] is not None:
                Rl, changed = clamp_local(Rl, *lims[j])
                counts[j] += changed
            Gn[j] = Gn[p] @ Rl
        out[f] = R.from_matrix(Gn).as_quat()
    return out.astype(np.float32), counts
