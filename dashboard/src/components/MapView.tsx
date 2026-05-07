'use client';

import { useState, useMemo, useCallback, useEffect } from 'react';
import 'maplibre-gl/dist/maplibre-gl.css';
import Map from 'react-map-gl/maplibre';
import DeckGL from '@deck.gl/react';
import { BitmapLayer, ScatterplotLayer, PathLayer } from '@deck.gl/layers';
import type { MapViewState } from '@deck.gl/core';
import type { FlightDataResponse, NIRFeature } from '@/lib/types';
import { nirToColor } from '@/lib/colormap';
import { format } from 'date-fns';
import { de } from 'date-fns/locale';

interface MapViewProps {
  data: FlightDataResponse | null;
}

interface TooltipInfo {
  x: number;
  y: number;
  feature: NIRFeature;
}

const MAP_STYLE = {
  version: 8 as const,
  sources: {
    arcgis: {
      type: 'raster' as const,
      tiles: [
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      attribution:
        'Tiles © Esri — Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
    },
  },
  layers: [
    {
      id: 'arcgis-layer',
      type: 'raster' as const,
      source: 'arcgis',
    },
  ],
};

const DEFAULT_VIEW_STATE: MapViewState = {
  longitude: 13.0,
  latitude: 47.8,
  zoom: 14,
  pitch: 0,
  bearing: 0,
};

// Canvas size for heatmap texture
const CANVAS_W = 1024;

/**
 * Gauß-Interpolation (Splatting): Jeder Messpunkt strahlt eine gewichtete
 * Farbglocke aus. Pixel = gewichteter Mittelwert aller Beiträge.
 * Gibt ein HTMLCanvasElement zurück (direkt als BitmapLayer-Image nutzbar).
 */
function buildHeatmapCanvas(
  points: Array<{ cx: number; cy: number; value: number }>,
  canvasW: number,
  canvasH: number,
  radius: number,
  nirMin: number,
  nirMax: number
): HTMLCanvasElement {
  const sigma = radius / 2.5;
  const sigma2 = 2 * sigma * sigma;

  const valueSum = new Float32Array(canvasW * canvasH);
  const weightSum = new Float32Array(canvasW * canvasH);

  for (const { cx, cy, value } of points) {
    const x0 = Math.max(0, Math.floor(cx - radius));
    const x1 = Math.min(canvasW - 1, Math.ceil(cx + radius));
    const y0 = Math.max(0, Math.floor(cy - radius));
    const y1 = Math.min(canvasH - 1, Math.ceil(cy + radius));

    for (let py = y0; py <= y1; py++) {
      for (let px = x0; px <= x1; px++) {
        const dx = px - cx;
        const dy = py - cy;
        const d2 = dx * dx + dy * dy;
        if (d2 > radius * radius) continue;
        const w = Math.exp(-d2 / sigma2);
        const idx = py * canvasW + px;
        valueSum[idx] += value * w;
        weightSum[idx] += w;
      }
    }
  }

  const canvas = document.createElement('canvas');
  canvas.width = canvasW;
  canvas.height = canvasH;
  const ctx = canvas.getContext('2d')!;
  const imgData = ctx.createImageData(canvasW, canvasH);

  for (let i = 0; i < canvasW * canvasH; i++) {
    if (weightSum[i] < 1e-6) {
      imgData.data[i * 4 + 3] = 0;
      continue;
    }
    const v = valueSum[i] / weightSum[i];
    const [r, g, b] = nirToColor(v, nirMin, nirMax);
    imgData.data[i * 4 + 0] = r;
    imgData.data[i * 4 + 1] = g;
    imgData.data[i * 4 + 2] = b;
    imgData.data[i * 4 + 3] = 215;
  }

  ctx.putImageData(imgData, 0, 0);
  return canvas;
}

export default function MapView({ data }: MapViewProps) {
  const [tooltip, setTooltip] = useState<TooltipInfo | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [heatmapCanvas, setHeatmapCanvas] = useState<HTMLCanvasElement | null>(null);

  const geoFeatures = useMemo(() => {
    if (!data) return [];
    return data.geojson.features.filter((f) => f.geometry !== null);
  }, [data]);

  const stats = data?.stats;
  const nirMin = stats?.nirMin ?? 0;
  const nirMax = stats?.nirMax ?? 1;

  const bounds = useMemo(() => {
    if (!stats?.bounds) return null;
    return stats.bounds;
  }, [stats?.bounds]);

  const initialViewState = useMemo((): MapViewState => {
    if (bounds) {
      return {
        longitude: (bounds.minLon + bounds.maxLon) / 2,
        latitude: (bounds.minLat + bounds.maxLat) / 2,
        zoom: 16,
        pitch: 0,
        bearing: 0,
      };
    }
    return DEFAULT_VIEW_STATE;
  }, [bounds]);

  // Build heatmap canvas whenever data/bounds change
  useEffect(() => {
    if (!geoFeatures.length || !bounds) {
      setHeatmapCanvas(null);
      return;
    }

    const { minLon, maxLon, minLat, maxLat } = bounds;
    const lonRange = maxLon - minLon || 1e-6;
    const latRange = maxLat - minLat || 1e-6;

    // Preserve geographic aspect ratio (account for latitude distortion)
    const cosLat = Math.cos(((minLat + maxLat) / 2) * (Math.PI / 180));
    const aspectGeo = (lonRange * cosLat) / latRange;
    const canvasH = Math.max(64, Math.round(CANVAS_W / aspectGeo));

    // Convert lon/lat → canvas pixel coords
    const pts = geoFeatures
      .filter((f) => f.properties.nir_mean !== null)
      .map((f) => {
        const lon = f.geometry!.coordinates[0];
        const lat = f.geometry!.coordinates[1];
        return {
          cx: ((lon - minLon) / lonRange) * (CANVAS_W - 1),
          cy: (1 - (lat - minLat) / latRange) * (canvasH - 1),
          value: f.properties.nir_mean!,
        };
      });

    // Radius: ~1.8× the average point spacing in pixels
    const colSpacingPx = (CANVAS_W - 1) / Math.max(1, Math.sqrt(pts.length));
    const radius = Math.max(20, Math.round(colSpacingPx * 1.8));

    setHeatmapCanvas(buildHeatmapCanvas(pts, CANVAS_W, canvasH, radius, nirMin, nirMax));
  }, [geoFeatures, bounds, nirMin, nirMax]);

  const pathData = useMemo(() => {
    if (geoFeatures.length < 2) return [];
    return [
      {
        path: geoFeatures.map(
          (f) =>
            [f.geometry!.coordinates[0], f.geometry!.coordinates[1]] as [number, number]
        ),
      },
    ];
  }, [geoFeatures]);

  const onHover = useCallback(
    (info: { index: number; x: number; y: number; object?: NIRFeature }) => {
      if (info.index >= 0 && info.object) {
        setHoveredIndex(info.index);
        setTooltip({ x: info.x, y: info.y, feature: info.object });
      } else {
        setHoveredIndex(null);
        setTooltip(null);
      }
    },
    []
  );

  const layers = useMemo(() => {
    const result = [];

    // Gaussian heatmap as bitmap overlay
    if (heatmapCanvas && bounds) {
      result.push(
        new BitmapLayer({
          id: 'heatmap-bitmap',
          image: heatmapCanvas,
          bounds: [bounds.minLon, bounds.minLat, bounds.maxLon, bounds.maxLat],
          opacity: 1,
        })
      );
    }

    // Small dark dot markers at each measurement position (like in reference)
    if (geoFeatures.length > 0) {
      result.push(
        new ScatterplotLayer<NIRFeature>({
          id: 'nir-dots',
          data: geoFeatures,
          getPosition: (f) => [
            f.geometry!.coordinates[0],
            f.geometry!.coordinates[1],
          ],
          getFillColor: (_, { index }) =>
            index === hoveredIndex ? [255, 255, 255, 240] : [15, 15, 15, 210],
          getLineColor: [220, 220, 220, 80],
          stroked: true,
          lineWidthMinPixels: 0.5,
          getRadius: (_, { index }) => (index === hoveredIndex ? 5 : 2.5),
          radiusUnits: 'meters',
          radiusMinPixels: 2,
          radiusMaxPixels: 5,
          pickable: true,
          onHover: onHover as never,
          updateTriggers: {
            getFillColor: [hoveredIndex],
            getRadius: [hoveredIndex],
          },
        })
      );
    }

    // Flight path — subtle white line
    if (pathData.length > 0) {
      result.push(
        new PathLayer({
          id: 'flight-path',
          data: pathData,
          getPath: (d) => d.path,
          getColor: [255, 255, 255, 60],
          getWidth: 1,
          widthUnits: 'meters',
          widthMinPixels: 1,
          widthMaxPixels: 2,
        })
      );
    }

    return result;
  }, [geoFeatures, pathData, bounds, hoveredIndex, onHover, heatmapCanvas]);

  return (
    <div className="relative w-full h-full">
      <DeckGL
        initialViewState={initialViewState}
        controller
        layers={layers}
        style={{ position: 'absolute', top: '0', left: '0', right: '0', bottom: '0' }}
      >
        <Map mapStyle={MAP_STYLE} />
      </DeckGL>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute z-50 pointer-events-none bg-panel border border-border rounded-lg px-3 py-2 text-xs font-mono shadow-xl"
          style={{
            left: tooltip.x + 12,
            top: tooltip.y - 10,
            transform:
              tooltip.x > window.innerWidth - 200 ? 'translateX(-110%)' : undefined,
          }}
        >
          <div className="text-accent font-semibold mb-1">NIR-Messpunkt</div>
          {tooltip.feature.properties.nir_mean !== null && (
            <div className="text-slate-200">
              NIR-∅:{' '}
              <span className="text-neon">
                {tooltip.feature.properties.nir_mean.toFixed(1)}
              </span>
            </div>
          )}
          <div className="text-slate-400">
            {tooltip.feature.geometry && (
              <>
                <div>Lat: {tooltip.feature.geometry.coordinates[1].toFixed(6)}</div>
                <div>Lon: {tooltip.feature.geometry.coordinates[0].toFixed(6)}</div>
                {tooltip.feature.geometry.coordinates[2] !== undefined && (
                  <div>Alt: {tooltip.feature.geometry.coordinates[2].toFixed(1)} m</div>
                )}
              </>
            )}
            <div className="mt-1 text-slate-500">
              {format(new Date(tooltip.feature.properties.timestamp), 'HH:mm:ss.SSS', {
                locale: de,
              })}
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {geoFeatures.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="bg-panel/90 border border-border rounded-xl px-6 py-4 text-center">
            <div className="text-slate-400 text-sm font-mono">Keine GPS-Daten vorhanden</div>
            <div className="text-slate-600 text-xs mt-1">Starten Sie den NIR-Geo-Logger</div>
          </div>
        </div>
      )}

      {/* Point count badge */}
      {geoFeatures.length > 0 && (
        <div className="absolute bottom-4 left-4 bg-panel/90 border border-border rounded-lg px-3 py-1.5 text-xs font-mono text-slate-300 pointer-events-none">
          {geoFeatures.length} Punkte &bull; {stats?.count ?? 0} gesamt
        </div>
      )}

      {/* Colormap legend */}
      {geoFeatures.length > 0 && (
        <div className="absolute bottom-4 right-4 bg-panel/90 border border-border rounded-lg px-3 py-2 pointer-events-none">
          <div className="text-xs font-mono text-slate-400 mb-1.5">NIR-Wert</div>
          <div
            className="w-32 h-3 rounded"
            style={{
              background:
                'linear-gradient(to right, #00008f, #00ffff, #008000, #ffff00, #800000)',
            }}
          />
          <div className="flex justify-between text-xs font-mono text-slate-500 mt-1">
            <span>{nirMin.toFixed(0)}</span>
            <span>{nirMax.toFixed(0)}</span>
          </div>
        </div>
      )}
    </div>
  );
}
