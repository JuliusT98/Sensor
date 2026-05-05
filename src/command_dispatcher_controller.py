#!/usr/bin/env python3

import requests
import json
import time
import numpy as np
import jsbeautifier
from pathlib import Path
from log_file_parser import LogFileParser


class CommandDispatcherController:
    def __init__(self, url):
        self.url = url

    def get_status(self):
        r = requests.get(self.url + "/status/")
        r.raise_for_status()

        return r.json()

    def get_connected_sensors(self):
        response = self.get_status()

        connected_sensors = []
        for sensor in response["sensors"]:
            if sensor["sensor_error"] == 0:
                connected_sensors.append(sensor["id"])

        return connected_sensors

    def get_data_stream_configuration(self):
        r = requests.get(self.url + "/receive_measurement/")
        r.raise_for_status()

        return r.json()

    def set_data_stream_configuration(
        self, sensor_module_id, NI_stream_active=False, MUR_stream_active=False, data_stream_period=100
    ):
        payload = {}

        payload["sensorID"] = sensor_module_id

        if NI_stream_active:
            payload["ni"] = True
        else:
            payload["ni"] = False

        if MUR_stream_active:
            payload["mur"] = True
        else:
            payload["mur"] = False

        payload["datastreamrate"] = data_stream_period

        payload = "[" + json.dumps(payload) + "]"

        r = requests.post(self.url + "/receive_measurement/", data=payload)
        r.raise_for_status()

        return r.text

    def start_measurements(self):
        r = requests.post(self.url + "/start_measurement_all_sensors/")
        r.raise_for_status()

        return r.text

    def stop_measurements(self):
        r = requests.post(self.url + "/stop_measurement_all_sensors/")
        r.raise_for_status()

        return r.text

    def get_measurements(self):
        r = requests.get(self.url + "/get_measurements/")
        r.raise_for_status()

        return r.json()

    def measurements_to_list(self, measurements, sensor_module_id):
        measurement = []
        for sensor in measurements["sensors"]:
            if sensor["id"] == sensor_module_id:
                for ch in range(1, 8 + 1):
                    for d in range(1, 4 + 1):
                        measurement.append(sensor["ch" + str(ch)]["d" + str(d) + "_ni1"])

                break

        return measurement

    def delete_measurement_log(self):
        r = requests.post(self.url + "/delete_measurement_log/")
        r.raise_for_status()

        return r.text

    def start_measurement_log(self):
        r = requests.post(self.url + "/start_measurement_log/")
        r.raise_for_status()

        return r.text

    def stop_measurement_log(self):
        r = requests.post(self.url + "/stop_measurement_log/")
        r.raise_for_status()

        return r.text

    def download_measurement_log(self, directory=None, file_name=None):
        r = requests.get(self.url + "/download_measurement_log/")
        r.raise_for_status()

        file_path = None
        if "content-disposition" in r.headers:
            if 'filename="' in r.headers["content-disposition"]:
                if file_name is None:
                    file_name = r.headers["content-disposition"].split('filename="')[-1].split('"')[0]

                if directory is None:
                    file_path = Path(file_name)
                else:
                    file_path = Path(directory) / file_name

                Path(file_path).parent.mkdir(parents=True, exist_ok=True)

                open(file_path, "wb").write(r.content)

        return file_path


if __name__ == "__main__":
    command_dispatcher_controller = CommandDispatcherController("http://192.168.2.30:8100")

    print("List connected sensors")
    print(command_dispatcher_controller.get_connected_sensors())

    print("\nGet data stream configuration")
    print(command_dispatcher_controller.get_data_stream_configuration())
    print("Set data stream configuration")
    print(
        command_dispatcher_controller.set_data_stream_configuration(
            1, NI_stream_active=True, MUR_stream_active=True, data_stream_period=50
        )
    )

    print("\nStart sensor measurements")
    print(command_dispatcher_controller.start_measurements())

    print("\nRetrieve single measurement")
    measurements = command_dispatcher_controller.get_measurements()
    print(command_dispatcher_controller.measurements_to_list(measurements, 1))

    print("\nDelete old measurement log")
    print(command_dispatcher_controller.delete_measurement_log())
    print("Start measurement log")
    print(command_dispatcher_controller.start_measurement_log())
    time.sleep(5)
    print("Stop measurement log")
    print(command_dispatcher_controller.stop_measurement_log())
    print("Download measurement log")
    log_zip_file = command_dispatcher_controller.download_measurement_log(directory="log_files")
    print(log_zip_file)

    print("\nDelete old measurement log")
    print(command_dispatcher_controller.delete_measurement_log())

    print("\nStop sensor measurements")
    print(command_dispatcher_controller.stop_measurements())

    print("\nParse log file")
    results = LogFileParser().extract_data(log_zip_file)

    for data_path, data, statistics in results:
        print("data path:", data_path)

        print("\ndata:")
        for sensor in data:
            for key in data[sensor]:
                print(sensor, key, "shape:", np.shape(data[sensor][key]))

        print("\nstatistics:")
        for sensor in statistics:
            print(sensor, "module:", jsbeautifier.beautify(json.dumps(statistics[sensor]["module"])))
            print(sensor, "spots:", jsbeautifier.beautify(json.dumps(statistics[sensor]["spots"])))

    print("\nDone")
