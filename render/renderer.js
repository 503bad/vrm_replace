/**
 * ブラウザ側。s4_threevrm.js から page.evaluate で呼ばれる。
 *
 * 座標系:
 *   mocap.json の回転 rot[f][j] は OpenCV カメラ座標 (x右 y下 z前) でのグローバル回転（T ポーズ基準）。
 *   three.js は y上 z手前（カメラは原点で -z を向く）。変換 C = diag(1,-1,-1) は x 軸まわり 180° 回転なので
 *   四元数 (x,y,z,w)_cv → (x,-y,-z,w)_three、位置 (x,y,z)_cv → (x,-y,-z)_three。
 * ボーン:
 *   three-vrm の正規化ボーン（getNormalizedBoneNode）はレスト時のワールド回転が identity なので、
 *   各ボーンの local = inv(W_parent) · W_bone（parent = mocap と VRM の両方に存在する最も近い祖先）。
 *   hips は vrm.scene の回転（VRM0.x は rotateVRM0 で 180° 回る）を打ち消してから入れる。
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, VRMHumanBoneParentMap } from "@pixiv/three-vrm";

const S = { canvas: document.getElementById("c") };

window.probeGPU = () => {
  const gl = S.canvas.getContext("webgl2", { alpha: true, antialias: true, premultipliedAlpha: false, preserveDrawingBuffer: true });
  S.gl = gl;
  if (!gl) return { renderer: "none", vendor: "none", webgl2: false };
  const dbg = gl.getExtension("WEBGL_debug_renderer_info");
  return {
    renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
    webgl2: true,
  };
};

window.initRenderer = async ({ vrmUrl, hipHeight, lightDir, lightIntensity, ambient }) => {
  const renderer = new THREE.WebGLRenderer({
    canvas: S.canvas, context: S.gl || undefined, alpha: true, antialias: true,
    premultipliedAlpha: false, preserveDrawingBuffer: true,
  });
  renderer.setPixelRatio(1);
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  S.renderer = renderer;

  const scene = new THREE.Scene();
  S.scene = scene;
  const dir = new THREE.DirectionalLight(0xffffff, lightIntensity);
  dir.position.set(lightDir[0], lightDir[1], lightDir[2]).normalize();
  scene.add(dir);
  scene.add(new THREE.AmbientLight(0xffffff, ambient));

  const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 200);
  camera.position.set(0, 0, 0);
  S.camera = camera;

  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(vrmUrl);
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error("not a VRM file");
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  if (VRMUtils.combineSkeletons) VRMUtils.combineSkeletons(gltf.scene);
  else if (VRMUtils.removeUnnecessaryJoints) VRMUtils.removeUnnecessaryJoints(gltf.scene);
  VRMUtils.rotateVRM0(vrm);            // 0.x は +Z を向くように 180° 回転（1.0 は何もしない）
  vrm.scene.traverse((o) => { o.frustumCulled = false; });
  scene.add(vrm.scene);
  S.vrm = vrm;

  // レスト時の腰の高さ → スケール（hipHeight が "auto" のときは各ショットの mocap.hip_height を使う）
  vrm.humanoid.resetNormalizedPose();
  vrm.scene.updateMatrixWorld(true);
  const hipsNode = vrm.humanoid.getNormalizedBoneNode("hips");
  const hipsWorld = hipsNode.getWorldPosition(new THREE.Vector3());
  // mocap の hips は左右股関節の中点なので、VRM 側も upperLeg の高さで合わせる
  const legTops = ["leftUpperLeg", "rightUpperLeg"].map((n) => vrm.humanoid.getNormalizedBoneNode(n))
    .filter(Boolean).map((nd) => nd.getWorldPosition(new THREE.Vector3()).y);
  const legTop = legTops.length ? legTops.reduce((a, b) => a + b, 0) / legTops.length : hipsWorld.y;
  const restHip = Math.max(legTop, 0.05);
  S.restHip = restHip;
  S.hipsAboveLeg = hipsWorld.y - legTop;   // 骨盤中心が股関節よりどれだけ上か（VRM 単位）
  S.hipHeightArg = hipHeight;
  const scale = setScale(typeof hipHeight === "number" ? hipHeight : 0.93);
  S.sceneQuatInv = vrm.scene.quaternion.clone().invert();
  S.hipsRestLocal = hipsNode.position.clone();

  const bones = [];
  for (const name of Object.keys(VRMHumanBoneParentMap)) {
    if (vrm.humanoid.getNormalizedBoneNode(name)) bones.push(name);
  }
  S.vrmBones = new Set(bones);
  const springs = vrm.springBoneManager ? vrm.springBoneManager.joints.size : 0;
  return { specVersion: vrm.meta?.metaVersion ?? "?", hipHeight: restHip, scale, springs, bones };
};

function setScale(hipHeight) {
  const scale = hipHeight / S.restHip;
  S.vrm.scene.scale.setScalar(scale);
  S.scale = scale;
  S.hipHeight = hipHeight;
  return scale;
}

/** K (3x3, px) から OpenGL 射影行列。主点ずれも反映 */
function projectionFromK(K, w, h, near, far) {
  const fx = K[0][0], fy = K[1][1], cx = K[0][2], cy = K[1][2];
  const m = new THREE.Matrix4();
  m.set(
    2 * fx / w, 0, (w - 2 * cx) / w, 0,
    0, 2 * fy / h, (2 * cy - h) / h, 0,
    0, 0, -(far + near) / (far - near), -2 * far * near / (far - near),
    0, 0, -1, 0,
  );
  return m;
}

