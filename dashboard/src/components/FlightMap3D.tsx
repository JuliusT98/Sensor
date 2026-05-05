'use client'

import { useMemo, useEffect, useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls, Grid, Html } from '@react-three/drei'
import * as THREE from 'three'
import { FlightData, FlightMeta } from '@/lib/types'

// ── Geo helpers ──────────────────────────────────────────────────────────────

const LAT_M = 111319
const lonM  = (lat: number) => 111319 * Math.cos((lat * Math.PI) / 180)
const ALT_SCALE = 8
const GROUND_Y  = 0
const SAT_ZOOM  = 17

function toXYZ(
  lat: number, lon: number, alt: number,
  cLat: number, cLon: number, meanAlt: number
): [number, number, number] {
  return [
    (lon - cLon) * lonM(cLat),
    (alt - meanAlt) * ALT_SCALE,
    -(lat - cLat) * LAT_M,
  ]
}

function latLonToTile(lat: number, lon: number, zoom: number) {
  const x = Math.floor(((lon + 180) / 360) * Math.pow(2, zoom))
  const r = (lat * Math.PI) / 180
  const y = Math.floor(
    ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * Math.pow(2, zoom)
  )
  return { x, y }
}

function tileToBounds(tx: number, ty: number, zoom: number) {
  const n   = Math.pow(2, zoom)
  const minLon = (tx / n) * 360 - 180
  const maxLon = ((tx + 1) / n) * 360 - 180
  const maxLat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * ty) / n))) * 180) / Math.PI
  const minLat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * (ty + 1)) / n))) * 180) / Math.PI
  return { minLat, maxLat, minLon, maxLon }
}

// ── Satellite tile mesh ──────────────────────────────────────────────────────

interface TileMeshProps {
  tx: number; ty: number; zoom: number
  meta: FlightMeta
}

function TileMesh({ tx, ty, zoom, meta }: TileMeshProps) {
  const [texture, setTexture] = useState<THREE.Texture | null>(null)

  useEffect(() => {
    const loader = new THREE.TextureLoader()
    loader.load(`/api/tile?z=${zoom}&x=${tx}&y=${ty}`, t => {
      t.colorSpace = THREE.SRGBColorSpace
      setTexture(t)
    })
    return () => { texture?.dispose() }
  }, [tx, ty, zoom]) // eslint-disable-line

  const { position, size } = useMemo(() => {
    const b  = tileToBounds(tx, ty, zoom)
    const lm = lonM(meta.center_lat)
    const cx = ((b.minLon + b.maxLon) / 2 - meta.center_lon) * lm
    const cz = -((b.minLat + b.maxLat) / 2 - meta.center_lat) * LAT_M
    const w  = (b.maxLon - b.minLon) * lm
    const h  = (b.maxLat - b.minLat) * LAT_M
    return {
      position: [cx, GROUND_Y, cz] as [number, number, number],
      size:     [w, h]             as [number, number],
    }
  }, [tx, ty, zoom, meta])

  if (!texture) return null

  return (
    <mesh position={position} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={size} />
      <meshBasicMaterial map={texture} />
    </mesh>
  )
}

// ── Satellite ground (assembles tiles) ───────────────────────────────────────

function SatelliteGround({ data }: { data: FlightData }) {
  const tiles = useMemo(() => {
    const { points, meta } = data
    const pad    = 0.001
    const minLat = Math.min(...points.map(p => p.lat)) - pad
    const maxLat = Math.max(...points.map(p => p.lat)) + pad
    const minLon = Math.min(...points.map(p => p.lon)) - pad
    const maxLon = Math.max(...points.map(p => p.lon)) + pad

    const tl = latLonToTile(maxLat, minLon, SAT_ZOOM)
    const br = latLonToTile(minLat, maxLon, SAT_ZOOM)

    const result: { x: number; y: number }[] = []
    for (let x = tl.x; x <= br.x; x++)
      for (let y = tl.y; y <= br.y; y++)
        result.push({ x, y })
    return result
  }, [data])

  return (
    <>
      {tiles.map(({ x, y }) => (
        <TileMesh key={`${x}-${y}`} tx={x} ty={y} zoom={SAT_ZOOM} meta={data.meta} />
      ))}
    </>
  )
}

// ── Flight path ───────────────────────────────────────────────────────────────

function FlightPath({ data, meanAlt }: { data: FlightData; meanAlt: number }) {
  const { points, meta } = data

  const { positions, colors } = useMemo(() => {
    const pos: number[] = [], col: number[] = []
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i], b = points[i + 1]
      const [ax, ay, az] = toXYZ(a.lat, a.lon, a.rel_alt, meta.center_lat, meta.center_lon, meanAlt)
      const [bx, by, bz] = toXYZ(b.lat, b.lon, b.rel_alt, meta.center_lat, meta.center_lon, meanAlt)
      pos.push(ax, ay, az, bx, by, bz)
      col.push(1, 0.85, 0, 1, 0.85, 0)
    }
    return { positions: new Float32Array(pos), colors: new Float32Array(col) }
  }, [points, meta, meanAlt])

  return (
    <lineSegments>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={positions} count={positions.length / 3} itemSize={3} />
        <bufferAttribute attach="attributes-color"    array={colors}    count={colors.length / 3}    itemSize={3} />
      </bufferGeometry>
      <lineBasicMaterial vertexColors />
    </lineSegments>
  )
}

