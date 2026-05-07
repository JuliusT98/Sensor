export interface NIRFeature {
  type: 'Feature';
  geometry: {
    type: 'Point';
    coordinates: [number, number, number]; // [lon, lat, alt]
  } | null;
  properties: {
    timestamp: string;
    nir_values: (number | null)[];
    nir_mean: number | null;
  };
}

export interface NIRFeatureCollection {
  type: 'FeatureCollection';
  features: NIRFeature[];
}

export interface FlightStats {
  count: number;
  withGPS: number;
  nirMin: number;
  nirMax: number;
  nirMean: number;
  nirStd: number;
  firstTs: string | null;
  lastTs: string | null;
  bounds: {
    minLat: number;
    maxLat: number;
    minLon: number;
    maxLon: number;
  } | null;
}

export interface FlightDataResponse {
  geojson: NIRFeatureCollection;
  stats: FlightStats;
}