window.loadShot = async ({ mocapUrl, width, height, warmup }) => {
  const mocap = await (await fetch(mocapUrl)).json();
  S.mocap = mocap;
  S.dt = 1 / mocap.fps;
  if (S.hipHeightArg === "auto" && typeof mocap.hip_height === "number") setScale(mocap.hip_height);
  S.renderer.setSize(width, height, false);
  S.canvas.width = width; S.canvas.height = height;
  S.camera.aspect = width / height;
  S.camera.projectionMatrix.copy(projectionFromK(mocap.K, width, height, 0.05, 200));
  S.camera.projectionMatrixInverse.copy(S.camera.projectionMatrix).invert();

  // mocap ボーン → VRM 正規化ノード。親は mocap にも VRM にも存在する最も近い祖先
  const vrm = S.vrm;
  const used = [];
  const nameIdx = new Map(mocap.bones.map((b, i) => [b, i]));
  for (let j = 0; j < mocap.bones.length; j++) {
    const name = mocap.bones[j];
    const node = vrm.humanoid.getNormalizedBoneNode(name);
    if (!node) continue;
    let p = mocap.parents[j];
    while (p >= 0 && !vrm.humanoid.getNormalizedBoneNode(mocap.bones[p])) p = mocap.parents[p];
    used.push({ j, name, node, parent: p });
  }
  S.used = used;
  S.W = mocap.bones.map(() => new THREE.Quaternion());

  vrm.humanoid.resetNormalizedPose();
  const first = mocap.valid.findIndex((v) => v);
  if (first >= 0) {
    applyPose(first);
    vrm.humanoid.update();
    vrm.scene.updateMatrixWorld(true);
    if (vrm.springBoneManager) vrm.springBoneManager.reset();
    for (let i = 0; i < warmup; i++) vrm.update(S.dt);
  }
  return { nFrames: mocap.n_frames, fps: mocap.fps, usedBones: used.length, hipHeight: S.hipHeight, scale: S.scale };
};

const _q = new THREE.Quaternion();
const _v = new THREE.Vector3();
const _off = new THREE.Vector3();

function applyPose(f) {
  const { mocap, used, W, vrm } = S;
  const rot = mocap.rot[f];
  for (let j = 0; j < mocap.bones.length; j++) {
    const r = rot[j];
    W[j].set(r[0], -r[1], -r[2], r[3]).normalize();   // cv → three
  }
  for (const u of used) {
    if (u.parent < 0) {
      u.node.quaternion.copy(S.sceneQuatInv).multiply(W[u.j]);
    } else {
      u.node.quaternion.copy(W[u.parent]).invert().multiply(W[u.j]);
    }
  }
  const hips = vrm.humanoid.getNormalizedBoneNode("hips");
  const t = mocap.hips[f];
  _v.set(t[0], -t[1], -t[2]);                                  // 股関節中点（ワールド, m）
  const hipsIdx = used.find((u) => u.name === "hips");
  if (hipsIdx) _v.add(_off.set(0, S.hipsAboveLeg * S.scale, 0).applyQuaternion(W[hipsIdx.j]));
  _v.divideScalar(S.scale).applyQuaternion(S.sceneQuatInv);
  hips.position.copy(_v);
}

window.renderFrame = (f) => {
  const { mocap, vrm, renderer, scene, camera } = S;
  const valid = mocap.valid[f];
  vrm.scene.visible = !!valid;
  if (valid) {
    applyPose(f);
    vrm.update(S.dt);
  }
  renderer.render(scene, camera);
  return S.canvas.toDataURL("image/png");
};

window.renderBatch = (from, to) => {
  const out = [];
  for (let f = from; f < to; f++) out.push(window.renderFrame(f));
  return out;
};

window.vrmReplaceReady = true;
