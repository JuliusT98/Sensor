import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import type { NIRFeatureCollection, FlightDataResponse } from '@/lib/types';
import { computeStats } from '@/lib/stats';

const GEOJSON_PATH = path.join(process.cwd(), '..', 'output', 'flight_geo.geojson');

const emptyCollection: NIRFeatureCollection = { type: 'FeatureCollection', features: [] };

export async function GET(): Promise<NextResponse<FlightDataResponse>> {
  let geojson: NIRFeatureCollection = emptyCollection;

  try {
    if (fs.existsSync(GEOJSON_PATH)) {
      const raw = fs.readFileSync(GEOJSON_PATH, 'utf-8');
      geojson = JSON.parse(raw) as NIRFeatureCollection;
    }
  } catch (err) {
    console.error('[flight-data] Fehler beim Lesen der GeoJSON-Datei:', err);
    geojson = emptyCollection;
  }

  const stats = computeStats(geojson);

  return NextResponse.json(
    { geojson, stats },
    {
      headers: {
        'Cache-Control': 'no-store',
      },
    }
  );
}
