import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// Presentation only: tracking is 2D. Layout (x, y) in metres maps to three.js (x, height, y), so the cameras sit on the
// low-y side looking toward +y, matching the 2D map's 180-degree turn (app.js fitView). three.js
// (server/static/lib/three, r187) is bundled so the dashboard works offline.
const FT = 0.3048;
const PERSON_H = 4.0 * FT;
const BOARD_H = 2.5 * FT;
const BG = 0x090b16;
const GREY = 0x7a819c;
const FURNITURE = { Bed: 0x7d6bb0, Desk: 0x8c6c4a, Table: 0x8c6c4a, Chair: 0x50698a, Bookshelf: 0x7c5c3a, Dresser: 0x6c5242, Cabinet: 0x6c5242, Sofa: 0x5c6c94, TV: 0x3a3f52 };
const STATUS = { online: 0x8fdcb2, stale: 0xe6b24c, offline: 0xee6a73 };
const GREY_C = new THREE.Color(GREY);
// Render cadence: full rate while the camera or a person moves, IDLE_MS between frames for the slow pulses alone.
const IDLE_MS = 33;
// A person must move this far (m) before the shadow map is redrawn; the room itself casts static shadows.
const SHADOW_MOVE_M = 0.005;
// Smoothed rAF interval (ms) above which quality steps down (pixel ratio, then shadow resolution), held SLOW_S s.
const SLOW_MS = 26, SLOW_S = 2;

function labelSprite(text, { size = 0.16, color = "#e7eaf6", bg = "rgba(12,15,28,0.78)" } = {}) {
  const canvas = document.createElement("canvas"), g = canvas.getContext("2d"), px = 48;
  const font = `600 ${px}px system-ui, sans-serif`;
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 28;
  canvas.width = w;
  canvas.height = px + 22;
  g.font = font;
  g.fillStyle = bg;
  g.beginPath(); g.roundRect(0, 0, w, canvas.height, 14); g.fill();
  g.fillStyle = color; g.textBaseline = "middle"; g.fillText(text, 14, canvas.height / 2 + 2);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  sprite.scale.set((size * w) / canvas.height, size, 1);
  sprite.renderOrder = 10;
  return sprite;
}

const flatDisc = (geo, mat) => { const m = new THREE.Mesh(geo, mat); m.rotation.x = -Math.PI / 2; return m; };
const vline = (y0, y1, mat) => new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, y0, 0), new THREE.Vector3(0, y1, 0)]), mat);

