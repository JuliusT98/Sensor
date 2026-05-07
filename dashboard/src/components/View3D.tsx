'use client';

import { useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Line, Text } from '@react-three/drei';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import * as THREE from 'three';
import type { FlightDataResponse } from '@/lib/types';
import { nirToColor } from '@/lib/colormap';

interface View3DProps {
  data: FlightDataResponse | null;
}

const MAX_HEIGHT = 2.5;  // max surface elevation in scene units
const SCENE_W    = 10.0; // width of the widest dimension in scene units
const GRID_U     = 70;   // mesh subdivisions along U axis

interface ScenePoint {
  x: number;
  z: number;
  nir: number;
}

/**
 * Builds an interpolated terrain-like THREE.BufferGeometry from scattered NIR
 * measurements. Uses Gaussian IDW so the surface matches the 2D heatmap look.
 */
function buildSurfaceGeometry(
  points: ScenePoint[],
  sceneW: number,
  sceneD: number,
  nirMin: number,
  nirMax: number,
): THREE.BufferGeometry {
  const nirRange = nirMax - nirMin || 1;

  // Grid subdivisions
  const gridU = GRID_U;
  const gridV = Math.max(4, Math.round(gridU * sceneD / sceneW));

  // Gaussian sigma ≈ 1.5× average point spacing in scene units
  const avgSpacing = Math.sqrt((sceneW * sceneD) / Math.max(1, points.length));
  const sigma2 = 2 * (avgSpacing * 1.5) ** 2;

  const geo = new THREE.PlaneGeometry(sceneW, sceneD, gridU, gridV);
  geo.rotateX(-Math.PI / 2); // lay flat (XZ plane)

  const posAttr = geo.attributes.position as THREE.BufferAttribute;
  const count = posAttr.count;
  const colors = new Float32Array(count * 3);

  for (let vi = 0; vi < count; vi++) {
    const vx = posAttr.getX(vi);
    const vz = posAttr.getZ(vi);

    // Gaussian-weighted NIR interpolation
    let valueSum  = 0;
    let weightSum = 0;
    for (const p of points) {
      const dx = vx - p.x;
      const dz = vz - p.z;
      const d2 = dx * dx + dz * dz;
      const w  = Math.exp(-d2 / sigma2);
      valueSum  += p.nir * w;
      weightSum += w;
    }

    const nirValue = weightSum > 1e-9 ? valueSum / weightSum : nirMin;
    const height   = ((nirValue - nirMin) / nirRange) * MAX_HEIGHT;
    posAttr.setY(vi, height);

    const [r, g, b] = nirToColor(nirValue, nirMin, nirMax);
    colors[vi * 3]     = r / 255;
    colors[vi * 3 + 1] = g / 255;
    colors[vi * 3 + 2] = b / 255;
  }

  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  return geo;
}

/* ---------- Scene sub-components ---------- */

function NIRSurface({
  points, sceneW, sceneD, nirMin, nirMax,
}: {
  points: ScenePoint[];
  sceneW: number;
  sceneD: number;
  nirMin: number;
  nirMax: number;
}) {
  const geometry = useMemo(
    () => buildSurfaceGeometry(points, sceneW, sceneD, nirMin, nirMax),
    [points, sceneW, sceneD, nirMin, nirMax],
  );

  return (
    <group>
      {/* Solid colored surface */}
      <mesh geometry={geometry} receiveShadow>
        <meshStandardMaterial
          vertexColors
          roughness={0.55}
          metalness={0.05}
          side={THREE.FrontSide}
        />
      </mesh>
      {/* Subtle wireframe overlay */}
      <mesh geometry={geometry}>
        <meshBasicMaterial
          wireframe
          color="#071828"
          opacity={0.18}
          transparent
        />
      </mesh>
    </group>
  );
}

function FlightPath({ positions }: { positions: [number, number, number][] }) {
  if (positions.length < 2) return null;
  return (
    <Line
      points={positions}
      color="#ffffff"
      lineWidth={1.5}
      opacity={0.5}
      transparent
    />
  );
}

function BaseGrid({ sceneW, sceneD }: { sceneW: number; sceneD: number }) {
  const size = Math.max(sceneW, sceneD) * 1.4;
  return (
    <gridHelper
      args={[size, Math.round(size * 4), '#0d1f30', '#0a1822']}
      position={[0, -0.02, 0]}
    />
  );
}

/* ---------- Main scene ---------- */

