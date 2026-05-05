export interface FlightPoint {
  lat: number
  lon: number
  alt: number
  rel_alt: number
  value: number
  time: string
  r: number
  g: number
  b: number
}

export interface PathPoint {
  lat: number
  lon: number
  alt: number
}

export interface FlightMeta {
  label: string
  point_count: number
  v_min: number
  v_max: number
  v_mean: number
  v_std: number
  center_lat: number
  center_lon: number
  generated_at: string
}

export interface FlightData {
  meta: FlightMeta
  points: FlightPoint[]
  path: PathPoint[]
}