class RoomView {
  constructor(el) {
    this.el = el;
    const r = (this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" }));
    this.pixelRatio = Math.min(1.5, window.devicePixelRatio || 1);
    r.setPixelRatio(this.pixelRatio);
    r.localClippingEnabled = true;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFShadowMap;
    r.shadowMap.autoUpdate = false;
    r.shadowMap.needsUpdate = true;
    el.appendChild(r.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(BG);
    this.scene.fog = new THREE.Fog(BG, 12, 30);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 100);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;

    this.scene.add(new THREE.HemisphereLight(0xd6dcff, 0x0e1020, 1.1));
    this.sun = new THREE.DirectionalLight(0xffffff, 1.4);
    this.sun.position.set(2, 6, 3);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(1024, 1024);
    this.scene.add(this.sun, this.sun.target);

    this.roomG = new THREE.Group();
    this.boardsG = new THREE.Group();
    this.peopleG = new THREE.Group();
    this.scene.add(this.roomG, this.boardsG, this.peopleG);
    this.people = new Map();
    this.boards = new Map();
    this.roomSig = "";
    this.visible = false;
    this.onScreen = true;
    this.start = performance.now();
    this.lastTick = 0;
    this.lastRender = 0;
    this.frameMs = 16.7;
    this.slowFor = 0;
    this.dirty = true;
    this.controls.addEventListener("change", () => { this.dirty = true; });
    new ResizeObserver(() => { this.resize(); this.dirty = true; }).observe(el);
    // The implicit root is the top-level viewport, so this also pauses an iframed view scrolled off the page.
    new IntersectionObserver(([e]) => { this.onScreen = !e || e.isIntersecting; }).observe(el);
    this.resize();
    r.setAnimationLoop(() => this.frame());
  }

  resize() {
    const w = this.el.clientWidth || 1, h = this.el.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  setVisible(on) { this.visible = on; if (on) { this.resize(); this.dirty = true; } }

  async setRoom(layout, scan) {
    const sig = JSON.stringify([layout.polygon, scan && scan.source, scan && scan.transform]);
    if (sig === this.roomSig) return;
    this.roomSig = sig;
    this.roomG.clear();
    const xs = layout.polygon.map((p) => p[0]), ys = layout.polygon.map((p) => p[1]);
    const b = (this.bounds = { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) });
    const cx = (b.x0 + b.x1) / 2, cz = (b.y0 + b.y1) / 2, span = Math.max(b.x1 - b.x0, b.y1 - b.y0);

    this.sun.position.set(cx + 1.5, 6, cz + 2.5);
    this.sun.target.position.set(cx, 0, cz);
    this.scene.fog.near = Math.max(12, span * 1.8);
    this.scene.fog.far = Math.max(30, span * 4.5);
    const half = Math.max(6, span * 0.8), cam = this.sun.shadow.camera;
    Object.assign(cam, { left: -half, right: half, top: half, bottom: -half, far: Math.max(50, span * 4) });
    cam.updateProjectionMatrix();
    this.home = { target: new THREE.Vector3(cx, 0.6, cz), pos: new THREE.Vector3(cx - span * 0.15, span * 1.15, cz - span * 1.25) };
    if (!this.cameraPlaced) { this.resetView(); this.cameraPlaced = true; }

    const grid = [];
    for (let f = 0; f * FT <= b.x1 + 1e-6; f++) grid.push(f * FT, 0.004, b.y0, f * FT, 0.004, b.y1);
    for (let f = 0; f * FT <= b.y1 + 1e-6; f++) grid.push(b.x0, 0.004, f * FT, b.x1, 0.004, f * FT);
    const gridGeo = new THREE.BufferGeometry();
    gridGeo.setAttribute("position", new THREE.Float32BufferAttribute(grid, 3));
    this.roomG.add(new THREE.LineSegments(gridGeo, new THREE.LineBasicMaterial({ color: 0x252d4a, transparent: true, opacity: 0.7 })));

    if (scan && scan.transform) {
      try { await this.loadScan(scan, b); this.renderer.shadowMap.needsUpdate = true; return; }
      catch (e) { console.warn("room scan failed to load, drawing the outline instead", e); this.roomG.clear(); }
    }
    this.outlineRoom(layout.polygon);
    this.renderer.shadowMap.needsUpdate = true;
  }

  outlineRoom(poly) {
    const floor = new THREE.Mesh(new THREE.ShapeGeometry(new THREE.Shape(poly.map((p) => new THREE.Vector2(p[0], p[1])))),
      new THREE.MeshStandardMaterial({ color: 0x181e34, roughness: 0.95 }));
    floor.rotation.x = Math.PI / 2;
    floor.receiveShadow = true;
    this.roomG.add(floor);
    const wallMat = new THREE.MeshStandardMaterial({ color: 0x95a0c8, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false });
    poly.forEach((a, i) => {
      const c = poly[(i + 1) % poly.length];
      const wall = new THREE.Mesh(new THREE.PlaneGeometry(Math.hypot(c[0] - a[0], c[1] - a[1]), 2.4), wallMat);
      wall.position.set((a[0] + c[0]) / 2, 1.2, (a[1] + c[1]) / 2);
      wall.rotation.y = -Math.atan2(c[1] - a[1], c[0] - a[0]);
      this.roomG.add(wall);
    });
  }

  // Room-mode scan from tools/import_scan.py, placed by room.json's transform (rotation.y = yaw, then shifted). Other
  // rooms in it are hidden through keep_nodes and clipping planes at this room's bounds.
  async loadScan(scan, b) {
    const gltf = await new GLTFLoader().loadAsync("/scan.glb");
    const root = gltf.scene, t = scan.transform, margin = 0.12;
    const holder = new THREE.Group();
    root.rotation.y = t.yaw || 0;
    holder.position.set(-t.x0, -t.floor_y, -t.z0);
    holder.add(root);
    const clip = [
      new THREE.Plane(new THREE.Vector3(1, 0, 0), -(b.x0 - margin)), new THREE.Plane(new THREE.Vector3(-1, 0, 0), b.x1 + margin),
      new THREE.Plane(new THREE.Vector3(0, 0, 1), -(b.y0 - margin)), new THREE.Plane(new THREE.Vector3(0, 0, -1), b.y1 + margin),
    ];
    const keep = new Set(scan.keep_nodes), byName = {};
    for (const o of scan.objects || []) byName[o.name] = o;
    this.roomG.add(holder);
    holder.updateMatrixWorld(true);
    const box = new THREE.Box3();
    const room = new THREE.Box3(new THREE.Vector3(b.x0 - 0.3, -1, b.y0 - 0.3), new THREE.Vector3(b.x1 + 0.3, 4, b.y1 + 0.3));
    const meshes = [];
    root.traverse((o) => { if (o.isMesh) meshes.push(o); });
    // Identical looks share one material and each edge style is one merged world-space line object, which keeps
    // draw calls and shader switches down on integrated GPUs. RoomPlan exports have no normals, so shade flat.
    const mats = new Map(), edges = { wall: [], other: [] };
    const shared = (key, make) => {
      let m = mats.get(key);
      if (!m) { m = make(); m.clippingPlanes = clip; m.flatShading = true; mats.set(key, m); }
      return m;
    };
    for (const mesh of meshes) {
      const name = mesh.name || mesh.parent?.name || "";
      box.setFromObject(mesh);
      const wall = /^(Wall|Joint)_/.test(name), opening = /^(Door|Window|Opening)_/.test(name), isWindow = /^Window/.test(name);
      if (!(keep.has(name) || (wall && box.intersectsBox(room))) || /^Ceiling/.test(name)) { mesh.visible = false; continue; }
      let mat;
      if (/^Floor_/.test(name)) mat = shared("floor", () => new THREE.MeshStandardMaterial({ color: 0x1a2038, roughness: 0.95 }));
      else if (wall) mat = shared("wall", () => new THREE.MeshStandardMaterial({ color: 0xa3aed4, transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide }));
      else if (opening) mat = shared(isWindow ? "window" : "door", () => new THREE.MeshStandardMaterial({ color: isWindow ? 0x62d2e0 : 0xe6b24c, emissive: isWindow ? 0x0d3a40 : 0x3a2a0d, transparent: true, opacity: 0.22, depthWrite: false, side: THREE.DoubleSide }));
      else {
        const color = FURNITURE[byName[name]?.label] ?? 0x6c7896;
        mat = shared("f" + color, () => new THREE.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0.05 }));
        mesh.castShadow = true;
      }
      mesh.material = mat;
      mesh.receiveShadow = true;
      const eg = new THREE.EdgesGeometry(mesh.geometry, 25);
      eg.applyMatrix4(mesh.matrixWorld);
      edges[wall ? "wall" : "other"].push(eg.attributes.position.array);
      eg.dispose();
    }
    for (const [kind, parts] of Object.entries(edges)) {
      if (!parts.length) continue;
      const all = new Float32Array(parts.reduce((n, a) => n + a.length, 0));
      let at = 0;
      for (const a of parts) { all.set(a, at); at += a.length; }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(all, 3));
      const wall = kind === "wall";
      this.roomG.add(new THREE.LineSegments(geo,
        new THREE.LineBasicMaterial({ color: wall ? 0xa3aed4 : 0xdde2f2, transparent: true, opacity: wall ? 0.35 : 0.25, clippingPlanes: clip })));
    }
    for (const o of scan.objects || []) {
      const fx = o.footprint.map((p) => p[0]), fy = o.footprint.map((p) => p[1]);
      const tag = labelSprite(o.label, { size: 0.12, color: "#cdd3e8", bg: "rgba(12,15,28,0.55)" });
      tag.position.set((Math.min(...fx) + Math.max(...fx)) / 2, o.height + 0.15, (Math.min(...fy) + Math.max(...fy)) / 2);
      this.roomG.add(tag);
    }
  }

  resetView() {
    if (!this.home) return;
    this.camera.up.set(0, 1, 0);
    this.controls.target.copy(this.home.target);
    this.camera.position.copy(this.home.pos);
    this.controls.update();
  }

  topView() {
    if (!this.bounds) return;
    const b = this.bounds, cx = (b.x0 + b.x1) / 2, cz = (b.y0 + b.y1) / 2;
    this.controls.target.set(cx, 0, cz);
    this.camera.position.set(cx, Math.max(b.x1 - b.x0, b.y1 - b.y0) * 1.9, cz - 0.001);
    this.controls.update();
  }

  makeBoard(color) {
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.025, 0.05), new THREE.MeshStandardMaterial({ color: 0x1e2238, emissive: color, emissiveIntensity: 0.6 }));
    body.position.y = BOARD_H;
    body.castShadow = true;
    const halo = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 12), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.35 }));
    halo.position.y = BOARD_H;
    const post = vline(0, BOARD_H, new THREE.LineDashedMaterial({ color: 0x5a6488, dashSize: 0.05, gapSize: 0.04 }));
    post.computeLineDistances();
    g.add(body, halo, post);
    g.userData = { body, halo };
    return g;
  }

  makePerson(color, e) {
    const g = new THREE.Group();
    const orb = new THREE.Mesh(new THREE.SphereGeometry(0.075, 24, 16), new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.9, roughness: 0.3 }));
    orb.position.y = PERSON_H;
    orb.castShadow = true;
    const glow = new THREE.Mesh(new THREE.SphereGeometry(0.16, 24, 16), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.18, depthWrite: false }));
    glow.position.y = PERSON_H;
    const post = vline(0.01, PERSON_H, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.5 }));
    // Floor marks skip the depth test: a scanned floor is bumpy by a few cm and would swallow them.
    const disc = flatDisc(new THREE.CircleGeometry(1, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.16, depthWrite: false, depthTest: false, side: THREE.DoubleSide }));
    disc.position.y = 0.012;
    const ring = flatDisc(new THREE.RingGeometry(0.97, 1, 64), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55, depthWrite: false, depthTest: false, side: THREE.DoubleSide }));
    ring.position.y = 0.013;
    const trail = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9, depthTest: false }));
    g.add(orb, glow, post, disc, ring);
    g.userData = { orb, glow, post, disc, ring, trail, pos: new THREE.Vector3(e.x, 0, e.y), unc: e.unc, color };
    return g;
  }

  update(S, colorFor, nameOf) {
    this.dirty = true;
    const boardsSeen = new Set();
    for (const [id, p] of Object.entries(S.layout.receivers || {})) {
      boardsSeen.add(id);
      const node = S.nodes.find((n) => n.id === id), color = STATUS[node?.status] ?? GREY, text = nameOf(id);
      let b = this.boards.get(id);
      if (!b) { b = this.makeBoard(color); this.boardsG.add(b); this.boards.set(id, b); }
      if (b.userData.text !== text) {
        if (b.userData.tag) b.remove(b.userData.tag);
        const tag = labelSprite(text, { size: 0.14 });
        tag.position.y = BOARD_H + 0.17;
        b.add(tag);
        Object.assign(b.userData, { tag, text });
      }
      if (b.position.x !== p.x || b.position.z !== p.y) { b.position.set(p.x, 0, p.y); this.renderer.shadowMap.needsUpdate = true; }
      b.userData.body.material.emissive.setHex(color);
      b.userData.halo.material.color.setHex(color);
    }
    for (const [id, b] of this.boards) if (!boardsSeen.has(id)) { this.boardsG.remove(b); this.boards.delete(id); this.renderer.shadowMap.needsUpdate = true; }

    const peopleSeen = new Set();
    for (const p of S.phones) {
      const e = p.est;
      if (!e) continue;
      peopleSeen.add(p.mac);
      const color = new THREE.Color(colorFor(p.mac));
      let g = this.people.get(p.mac);
      if (!g) { g = this.makePerson(color, e); this.peopleG.add(g, g.userData.trail); this.people.set(p.mac, g); }
      const u = g.userData, stale = e.kind === "stale", shade = stale ? GREY_C : color;
      u.target = new THREE.Vector3(e.x, 0, e.y);
      u.targetUnc = e.unc;
      u.stale = stale;
      u.snap = !!p.snapped;   // app.js snapToTrace: jump, don't glide
      for (const m of [u.orb.material.color, u.orb.material.emissive, u.glow.material.color, u.post.material.color, u.disc.material.color, u.ring.material.color]) m.copy(shade);
      u.orb.material.emissiveIntensity = stale ? 0.15 : 0.9;
      u.disc.material.opacity = stale ? 0.06 : e.kind === "weak" ? 0.1 : 0.16;
      const text = personLabel(p) + (stale ? " (not heard)" : e.kind === "weak" ? " (few boards)" : "");
      if (u.text !== text) {
        if (u.tag) g.remove(u.tag);
        u.tag = labelSprite(text, { size: 0.15 });
        u.tag.position.y = PERSON_H + 0.28;
        g.add(u.tag);
        u.text = text;
      }
      this.setTrail(u, p.trail, color);
    }
    for (const [mac, g] of this.people) if (!peopleSeen.has(mac)) { this.peopleG.remove(g, g.userData.trail); this.people.delete(mac); this.renderer.shadowMap.needsUpdate = true; }
  }

  // Trail buffers are reused and only grown, so the 4 Hz feed doesn't churn GPU buffers.
  setTrail(u, trail, color) {
    const n = trail.length, geo = u.trail.geometry;
    let pa = geo.attributes.position;
    if (!pa || pa.count < n) {
      const cap = Math.max(64, n * 2);
      pa = new THREE.BufferAttribute(new Float32Array(cap * 3), 3).setUsage(THREE.DynamicDrawUsage);
      geo.setAttribute("position", pa);
      geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(cap * 3), 3).setUsage(THREE.DynamicDrawUsage));
    }
    const P = pa.array, C = geo.attributes.color.array;
    for (let i = 0; i < n; i++) {
      const q = trail[i], fade = 0.15 + 0.85 * (i / Math.max(1, n - 1)), c = q[4] === "estimated" ? color : GREY_C;
      P[i * 3] = q[1]; P[i * 3 + 1] = 0.02; P[i * 3 + 2] = q[2];
      C[i * 3] = c.r * fade; C[i * 3 + 1] = c.g * fade; C[i * 3 + 2] = c.b * fade;
    }
    pa.needsUpdate = true;
    geo.attributes.color.needsUpdate = true;
    geo.setDrawRange(0, n >= 2 ? n : 0);
    geo.computeBoundingSphere();
  }

  // Slow GPU: drop the pixel ratio to 1 first, then halve the shadow map.
  degrade() {
    if (this.pixelRatio > 1) {
      this.pixelRatio = 1;
      this.renderer.setPixelRatio(1);
      this.resize();
    } else if (this.sun.shadow.mapSize.x > 512) {
      this.sun.shadow.mapSize.set(512, 512);
      this.sun.shadow.map?.dispose();
      this.sun.shadow.map = null;
      this.renderer.shadowMap.needsUpdate = true;
    }
  }

  frame() {
    const now = performance.now();
    if (!this.visible || !this.onScreen) { this.lastTick = 0; return; }
    const dt = this.lastTick ? Math.min(0.1, (now - this.lastTick) / 1000) : 1 / 60;
    if (this.lastTick) {
      this.frameMs += (now - this.lastTick - this.frameMs) * 0.05;
      this.slowFor = this.frameMs > SLOW_MS ? this.slowFor + dt : 0;
      if (this.slowFor > SLOW_S) { this.degrade(); this.slowFor = 0; this.frameMs = 16.7; }
    }
    this.lastTick = now;
    // The same glide as 0.12 per frame at 60 fps, whatever the frame rate.
    const k = 1 - Math.pow(0.88, dt * 60);
    let moving = this.controls.update() || this.dirty, shadowMoved = false;
    for (const g of this.people.values()) {
      const u = g.userData;
      if (u.target) {
        const kk = u.snap ? 1 : k;
        u.pos.lerp(u.target, kk);
        u.unc += (u.targetUnc - u.unc) * kk;
        if (u.pos.distanceToSquared(u.target) > 1e-6 || Math.abs(u.targetUnc - u.unc) > 1e-3) moving = true;
      }
      if (!u.shadowAt || u.shadowAt.distanceToSquared(u.pos) > SHADOW_MOVE_M * SHADOW_MOVE_M) {
        (u.shadowAt ??= new THREE.Vector3()).copy(u.pos);
        shadowMoved = true;
      }
    }
    if (!moving && now - this.lastRender < IDLE_MS) return;
    this.dirty = false;
    this.lastRender = now;
    if (shadowMoved) this.renderer.shadowMap.needsUpdate = true;
    const t = (now - this.start) / 1000;
    for (const g of this.people.values()) {
      const u = g.userData;
      g.position.copy(u.pos);
      const r = Math.max(0.12, u.unc);
      u.disc.scale.setScalar(r);
      u.ring.scale.setScalar(r);
      u.glow.scale.setScalar(u.stale ? 1 : 1 + 0.12 * Math.sin(t * 3));
      u.orb.position.y = u.glow.position.y = PERSON_H + (u.stale ? 0 : 0.02 * Math.sin(t * 2));
    }
    for (const b of this.boards.values()) b.userData.halo.scale.setScalar(1 + 0.15 * Math.sin(t * 2.2));
    this.renderer.render(this.scene, this.camera);
  }
}

window.Luna3D = { create: (el) => new RoomView(el) };
window.dispatchEvent(new Event("luna3d-ready"));
