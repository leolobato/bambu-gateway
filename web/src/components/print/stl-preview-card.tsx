import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import {
  Compass,
  Grid3x3,
  Move,
  RotateCcw,
  RotateCw,
  Undo2,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { InfoBanner } from '@/components/print/info-banner';
import type {
  BannerData,
} from '@/lib/print-context';
import type {
  StlDraftScene,
  StlLayoutAction,
  StlTransform,
} from '@/lib/api/types';

const ACTION_LABELS: Record<StlLayoutAction, { label: string; icon: typeof Compass }> = {
  auto_orient: { label: 'Auto-orient', icon: Compass },
  rotate_x_90: { label: 'X +90°', icon: RotateCw },
  rotate_x_minus_90: { label: 'X −90°', icon: RotateCcw },
  rotate_y_90: { label: 'Y +90°', icon: RotateCw },
  rotate_y_minus_90: { label: 'Y −90°', icon: RotateCcw },
  rotate_z_90: { label: 'Z +90°', icon: RotateCw },
  rotate_z_minus_90: { label: 'Z −90°', icon: RotateCcw },
  center: { label: 'Center', icon: Move },
  arrange: { label: 'Arrange', icon: Grid3x3 },
  reset: { label: 'Reset', icon: Undo2 },
};

const ACTION_ORDER: StlLayoutAction[] = [
  'auto_orient',
  'rotate_x_90',
  'rotate_x_minus_90',
  'rotate_y_90',
  'rotate_y_minus_90',
  'rotate_z_90',
  'rotate_z_minus_90',
  'center',
  'arrange',
  'reset',
];

export function StlPreviewCard({
  filename,
  scene,
  applyingAction,
  banner,
  onAction,
  onAccept,
  onCancel,
}: {
  filename: string;
  scene: StlDraftScene;
  applyingAction: StlLayoutAction | null;
  banner?: BannerData;
  onAction: (action: StlLayoutAction) => void;
  onAccept: () => void;
  onCancel: () => void;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const partsRootRef = useRef<THREE.Group | null>(null);
  const geometryRef = useRef<THREE.BufferGeometry | null>(null);
  const sharedMaterialRef = useRef<THREE.MeshStandardMaterial | null>(null);
  const animFrameRef = useRef<number | null>(null);

  const bedWidth = scene.bed.width || 256;
  const bedDepth = scene.bed.depth || 256;
  const printableArea = scene.bed.printable_area;
  // The bed plane is centered at (width/2, depth/2, 0). Camera framing
  // depends only on bed extents, so derive a single key for the layout
  // effect.
  const layoutKey = `${bedWidth}x${bedDepth}|${printableArea.length}`;

  // Set up renderer/scene/camera/controls once per mount.
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    const width = mount.clientWidth;
    const height = mount.clientHeight;
    renderer.setSize(width, height);
    mount.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    const threeScene = new THREE.Scene();
    threeScene.background = null;
    // Bambu printers use Z-up; align Three.js with that so transforms in
    // mesh_transform/transform read the same as in OrcaSlicer.
    threeScene.up.set(0, 0, 1);
    sceneRef.current = threeScene;

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 5000);
    camera.up.set(0, 0, 1);
    cameraRef.current = camera;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controlsRef.current = controls;

    // Bed plane (filled).
    const bedGeom = new THREE.PlaneGeometry(bedWidth, bedDepth);
    const bedMat = new THREE.MeshBasicMaterial({
      color: 0x202428,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.7,
    });
    const bedMesh = new THREE.Mesh(bedGeom, bedMat);
    bedMesh.position.set(bedWidth / 2, bedDepth / 2, 0);
    threeScene.add(bedMesh);

    // Printable-area outline.
    if (printableArea.length >= 2) {
      const points: THREE.Vector3[] = printableArea.map(
        ([x, y]) => new THREE.Vector3(x, y, 0.05),
      );
      points.push(points[0].clone());
      const lineGeom = new THREE.BufferGeometry().setFromPoints(points);
      const lineMat = new THREE.LineBasicMaterial({ color: 0x3aa0ff });
      threeScene.add(new THREE.Line(lineGeom, lineMat));
    }

    // Lights.
    const ambient = new THREE.AmbientLight(0xffffff, 0.55);
    threeScene.add(ambient);
    const key = new THREE.DirectionalLight(0xffffff, 0.85);
    key.position.set(bedWidth, -bedDepth, Math.max(bedWidth, bedDepth));
    threeScene.add(key);

    // Camera framing.
    const cx = bedWidth / 2;
    const cy = bedDepth / 2;
    const size = Math.max(bedWidth, bedDepth);
    camera.position.set(cx + size * 0.6, cy - size * 1.1, size * 0.9);
    camera.lookAt(cx, cy, 0);
    controls.target.set(cx, cy, 0);
    controls.update();

    // Group that holds the loaded parts; instances re-attach on scene updates.
    const partsRoot = new THREE.Group();
    threeScene.add(partsRoot);
    partsRootRef.current = partsRoot;

    sharedMaterialRef.current = new THREE.MeshStandardMaterial({
      color: 0x6aa5ff,
      metalness: 0.05,
      roughness: 0.55,
    });

    function render() {
      controls.update();
      renderer.render(threeScene, camera);
      animFrameRef.current = requestAnimationFrame(render);
    }
    animFrameRef.current = requestAnimationFrame(render);

    const ro = new ResizeObserver(() => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    ro.observe(mount);

    return () => {
      ro.disconnect();
      if (animFrameRef.current != null) cancelAnimationFrame(animFrameRef.current);
      controls.dispose();
      // Dispose all geometries/materials/textures we created.
      threeScene.traverse((obj) => {
        const m = obj as THREE.Mesh;
        if ((m as THREE.Mesh).isMesh) {
          m.geometry?.dispose();
          const mat = m.material as THREE.Material | THREE.Material[];
          if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
          else mat?.dispose();
        }
        const l = obj as THREE.Line;
        if ((l as THREE.Line).isLine) l.geometry?.dispose();
      });
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      rendererRef.current = null;
      sceneRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
      partsRootRef.current = null;
      geometryRef.current = null;
      sharedMaterialRef.current = null;
    };
    // We intentionally only re-init on bed/printable-area changes; transforms
    // come through the dedicated scene.objects effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey]);

  // Load the STL bytes when source_url changes. Geometry is shared across
  // instances; instance-local placement comes from mesh_transform + transform.
  useEffect(() => {
    let cancelled = false;
    const loader = new STLLoader();
    loader.load(
      scene.source_url,
      (geom) => {
        if (cancelled) return;
        // Compute normals so the standard material lights it.
        geom.computeVertexNormals();
        // Replace the prior geometry if any.
        geometryRef.current?.dispose();
        geometryRef.current = geom;
        rebuildInstances(geom);
      },
      undefined,
      (err) => {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.error('STL load failed', err);
      },
    );
    return () => {
      cancelled = true;
    };
    // rebuildInstances closes over scene.objects via the effect below; we
    // rebuild on geometry load and on scene.objects updates separately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene.source_url]);

  // Rebuild placed mesh instances when scene.objects changes.
  useEffect(() => {
    if (!geometryRef.current) return;
    rebuildInstances(geometryRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene.objects]);

  function rebuildInstances(geom: THREE.BufferGeometry) {
    const root = partsRootRef.current;
    const material = sharedMaterialRef.current;
    if (!root || !material) return;
    // Clear prior instances (groups only; geometry/material are shared).
    while (root.children.length > 0) {
      root.remove(root.children[0]);
    }
    for (const obj of scene.objects) {
      const instanceGroup = new THREE.Group();
      applyTransform(instanceGroup, obj.transform);
      const meshGroup = new THREE.Group();
      applyTransform(meshGroup, obj.mesh_transform);
      const mesh = new THREE.Mesh(geom, material);
      meshGroup.add(mesh);
      instanceGroup.add(meshGroup);
      root.add(instanceGroup);
    }
  }

  const supportedActions = useMemo(() => {
    const set = new Set(scene.actions ?? []);
    return ACTION_ORDER.filter((a) => set.size === 0 || set.has(a));
  }, [scene.actions]);

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-2xl border border-line bg-surface-0 p-4 shadow-card">
        <div className="flex items-center justify-between gap-3 pb-3">
          <div className="text-sm text-text-1 truncate">
            <span className="text-text-0 font-semibold">{filename}</span>
            <span className="ml-2">— STL preview</span>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onCancel}
            aria-label="Cancel STL preview"
          >
            <X className="w-4 h-4" />
          </Button>
        </div>
        <div
          ref={mountRef}
          className="w-full h-[420px] rounded-xl bg-bg-1 overflow-hidden"
        />
        <div className="flex flex-wrap gap-2 pt-3">
          {supportedActions.map((action) => {
            const { label, icon: Icon } = ACTION_LABELS[action];
            const busy = applyingAction === action;
            return (
              <Button
                key={action}
                type="button"
                onClick={() => onAction(action)}
                disabled={applyingAction !== null}
                className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-9 px-4 text-[13px] font-semibold"
              >
                <Icon className="w-4 h-4 mr-1.5" aria-hidden />
                {busy ? `${label}…` : label}
              </Button>
            );
          })}
        </div>
      </div>

      {banner && (
        <InfoBanner
          variant={banner.variant}
          title={banner.title}
          message={banner.message}
          details={banner.details}
        />
      )}

      <div className="grid grid-cols-2 gap-2.5">
        <Button
          type="button"
          onClick={onCancel}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          Cancel
        </Button>
        <Button
          type="button"
          onClick={onAccept}
          disabled={applyingAction !== null}
          className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
        >
          Accept and continue
        </Button>
      </div>
    </div>
  );
}

function applyTransform(obj: THREE.Object3D, transform: StlTransform) {
  obj.position.set(transform.offset[0], transform.offset[1], transform.offset[2]);
  obj.rotation.set(
    transform.rotation[0],
    transform.rotation[1],
    transform.rotation[2],
  );
  obj.scale.set(transform.scale[0], transform.scale[1], transform.scale[2]);
}
