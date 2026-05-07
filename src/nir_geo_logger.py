#!/usr/bin/env python3
"""
NIR-Geo-Logger: Kombiniert NIR-Sensordaten (HTTP) und Drohnen-GPS (MAVLink)
zu einer kontinuierlich erweiterten, georeferenzierten GeoJSON-Datei.

Verwendung:
  python src/nir_geo_logger.py
  python src/nir_geo_logger.py --sensor-url http://192.168.2.30:8100 \\
      --mavlink udp:0.0.0.0:14550 --hz 5 --output output/flight.geojson
  python src/nir_geo_logger.py --start          # Sensor starten + am Ende stoppen
"""

import argparse
import json
import logging
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

import requests
from pymavlink import mavutil

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_SENSOR_URL = "http://192.168.2.30:8100"
DEFAULT_HZ         = 5.0
DEFAULT_OUTPUT     = "output/flight_geo.geojson"
DEFAULT_MAVLINK    = "udp:0.0.0.0:14550"
DEFAULT_SENSOR_ID  = 1

N_CH  = 8
N_DET = 4

log = logging.getLogger("nir_geo_logger")


# ── SensorClient ───────────────────────────────────────────────────────────────
class SensorClient:
    """HTTP-Client für den NIR-Sensor. Fetch/Extract-Logik identisch zu nir_monitor.py."""

    def __init__(self, url: str, sensor_id: int):
        self.url       = url
        self.sensor_id = sensor_id

    # identisch zu nir_monitor.fetch()
    def _fetch(self) -> dict:
        r = requests.get(self.url + "/get_measurements/", timeout=2.0)
        r.raise_for_status()
        return r.json()

    # identisch zu nir_monitor.extract_nir()
    def _extract_nir(self, data: dict) -> list[float] | None:
        for sensor in data.get("sensors", []):
            if sensor.get("id") != self.sensor_id:
                continue
            values = []
            for ch in range(1, N_CH + 1):
                ch_data  = sensor.get(f"ch{ch}", {})
                det_vals = [ch_data.get(f"d{d}_ni1") for d in range(1, N_DET + 1)]
                det_vals = [v for v in det_vals if v is not None]
                values.append(sum(det_vals) / len(det_vals) if det_vals else float("nan"))
            return values
        return None

    def get_nir(self) -> list[float] | None:
        """Einen NIR-Messwert abrufen. Gibt None bei Netzwerkfehler zurück."""
        try:
            data = self._fetch()
            return self._extract_nir(data)
        except requests.RequestException as e:
            log.warning("Sensor-Fehler: %s", e)
            return None

    def start_measurement(self) -> bool:
        try:
            payload = (
                f'[{{"sensorID":{self.sensor_id},'
                f'"ni":true,"mur":true,"datastreamrate":100}}]'
            )
            requests.post(
                self.url + "/receive_measurement/",
                data=payload,
                timeout=5.0,
            ).raise_for_status()
            requests.post(
                self.url + "/start_measurement_all_sensors/",
                timeout=5.0,
            ).raise_for_status()
            log.info("Sensor-Messung gestartet.")
            time.sleep(0.5)
            return True
        except Exception as e:
            log.error("Sensor konnte nicht gestartet werden: %s", e)
            return False

    def stop_measurement(self):
        try:
            requests.post(self.url + "/stop_measurement_all_sensors/", timeout=5.0)
            log.info("Sensor-Messung gestoppt.")
        except Exception as e:
            log.warning("Sensor konnte nicht gestoppt werden: %s", e)


# ── MavlinkGPS ─────────────────────────────────────────────────────────────────
class MavlinkGPS:
    """
    Liest GLOBAL_POSITION_INT-Nachrichten in einem Hintergrund-Thread.
    Hält immer den zuletzt bekannten GPS-Fix vor; bei keinem Fix: None.
    Stellt automatisch die Verbindung wieder her.
    """

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self._lat: float | None = None
        self._lon: float | None = None
        self._alt: float | None = None
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="mavlink-gps"
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def get_position(self) -> tuple[float | None, float | None, float | None]:
        """Gibt (lat, lon, alt) zurück — oder (None, None, None) ohne Fix."""
        with self._lock:
            return self._lat, self._lon, self._alt

    def _connect(self) -> mavutil.mavfile:
        log.info("Verbinde MAVLink: %s", self.connection_string)
        mav = mavutil.mavlink_connection(self.connection_string)
        mav.wait_heartbeat(timeout=10)
        log.info("MAVLink-Heartbeat empfangen (System %d, Komponente %d).",
                 mav.target_system, mav.target_component)
        return mav

    def _run(self):
        while not self._stop.is_set():
            try:
                mav = self._connect()
                while not self._stop.is_set():
                    msg = mav.recv_match(
                        type="GLOBAL_POSITION_INT",
                        blocking=True,
                        timeout=2.0,
                    )
                    if msg is None:
                        continue
                    lat = msg.lat / 1e7
                    lon = msg.lon / 1e7
                    alt = msg.alt / 1000.0
                    # (0, 0) bedeutet kein Fix
                    if lat == 0.0 and lon == 0.0:
                        continue
                    with self._lock:
                        self._lat = lat
                        self._lon = lon
                        self._alt = alt
                    log.debug("GPS  lat=%.6f  lon=%.6f  alt=%.1f m", lat, lon, alt)
            except Exception as e:
                if not self._stop.is_set():
                    log.warning("MAVLink-Verbindungsfehler — Wiederverbindung in 3 s: %s", e)
                    time.sleep(3.0)


