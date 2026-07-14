"""
SensorClient — robuste Hülle um die Hersteller-Klasse (command_dispatcher_controller).

Die Vendor-Datei bleibt unverändert; deren requests-Aufrufe haben keinen Timeout
und würden bei Netzwerkproblemen ewig hängen. Diese Klasse ergänzt:
  - Timeouts + Retries für alle HTTP-Aufrufe
  - Uhr-Kalibrierung (Log-Start/-Stop als HTTP-Roundtrip-Mittelpunkt, Drift-Check)
  - Health-Monitor während des Flugs
"""

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import (
    HTTP_DOWNLOAD_TIMEOUT_S, HTTP_RETRIES, HTTP_TIMEOUT_S, LOG_DIR,
    SENSOR_HEALTH_PERIOD_S, SENSOR_MODULE_ID, SENSOR_URL, STREAM_PERIOD_MS,
)


def _make_controller(url):
    """Erzeugt die robuste Unterklasse der Hersteller-Klasse (lazy Import)."""
    import requests
    from command_dispatcher_controller import CommandDispatcherController

    class RobustSensorController(CommandDispatcherController):
        def _request(self, method, path, data=None,
                     timeout=HTTP_TIMEOUT_S, retries=HTTP_RETRIES):
            last_err = None
            for attempt in range(1, retries + 1):
                try:
                    r = requests.request(method, self.url + path, data=data, timeout=timeout)
                    r.raise_for_status()
                    return r
                except Exception as e:
                    last_err = e
                    if attempt < retries:
                        print(f"  [HTTP] {path}: {e} — Versuch {attempt + 1}/{retries} ...")
                        time.sleep(2)
            raise RuntimeError(f"{path} nach {retries} Versuchen fehlgeschlagen: {last_err}")

        def get_status(self):
            return self._request("get", "/status/").json()

        def get_status_quick(self):
            # Für den Health-Monitor: ein Versuch, kurzer Timeout — blockiert nicht
            return self._request("get", "/status/", timeout=5, retries=1).json()

        def get_data_stream_configuration(self):
            return self._request("get", "/receive_measurement/").json()

        def set_data_stream_configuration(self, sensor_module_id, NI_stream_active=False,
                                          MUR_stream_active=False, data_stream_period=100):
            payload = json.dumps([{
                "sensorID":       sensor_module_id,
                "ni":             bool(NI_stream_active),
                "mur":            bool(MUR_stream_active),
                "datastreamrate": data_stream_period,
            }])
            return self._request("post", "/receive_measurement/", data=payload).text

        def start_measurements(self):
            return self._request("post", "/start_measurement_all_sensors/").text

        def stop_measurements(self):
            return self._request("post", "/stop_measurement_all_sensors/").text

        def delete_measurement_log(self):
            return self._request("post", "/delete_measurement_log/").text

        def start_measurement_log(self):
            return self._request("post", "/start_measurement_log/").text

        def stop_measurement_log(self):
            return self._request("post", "/stop_measurement_log/").text

        def download_measurement_log(self, directory=None, file_name=None):
            r = self._request("get", "/download_measurement_log/",
                              timeout=HTTP_DOWNLOAD_TIMEOUT_S)
            file_path = None
            if 'filename="' in r.headers.get("content-disposition", ""):
                if file_name is None:
                    file_name = r.headers["content-disposition"].split('filename="')[-1].split('"')[0]
                file_path = Path(file_name) if directory is None else Path(directory) / file_name
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(r.content)
            return file_path

    return RobustSensorController(url)