function SceneContent({ data }: { data: FlightDataResponse | null }) {
  const stats    = data?.stats;
  const features = data?.geojson.features ?? [];

  const geoFeatures = useMemo(
    () => features.filter(f => f.geometry !== null && f.properties.nir_mean !== null),
    [features],
  );

  const { points, pathPositions, sceneW, sceneD } = useMemo(() => {
    if (!stats?.bounds || geoFeatures.length === 0) {
      return { points: [] as ScenePoint[], pathPositions: [] as [number,number,number][], sceneW: SCENE_W, sceneD: SCENE_W };
    }

    const { minLat, maxLat, minLon, maxLon } = stats.bounds;
    const centerLat = (minLat + maxLat) / 2;
    const centerLon = (minLon + maxLon) / 2;
    const cosLat    = Math.cos((centerLat * Math.PI) / 180);

    const latSpanM = (maxLat - minLat) * 111_111;
    const lonSpanM = (maxLon - minLon) * 111_111 * cosLat;
    const maxSpanM = Math.max(latSpanM, lonSpanM, 1);
    const scale    = SCENE_W / maxSpanM;

    const nirMin   = stats.nirMin;
    const nirMax   = stats.nirMax;
    const nirRange = nirMax - nirMin || 1;

    const pts: ScenePoint[] = geoFeatures.map(f => {
      const lon = f.geometry!.coordinates[0];
      const lat = f.geometry!.coordinates[1];
      return {
        x:   (lon - centerLon) * 111_111 * cosLat * scale,
        z: -((lat - centerLat) * 111_111 * scale),
        nir: f.properties.nir_mean!,
      };
    });

    // Flight path traces along the actual surface heights
    const path: [number, number, number][] = pts.map(p => [
      p.x,
      ((p.nir - nirMin) / nirRange) * MAX_HEIGHT + 0.05,
      p.z,
    ]);

    return {
      points: pts,
      pathPositions: path,
      sceneW: lonSpanM * scale,
      sceneD: latSpanM * scale,
    };
  }, [geoFeatures, stats]);

  if (geoFeatures.length === 0) {
    return (
      <Text position={[0, 0, 0]} fontSize={0.4} color="#475569" anchorX="center" anchorY="middle">
        Keine Daten verfügbar
      </Text>
    );
  }

  return (
    <>
      <NIRSurface
        points={points}
        sceneW={sceneW}
        sceneD={sceneD}
        nirMin={stats!.nirMin}
        nirMax={stats!.nirMax}
      />
      <FlightPath positions={pathPositions} />
    </>
  );
}

/* ---------- Export ---------- */

export default function View3D({ data }: View3DProps) {
  const nirMin = data?.stats.nirMin ?? 0;
  const nirMax = data?.stats.nirMax ?? 1;

  return (
    <div className="relative w-full h-full bg-[#040c14]">
      <Canvas
        camera={{ position: [8, 7, 10], fov: 48, near: 0.01, far: 500 }}
        gl={{ antialias: true, alpha: false }}
        style={{ background: '#040c14' }}
        shadows
      >
        {/* Lighting */}
        <ambientLight intensity={0.25} />
        <directionalLight
          position={[6, 12, 6]}
          intensity={1.1}
          castShadow
          shadow-mapSize={[1024, 1024]}
        />
        <pointLight position={[-4, 4, -4]} intensity={0.2} color="#4060ff" />

        {/* Scene */}
        <SceneContent data={data} />
        {data && <BaseGrid sceneW={SCENE_W} sceneD={SCENE_W} />}

        {/* Controls */}
        <OrbitControls
          enablePan
          enableZoom
          enableRotate
          minDistance={1.5}
          maxDistance={35}
          maxPolarAngle={Math.PI / 2 + 0.05}
        />

        {/* Post-processing */}
        <EffectComposer>
          <Bloom intensity={0.18} luminanceThreshold={0.55} luminanceSmoothing={0.9} />
        </EffectComposer>
      </Canvas>

      {/* Colormap legend */}
      {nirMin !== nirMax && (
        <div className="absolute bottom-4 right-4 bg-panel/90 border border-border rounded-lg px-3 py-2 text-xs font-mono pointer-events-none">
          <div className="text-slate-400 mb-1.5">NIR-Wert / Höhe</div>
          <div
            className="h-3 w-32 rounded-sm"
            style={{
              background: 'linear-gradient(to right, #00008f, #00ffff, #008000, #ffff00, #800000)',
            }}
          />
          <div className="flex justify-between text-slate-500 mt-1">
            <span>{nirMin.toFixed(0)}</span>
            <span>{nirMax.toFixed(0)}</span>
          </div>
        </div>
      )}

      {/* Hint */}
      <div className="absolute top-3 left-3 text-xs text-slate-600 font-mono pointer-events-none select-none">
        Drehen: LMB · Zoomen: Mausrad · Verschieben: RMB
      </div>
    </div>
  );
}