# ── GeoJSONWriter ──────────────────────────────────────────────────────────────
class GeoJSONWriter:
    """
    Schreibt eine GeoJSON-FeatureCollection atomar (temp-Datei + os.replace),
    sodass die Ausgabedatei zu jedem Zeitpunkt valides JSON enthält.
    Thread-sicher.
    """

    def __init__(self, output_path: str):
        self.output_path = output_path
        self._features: list[dict] = []
        self._lock = threading.Lock()
        out_dir = os.path.dirname(output_path) or "."
        os.makedirs(out_dir, exist_ok=True)

    def add(
        self,
        lat: float | None,
        lon: float | None,
        alt: float | None,
        nir_values: list[float],
        timestamp: str,
    ):
        # NaN → null (GeoJSON erlaubt kein NaN)
        nir_json = [None if math.isnan(v) else round(v, 4) for v in nir_values]
        valid    = [v for v in nir_json if v is not None]
        nir_mean = round(sum(valid) / len(valid), 4) if valid else None

        if lat is not None and lon is not None:
            coords   = [round(lon, 7), round(lat, 7), round(alt, 2)] if alt is not None \
                       else [round(lon, 7), round(lat, 7)]
            geometry = {"type": "Point", "coordinates": coords}
        else:
            geometry = None  # GeoJSON-Spec erlaubt null-Geometrie

        feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "timestamp": timestamp,
                "nir_values": nir_json,
                "nir_mean":   nir_mean,
            },
        }

        with self._lock:
            self._features.append(feature)
            self._write()

    def close(self):
        with self._lock:
            self._write()
        log.info("GeoJSON abgeschlossen: %s  (%d Features)", self.output_path, len(self._features))

    def _write(self):
        """Atomares Schreiben: temp-Datei → fsync → os.replace."""
        collection = {"type": "FeatureCollection", "features": self._features}
        out_dir = os.path.dirname(self.output_path) or "."
        try:
            fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".tmp.geojson")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(collection, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.output_path)
        except Exception as e:
            log.error("Fehler beim Schreiben der GeoJSON-Datei: %s", e)


# ── Hauptprogramm ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="NIR-Geo-Logger — NIR-Sensor (HTTP) + GPS (MAVLink) → GeoJSON"
    )
    parser.add_argument("--sensor-url", default=DEFAULT_SENSOR_URL,
                        help="HTTP-URL des NIR-Sensors (Standard: %(default)s)")
    parser.add_argument("--hz",         type=float, default=DEFAULT_HZ,
                        help="Sensor-Polling-Rate in Hz (Standard: %(default)s)")
    parser.add_argument("--output",     default=DEFAULT_OUTPUT,
                        help="Ausgabepfad der GeoJSON-Datei (Standard: %(default)s)")
    parser.add_argument("--mavlink",    default=DEFAULT_MAVLINK,
                        help="MAVLink-Verbindungsstring (Standard: %(default)s)")
    parser.add_argument("--sensor-id",  type=int, default=DEFAULT_SENSOR_ID,
                        help="Sensor-ID in der HTTP-API (Standard: %(default)s)")
    parser.add_argument("--start",      action="store_true",
                        help="Sensor-Messung beim Start initiieren und am Ende stoppen")
    parser.add_argument("--log-level",  default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Log-Level (Standard: %(default)s)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    sensor = SensorClient(args.sensor_url, args.sensor_id)
    gps    = MavlinkGPS(args.mavlink)
    writer = GeoJSONWriter(args.output)

    if args.start:
        if not sensor.start_measurement():
            log.error("Abbruch: Sensor nicht erreichbar.")
            return

    gps.start()
    log.info(
        "Logger gestartet — Sensor: %s  |  MAVLink: %s  |  Ausgabe: %s  |  %.1f Hz",
        args.sensor_url, args.mavlink, args.output, args.hz,
    )

    interval    = 1.0 / args.hz
    count       = 0
    no_gps_warn = 0

    try:
        while True:
            t0 = time.monotonic()
            ts = datetime.now(timezone.utc).isoformat()

            nir = sensor.get_nir()
            if nir is None:
                log.debug("Kein NIR-Wert empfangen — Messung übersprungen.")
                time.sleep(max(0.0, interval - (time.monotonic() - t0)))
                continue

            if all(v == 0.0 for v in nir):
                log.debug("Alle NIR-Werte = 0 — Sensor noch nicht bereit.")
                time.sleep(max(0.0, interval - (time.monotonic() - t0)))
                continue

            lat, lon, alt = gps.get_position()
            if lat is None:
                no_gps_warn += 1
                if no_gps_warn % 20 == 1:
                    log.warning("Kein GPS-Fix — Feature wird mit null-Geometrie gespeichert.")

            writer.add(lat, lon, alt, nir, ts)
            count += 1

            valid_nir = [v for v in nir if not math.isnan(v)]
            nir_mean  = sum(valid_nir) / len(valid_nir) if valid_nir else float("nan")
            log.debug(
                "Punkt %4d  lat=%-11s  lon=%-11s  NIR-∅=%8.2f",
                count,
                f"{lat:.6f}" if lat is not None else "–",
                f"{lon:.6f}" if lon is not None else "–",
                nir_mean,
            )

            time.sleep(max(0.0, interval - (time.monotonic() - t0)))

    except KeyboardInterrupt:
        log.info("Abbruch durch Benutzer (Ctrl+C).")
    finally:
        gps.stop()
        writer.close()
        if args.start:
            sensor.stop_measurement()
        log.info("Beendet. %d Messpunkte in '%s' gespeichert.", count, args.output)


if __name__ == "__main__":
    main()