class SensorClient:
    """Steuert den Sensor über die HTTP-API: Start, Stop/Download, Kalibrierung, Health."""

    def __init__(self, url: str = SENSOR_URL):
        self._ctrl = _make_controller(url)
        self._log_start_pc_utc = None
        self._log_start_unc_s  = 0.0
        self._log_stop_pc_utc  = None

    # -----------------------------------------------------------------------
    # Messung starten / stoppen
    # -----------------------------------------------------------------------

    def start(self):
        print("  Verbinde Sensor ...")
        connected = self._ctrl.get_connected_sensors()
        if not connected:
            raise RuntimeError(f"Kein Sensor unter {self._ctrl.url}")
        print(f"  Verbundene Module: {connected}")

        self._ctrl.set_data_stream_configuration(
            SENSOR_MODULE_ID,
            NI_stream_active=True,
            MUR_stream_active=True,
            data_stream_period=STREAM_PERIOD_MS,
        )
        self._ctrl.start_measurements()
        self._ctrl.delete_measurement_log()

        # PC-Zeit als Mittelpunkt des HTTP-Roundtrips → Uhr-Kalibrierung
        t0 = datetime.now(timezone.utc)
        self._ctrl.start_measurement_log()
        t1 = datetime.now(timezone.utc)
        self._log_start_pc_utc = t0 + (t1 - t0) / 2
        self._log_start_unc_s  = (t1 - t0).total_seconds() / 2
        print(f"  Sensor-Aufzeichnung läuft (Kalibrier-Unsicherheit ±{self._log_start_unc_s:.2f}s).")

    def stop_and_download(self) -> Path:
        print("  Stoppe Aufzeichnung ...")
        # Stop-Zeitpunkt als Roundtrip-Mittelpunkt merken → Drift-Check der RPi-Uhr
        t0 = datetime.now(timezone.utc)
        self._ctrl.stop_measurement_log()
        t1 = datetime.now(timezone.utc)
        self._log_stop_pc_utc = t0 + (t1 - t0) / 2
        self._ctrl.stop_measurements()

        print("  Lade Sensor-Log herunter ...")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = self._ctrl.download_measurement_log(directory=str(LOG_DIR))
        if zip_path is None:
            raise RuntimeError("Download fehlgeschlagen.")

        print(f"  Gespeichert: {zip_path}")
        return Path(zip_path)

    # -----------------------------------------------------------------------
    # Uhr-Kalibrierung
    # -----------------------------------------------------------------------

    def calibrate_clock_offset(self, sensor_dir: str, prefix: str) -> float:
        """
        Vergleicht PC-Zeit bei Log-Start/-Stop mit erstem/letztem Sensor-Timestamp.
        Gibt den Offset in Sekunden zurück: sensor_utc = sensor_raw - offset.
        Zusätzlich Drift-Check über die Flugdauer (RPi-Uhr ohne NTP driftet).
        """
        ts_path  = Path(sensor_dir) / f"{prefix}_timestamps.csv"
        ts       = pd.read_csv(ts_path)["timestamp"]
        first_ts = pd.to_datetime(ts.iloc[0])   # ohne Timezone-Annahme
        last_ts  = pd.to_datetime(ts.iloc[-1])

        log_start_pc = self._log_start_pc_utc.replace(tzinfo=None)
        offset_s     = (first_ts - log_start_pc).total_seconds()

        print(f"  PC-Zeit Log-Start: {log_start_pc}")
        print(f"  Erster Sensor-TS:  {first_ts}")
        print(f"  RPi-Uhr Offset:    {offset_s:+.1f}s  (±{self._log_start_unc_s:.2f}s)")

        if self._log_stop_pc_utc is not None:
            log_stop_pc = self._log_stop_pc_utc.replace(tzinfo=None)
            offset_end  = (last_ts - log_stop_pc).total_seconds()
            drift_s     = offset_end - offset_s
            print(f"  Uhr-Drift über Flug: {drift_s:+.2f}s")
            if abs(drift_s) > 0.5:
                print("  WARNUNG: RPi-Uhr driftet deutlich — NTP auf dem RPi einrichten!")

        return offset_s

    # -----------------------------------------------------------------------
    # Health-Monitor während des Flugs
    # -----------------------------------------------------------------------

    def start_health_monitor(self, stop_event: threading.Event):
        threading.Thread(target=self._health_loop, args=(stop_event,), daemon=True).start()

    def _health_loop(self, stop_event: threading.Event):
        fails = 0
        while not stop_event.is_set():
            time.sleep(SENSOR_HEALTH_PERIOD_S)
            if stop_event.is_set():
                return
            try:
                status = self._ctrl.get_status_quick()
                bad = [s["id"] for s in status.get("sensors", []) if s.get("sensor_error", 0) != 0]
                if bad:
                    print(f"  [SENSOR] WARNUNG: Sensor-Fehler auf Modul(en) {bad}!")
                fails = 0
            except Exception as e:
                fails += 1
                print(f"  [SENSOR] Status-Abfrage fehlgeschlagen ({fails}x): {e}")
