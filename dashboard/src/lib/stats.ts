import type { NIRFeatureCollection, FlightStats } from './types';

export function computeStats(geojson: NIRFeatureCollection): FlightStats {
  const features     = geojson.features;
  const count        = features.length;
  const withGPSFeats = features.filter(f => f.geometry !== null);
  const withGPS      = withGPSFeats.length;

  const nirValues: number[] = features
    .map(f => f.properties.nir_mean)
    .filter((v): v is number => v !== null && v !== undefined);

  let nirMin = 0, nirMax = 0, nirMean = 0, nirStd = 0;
  if (nirValues.length > 0) {
    nirMin  = Math.min(...nirValues);
    nirMax  = Math.max(...nirValues);
    nirMean = nirValues.reduce((a, b) => a + b, 0) / nirValues.length;
    nirStd  = Math.sqrt(
      nirValues.reduce((s, v) => s + (v - nirMean) ** 2, 0) / nirValues.length,
    );
  }

  const timestamps = features.map(f => f.properties.timestamp).filter(Boolean).sort();
  const firstTs    = timestamps.at(0) ?? null;
  const lastTs     = timestamps.at(-1) ?? null;

  let bounds: FlightStats['bounds'] = null;
  if (withGPSFeats.length > 0) {
    const lats = withGPSFeats.map(f => f.geometry!.coordinates[1]);
    const lons = withGPSFeats.map(f => f.geometry!.coordinates[0]);
    bounds = {
      minLat: Math.min(...lats), maxLat: Math.max(...lats),
      minLon: Math.min(...lons), maxLon: Math.max(...lons),
    };
  }

  return { count, withGPS, nirMin, nirMax, nirMean, nirStd, firstTs, lastTs, bounds };
}
