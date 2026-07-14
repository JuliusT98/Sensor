"""
GPS-Tracking per MAVLink (Pixhawk / Telemetrie-Radio).

GpsBuffer  — thread-sicherer Punktspeicher mit crash-sicherem Live-CSV
GpsTracker — Empfangs-Thread: GPS-Zeit (SYSTEM_TIME), Survey-Fortschritt,
             HDOP-Gating, Watchdog + Reconnect, Landungserkennung via Disarm
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    LAND_ALT_M, LAND_CONFIRM_S, LAND_SPEED_MS, LINK_LOST_S, LINK_WARN_S,
    MIN_HDOP, MISSION_MAX_S, TAKEOFF_ALT_M, TELEMETRY_BAUD,
)


class GpsBuffer:
    """Thread-sicherer GPS-Punktspeicher, schreibt optional live in ein CSV mit."""

    def __init__(self, csv_path=None):
        self._lock    = threading.Lock()
        self._data    = deque()
        self.count    = 0
        self.landed   = threading.Event()
        self.armed    = False
        # Inkrementelles CSV: bei Absturz mitten im Flug bleiben die Punkte erhalten
        self._csv        = None
        self._last_flush = 0.0
        if csv_path is not None:
            Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
            self._csv = open(csv_path, "w", encoding="utf-8")
            self._csv.write("timestamp,lat,lon,alt,rel_alt,speed\n")
            self._csv.flush()

    def add(self, ts, lat, lon, alt, rel_alt, speed):
        with self._lock:
            self._data.append((ts, lat, lon, alt, rel_alt, speed))
            self.count += 1
            if self._csv is not None:
                self._csv.write(f"{ts.isoformat()},{lat:.7f},{lon:.7f},"
                                f"{alt:.2f},{rel_alt:.2f},{speed:.2f}\n")
                now = time.monotonic()
                if now - self._last_flush > 2.0:
                    self._csv.flush()
                    self._last_flush = now

    def close_csv(self):
        with self._lock:
            if self._csv is not None:
                self._csv.flush()
                self._csv.close()
                self._csv = None

    def to_dataframe(self) -> pd.DataFrame:
        with self._lock:
            rows = list(self._data)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["timestamp", "lat", "lon", "alt", "rel_alt", "speed"])
        return df.set_index("timestamp").sort_index()


class GpsTracker:
    """MAVLink-GPS-Logger mit Reconnect, GPS-Zeit und QGC-Survey-Fortschritt."""

    def __init__(self, port: str, baud: int = TELEMETRY_BAUD,
                 sitl: bool = False, csv_path=None):
        self.port   = port
        self.baud   = baud
        self.sitl   = sitl
        self.buffer = GpsBuffer(csv_path=csv_path)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # Bequeme Durchgriffe auf den Buffer
    @property
    def landed(self) -> threading.Event:
        return self.buffer.landed

    @property
    def count(self) -> int:
        return self.buffer.count

    # -----------------------------------------------------------------------
    # Empfangs-Thread
    # -----------------------------------------------------------------------

    def _run(self):
        from pymavlink import mavutil

        buf  = self.buffer
        sitl = self.sitl

        airborne          = False   # erst True, wenn Drohne wirklich abgehoben ist
        armed_since       = None    # monotonic-Zeit des Armings (für Missions-Timeout)
        boot_offset_us    = None    # GPS-Zeit: time_unix_usec - time_boot_ms*1000 (SYSTEM_TIME)
        warned_pc_clock   = False
        mission_count     = None    # Anzahl Mission-Items (QGC-Survey-Plan)
        last_mission_seq  = None
        survey_done       = False
        fix_ok            = True    # GPS-Qualität laut GPS_RAW_INT
        last_quality_warn = 0.0
        land_notice_since = None
        land_notice_done  = False
        connect_fails     = 0

        while not buf.landed.is_set():
            # -- Verbinden (mit Reconnect bei Link-Verlust) ---------------------
            try:
                print(f"[GPS] Verbinde {self.port} ...")
                mav = mavutil.mavlink_connection(self.port, baud=self.baud)
                hb  = mav.wait_heartbeat(timeout=15)
                if hb is None:
                    raise RuntimeError("Kein Heartbeat erhalten (Timeout 15s)")
                print(f"[GPS] Verbunden (System {mav.target_system}) — fordere Streams an ...")
            except Exception as e:
                connect_fails += 1
                print(f"[GPS] Verbindungsfehler: {e}")
                if connect_fails >= 3 and buf.count == 0:
                    print("[GPS] Keine Verbindung möglich — GPS-Logging abgebrochen.")
                    buf.landed.set()
                    return
                time.sleep(3)
                continue
            connect_fails = 0

            # Streams anfordern — klassisch und per MESSAGE_INTERVAL (je nach Firmware)
            for stream_id, rate_hz in (
                (mavutil.mavlink.MAV_DATA_STREAM_POSITION,        10),
                (mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS,  2),
                (mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,           4),
            ):
                mav.mav.request_data_stream_send(
                    mav.target_system, mav.target_component, stream_id, rate_hz, 1)
            for msg_id, interval_us in (
                (mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT,   100_000),  # 10 Hz
                (mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT,           500_000),  #  2 Hz
                (mavutil.mavlink.MAVLINK_MSG_ID_SYSTEM_TIME,         1_000_000),  #  1 Hz
                (mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE,  1_000_000),
                (mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT,     1_000_000),
            ):
                mav.mav.command_long_send(
                    mav.target_system, mav.target_component,
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                    msg_id, interval_us, 0, 0, 0, 0, 0)

            # Missions-Länge abfragen (Fortschritt/Survey-Ende) — Antwort: MISSION_COUNT
            if mission_count is None:
                mav.mav.mission_request_list_send(mav.target_system, mav.target_component)

            last_msg_t  = time.monotonic()
            link_warned = False

            # -- Empfangsschleife -----------------------------------------------
            while not buf.landed.is_set():
                try:
                    msg = mav.recv_match(
                        type=["GLOBAL_POSITION_INT", "HEARTBEAT", "EXTENDED_SYS_STATE",
                              "GPS_RAW_INT", "SYSTEM_TIME", "MISSION_COUNT",
                              "MISSION_CURRENT", "MISSION_ITEM_REACHED"],
                        blocking=True, timeout=1.0)
                except Exception as e:
                    print(f"[GPS] Link-Fehler: {e} — Reconnect ...")
                    break

                now = time.monotonic()

                # Not-Stopp: Mission dauert unplausibel lange
                if armed_since is not None and now - armed_since > MISSION_MAX_S:
                    print(f"[GPS] Missions-Timeout ({MISSION_MAX_S / 60:.0f} min) — stoppe Aufzeichnung.")
                    buf.landed.set()
                    break

                if msg is None:
                    silent = now - last_msg_t
                    if silent > LINK_LOST_S:
                        print(f"[GPS] {silent:.0f}s keine Telemetrie — Reconnect ...")
                        break
                    if silent > LINK_WARN_S and not link_warned:
                        print(f"[GPS] WARNUNG: seit {silent:.0f}s keine Telemetrie.")
                        link_warned = True
                    continue
                last_msg_t  = now
                link_warned = False

                t = msg.get_type()

                if t == "HEARTBEAT":
                    armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                    if armed and not buf.armed:
                        print("[GPS] Drohne armed — starte Aufzeichnung.")
                        armed_since = now
                    if buf.armed and not armed and airborne:
                        print("[GPS] Disarm erkannt → Landung bestätigt.")
                        buf.landed.set()
                    buf.armed = armed

                elif t == "SYSTEM_TIME":
                    # GPS-Zeit vom Autopiloten. Latenzfrei, da beide Felder onboard
                    # entstehen — Offset gilt dann für jedes time_boot_ms.
                    if msg.time_unix_usec > 1_000_000_000_000_000:  # Plausibilität (> Jahr 2001)
                        if boot_offset_us is None:
                            print("[GPS] GPS-Zeit empfangen — Zeitstempel ab jetzt von der GPS-Uhr.")
                        boot_offset_us = msg.time_unix_usec - msg.time_boot_ms * 1000

                elif t == "GPS_RAW_INT":
                    # Nur Qualitäts-Überwachung — Positionen kommen aus GLOBAL_POSITION_INT
                    hdop   = msg.eph / 100.0 if msg.eph not in (0, 65535) else None
                    fix_ok = msg.fix_type >= 3 and (hdop is None or hdop <= MIN_HDOP)
                    if not fix_ok and now - last_quality_warn > 5.0:
                        print(f"[GPS] WARNUNG: GPS-Qualität schlecht (fix={msg.fix_type}, "
                              f"HDOP={hdop}) — Punkte werden verworfen.")
                        last_quality_warn = now

                elif t == "GLOBAL_POSITION_INT":
                    if boot_offset_us is not None:
                        ts = datetime.fromtimestamp(
                            (boot_offset_us + msg.time_boot_ms * 1000) / 1e6, tz=timezone.utc)
                    else:
                        if not warned_pc_clock:
                            print("[GPS] Noch keine GPS-Zeit (SYSTEM_TIME) — nutze vorerst PC-Uhr.")
                            warned_pc_clock = True
                        ts = datetime.now(timezone.utc)

                    lat     = msg.lat / 1e7
                    lon     = msg.lon / 1e7
                    alt     = msg.alt / 1000.0
                    rel_alt = msg.relative_alt / 1000.0
                    speed   = np.sqrt(msg.vx**2 + msg.vy**2) / 100.0

                    # rel_alt == 0.0 während des Fluges: SITL-Artefakt (nur mit --sitl filtern)
                    spurious = sitl and buf.armed and rel_alt == 0.0
                    if lat != 0.0 and lon != 0.0 and fix_ok and not spurious:
                        buf.add(ts, lat, lon, alt, rel_alt, speed)

                    if buf.armed and not spurious and rel_alt > TAKEOFF_ALT_M:
                        if not airborne:
                            print("[GPS] Abgehoben.")
                        airborne = True

                    # Höhen/Geschwindigkeits-Heuristik: nur noch Hinweis — der Stop
                    # kommt von Disarm oder landed_state (Baro-Rauschen bei 1.5 m Flughöhe)
                    if airborne and buf.armed and not spurious \
                            and rel_alt < LAND_ALT_M and speed < LAND_SPEED_MS:
                        if land_notice_since is None:
                            land_notice_since = now
                        elif now - land_notice_since >= LAND_CONFIRM_S and not land_notice_done:
                            print("[GPS] Hinweis: sieht nach Landung aus — warte auf Disarm "
                                  "(oder ENTER für manuellen Stop).")
                            land_notice_done = True
                    else:
                        land_notice_since = None

                elif t == "EXTENDED_SYS_STATE":
                    # landed_state: 1 = ON_GROUND, 2 = IN_AIR, 3 = TAKEOFF, 4 = LANDING
                    if msg.landed_state >= 2:
                        airborne = True
                    elif msg.landed_state == 1 and airborne:
                        print("[GPS] Landung erkannt (landed_state).")
                        buf.landed.set()

                elif t == "MISSION_COUNT":
                    mission_count = msg.count
                    print(f"[GPS] Survey-Plan geladen: {mission_count} Mission-Items.")
                    # Transaktion sauber beenden (wir brauchen die Items selbst nicht)
                    mav.mav.mission_ack_send(mav.target_system, mav.target_component,
                                             mavutil.mavlink.MAV_MISSION_ACCEPTED)

                elif t == "MISSION_CURRENT":
                    if msg.seq != last_mission_seq:
                        last_mission_seq = msg.seq
                        total = f"/{mission_count - 1}" if mission_count else ""
                        print(f"[GPS] Mission-Item {msg.seq}{total}")

                elif t == "MISSION_ITEM_REACHED":
                    if mission_count and msg.seq >= mission_count - 1 and not survey_done:
                        survey_done = True
                        print("[GPS] Letztes Mission-Item erreicht — Survey fertig, warte auf Landung.")

            try:
                mav.close()
            except Exception:
                pass

        print(f"[GPS] Beendet — {buf.count} GPS-Punkte geloggt.")