// ── Measurement points ────────────────────────────────────────────────────────

function MeasurementPoints({ data, meanAlt }: { data: FlightData; meanAlt: number }) {
  const { points, meta } = data

  const { positions, colors } = useMemo(() => {
    const pos = new Float32Array(points.length * 3)
    const col = new Float32Array(points.length * 3)
    points.forEach((p, i) => {
      const [x, , z] = toXYZ(p.lat, p.lon, 0, meta.center_lat, meta.center_lon, 0)
      pos[i*3]=x; pos[i*3+1]=GROUND_Y; pos[i*3+2]=z
      col[i*3]=p.r/255; col[i*3+1]=p.g/255; col[i*3+2]=p.b/255
    })
    return { positions: pos, colors: col }
  }, [points, meta, meanAlt])

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={positions} count={positions.length / 3} itemSize={3} />
        <bufferAttribute attach="attributes-color"    array={colors}    count={colors.length / 3}    itemSize={3} />
      </bufferGeometry>
      <pointsMaterial vertexColors size={1.8} sizeAttenuation />
    </points>
  )
}

// ── Markers ───────────────────────────────────────────────────────────────────

function Markers({ data, meanAlt }: { data: FlightData; meanAlt: number }) {
  const { points, meta } = data
  const [sx, , sz] = toXYZ(points[0].lat, points[0].lon, 0, meta.center_lat, meta.center_lon, 0)
  const last = points[points.length - 1]
  const [ex, , ez] = toXYZ(last.lat, last.lon, 0, meta.center_lat, meta.center_lon, 0)

  return (
    <>
      <mesh position={[sx, GROUND_Y, sz]}>
        <sphereGeometry args={[0.4, 12, 12]} />
        <meshStandardMaterial color="#00ff88" emissive="#00ff88" emissiveIntensity={0.6} />
      </mesh>
      <Html position={[sx, GROUND_Y + 5, sz]} center style={{ pointerEvents: 'none' }}>
        <div style={{ color: '#00ff88', fontSize: 11, fontFamily: 'sans-serif', whiteSpace: 'nowrap' }}>Start</div>
      </Html>

      <mesh position={[ex, GROUND_Y, ez]}>
        <sphereGeometry args={[0.4, 12, 12]} />
        <meshStandardMaterial color="#ff4444" emissive="#ff4444" emissiveIntensity={0.6} />
      </mesh>
      <Html position={[ex, GROUND_Y + 5, ez]} center style={{ pointerEvents: 'none' }}>
        <div style={{ color: '#ff4444', fontSize: 11, fontFamily: 'sans-serif', whiteSpace: 'nowrap' }}>Landung</div>
      </Html>
    </>
  )
}

// ── Scene ─────────────────────────────────────────────────────────────────────

function Scene({ data, showSat }: { data: FlightData; showSat: boolean }) {
  const meanAlt = 0

  return (
    <>
      <ambientLight intensity={0.5} />
      <directionalLight position={[100, 200, 100]} intensity={0.7} />

      {showSat
        ? <SatelliteGround data={data} />
        : (
          <Grid
            args={[600, 600]}
            cellSize={10} cellThickness={0.3} cellColor="#1a2030"
            sectionSize={50} sectionThickness={0.8} sectionColor="#2d3a50"
            fadeDistance={600} fadeStrength={2}
            position={[0, GROUND_Y, 0]}
          />
        )
      }

      <FlightPath data={data} meanAlt={meanAlt} />
      <MeasurementPoints data={data} meanAlt={meanAlt} />
      <Markers data={data} meanAlt={meanAlt} />

      <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
    </>
  )
}

// ── Export ────────────────────────────────────────────────────────────────────

export default function FlightMap3D({ data }: { data: FlightData }) {
  const [showSat, setShowSat] = useState(false)

  return (
    <div className="w-full h-full relative">
      <Canvas
        camera={{ position: [0, 90, 220], fov: 55, near: 0.5, far: 5000 }}
        style={{ background: '#05070f' }}
        gl={{ antialias: true }}
      >
        <fog attach="fog" args={['#05070f', 400, 1200]} />
        <Scene data={data} showSat={showSat} />
      </Canvas>

      {/* Satellite toggle */}
      <button
        onClick={() => setShowSat(s => !s)}
        className={`absolute top-3 right-3 px-3 py-1.5 rounded text-xs font-medium transition-all z-10 ${
          showSat
            ? 'bg-[#00d4ff] text-black shadow-[0_0_12px_rgba(0,212,255,0.4)]'
            : 'text-slate-400 hover:text-white border border-[#1e2433] bg-[#0d1117]/80 backdrop-blur-sm'
        }`}
      >
        🛰 Satellit
      </button>

      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-slate-600 pointer-events-none">
        Drag zum Rotieren · Scroll zum Zoomen · Rechtsklick zum Verschieben
      </div>
    </div>
  )
}
