import sys
import numpy as np
from pathlib import Path
import json
import datetime
import jsbeautifier
import zipfile


class LogFileParser:
    def __init__(self):
        pass

    def extract_data(self, log_zip_file):
        extract_directory = self.extract_zip_file(log_zip_file)

        results = []
        for data_file in list(Path(extract_directory).glob("*.csv")):
            if (
                str(data_file)[-10:] != "_fixed.csv"
                and str(data_file)[-9:] != "_MURs.csv"
                and str(data_file)[-8:] != "_NIs.csv"
                and str(data_file)[-15:] != "_timestamps.csv"
            ):
                # print(data_file)

                data_file_fixed = self.fix_csv_file(data_file, file_out=None)
                messages = self.read_data_file(data_file_fixed)

                data = self.parse_messages(messages)
                statistics = self.calculate_statistics(data)

                data_file_path = Path(data_file)
                data_path = Path(data_file_path.parent).joinpath(data_file_path.stem)
                self.save_data(statistics, data, data_path)

                results.append((str(data_path), data, statistics))

                # print(data_file, "done")

        return results

    def extract_zip_file(self, log_zip_file):
        extract_directory = str(log_zip_file)[:-4]

        with zipfile.ZipFile(log_zip_file, "r") as file:
            file.extractall(extract_directory)

        return Path(extract_directory).as_posix()

    def fix_csv_file(self, file_in, file_out=None):
        file_in_path = Path(file_in)

        if file_in_path.suffix != ".csv":
            raise ValueError("No .csv file")

        if not file_in_path.exists():
            raise ValueError("File does not exist")

        if file_out == None:
            file_out_path = Path(file_in_path.parent).joinpath(file_in_path.stem + "_fixed" + file_in_path.suffix)
        else:
            file_out_path = Path(file_out)

        with open(file_in_path, "r") as f:
            file_lines = f.readlines()

        max_num_cols = 0
        for line in file_lines:
            if len(line.strip()) > 0:
                if line.strip()[0] != "#":
                    max_num_cols = max([max_num_cols, line.count(",")])

        with open(file_out_path, "w") as f:
            for line in file_lines:
                line_fixed = line

                if len(line.strip()) > 0:
                    if line.strip()[0] != "#" and line.count(",") > 0:
                        add_string = "," * max([0, max_num_cols - line.count(",")])
                        line_fixed = line[: len(line.rstrip())] + add_string + line[len(line.rstrip()) :]

                f.write(line_fixed)

            # print(file_out_path, "created")

        return file_out_path.as_posix()

    def read_data_file(self, file):
        file_path = Path(file)

        messages = list(np.genfromtxt(file_path, delimiter=",", dtype=np.dtype(str), encoding="UTF-8"))
        for msg_idx in range(len(messages)):
            messages[msg_idx] = list(messages[msg_idx])

            messages[msg_idx][0] = str(messages[msg_idx][0])
            messages[msg_idx][1] = str(messages[msg_idx][1])
            for col_idx in range(2, len(messages[msg_idx])):
                try:
                    messages[msg_idx][col_idx] = int(messages[msg_idx][col_idx])
                except:
                    try:
                        messages[msg_idx][col_idx] = float(messages[msg_idx][col_idx])
                    except:
                        pass

        return messages

    def parse_messages(self, messages):
        data = {}

        sensor_module_ids = list(set([message[2] for message in messages]))

        for sensor_module_id in sensor_module_ids:
            data["sensor" + str(sensor_module_id)] = {}

            NIs_available = any(
                [
                    (
                        1
                        if (
                            message[1][:3] == "0x7"
                            and message[2] == sensor_module_id
                            and message[3] == 3
                            and message[4] == 1
                        )
                        else 0
                    )
                    for message in messages
                ]
            )
            MURs_available = any(
                [
                    (
                        1
                        if (
                            message[1][:3] == "0x7"
                            and message[2] == sensor_module_id
                            and message[3] == 3
                            and message[4] == 2
                        )
                        else 0
                    )
                    for message in messages
                ]
            )

            data["sensor" + str(sensor_module_id)]["timestamps"] = []

            if NIs_available:
                data["sensor" + str(sensor_module_id)]["NIs"] = []

            if MURs_available:
                data["sensor" + str(sensor_module_id)]["MURs"] = []

            NIs_timestamp = ""
            NIs_count = -1
            NIs_line = [-1] * 8 * 4
            MURs_timestamp = ""
            MURs_count = -1
            MURs_line = [-1] * 8 * 4
            for message in messages:
                if (
                    message[1][:3] == "0x7"
                    and message[2] == sensor_module_id
                    and message[3] == 3
                    and (message[4] == 1 or message[4] == 2)
                ):
                    if message[4] == 1:
                        NIs_timestamp = message[0]
                        NIs_count = int(message[5])
                        NIs_line = list(map(int, message[6 : (6 + 8 * 4)]))
                    elif message[4] == 2:
                        MURs_timestamp = message[0]
                        MURs_count = int(message[5])
                        MURs_line = list(map(lambda x: x / 100, message[6 : (6 + 8 * 4)]))

                    if NIs_count == MURs_count:
                        data["sensor" + str(sensor_module_id)]["timestamps"].append(NIs_timestamp)
                        data["sensor" + str(sensor_module_id)]["NIs"].append(NIs_line)
                        data["sensor" + str(sensor_module_id)]["MURs"].append(MURs_line)
                    elif message[4] == 1 and (not MURs_available):
                        data["sensor" + str(sensor_module_id)]["timestamps"].append(NIs_timestamp)
                        data["sensor" + str(sensor_module_id)]["NIs"].append(NIs_line)
                    elif message[4] == 2 and (not NIs_available):
                        data["sensor" + str(sensor_module_id)]["timestamps"].append(MURs_timestamp)
                        data["sensor" + str(sensor_module_id)]["MURs"].append(MURs_line)

        return data

    def to_native_type(self, object):
        f = lambda x: str(x) if (type(x) == datetime.datetime or type(x) == datetime.timedelta) else x.item()

        object_np = np.array(object)

        result = np.array(list(map(f, object_np.flatten()))).reshape(np.shape(object_np)).tolist()

        return result

    def calculate_array_statistics(self, array, only_first_dim=True, simple_statistics=False, decimals=None):
        array_np = np.array(array)

        array_statistics = {}

        if only_first_dim:
            stat_axis = 0
        else:
            stat_num_axis = len(np.shape(array_np)) - 1

            if stat_num_axis <= 1:
                stat_axis = None
            else:
                stat_axis = tuple(range(stat_num_axis))

        if np.shape(array_np)[-1] == 1:
            array_np = array_np[..., 0]

        array_statistics = {}
        array_statistics["min"] = self.to_native_type(np.min(array_np, axis=stat_axis))
        array_statistics["max"] = self.to_native_type(np.max(array_np, axis=stat_axis))
        array_statistics["range"] = self.to_native_type(
            np.max(array_np, axis=stat_axis) - np.min(array_np, axis=stat_axis)
        )

        if not simple_statistics:
            array_statistics["median"] = self.to_native_type(np.median(array_np, axis=stat_axis))
            array_statistics["mean"] = self.to_native_type(np.mean(array_np, axis=stat_axis))
            array_statistics["st_dev"] = self.to_native_type(np.std(array_np, axis=stat_axis))

        if decimals != None:
            for key in array_statistics:
                array_statistics[key] = self.to_native_type(np.round(array_statistics[key], decimals))

        return array_statistics

    def calculate_statistics(self, data):
        statistics = {}

        for key in data:

            statistics[key] = {}
            statistics[key]["module"] = {}
            statistics[key]["spots"] = {}

            timestamp_values = [[datetime.datetime.fromisoformat(timestamp)] for timestamp in data[key]["timestamps"]]
            statistics[key]["module"]["timestamps"] = self.calculate_array_statistics(
                timestamp_values, simple_statistics=True, only_first_dim=False
            )

            if "MURs" in data[key]:
                MUR_values = np.reshape(data[key]["MURs"], (-1, 8, 4, 1))
                statistics[key]["module"]["MURs"] = self.calculate_array_statistics(
                    MUR_values, decimals=3, only_first_dim=False
                )
                statistics[key]["spots"]["MURs"] = self.calculate_array_statistics(
                    MUR_values, decimals=3, only_first_dim=True
                )

            if "NIs" in data[key]:
                NI_values = np.reshape(data[key]["NIs"], (-1, 8, 4, 1))
                statistics[key]["module"]["NIs"] = self.calculate_array_statistics(
                    NI_values, decimals=1, only_first_dim=False
                )
                statistics[key]["spots"]["NIs"] = self.calculate_array_statistics(
                    NI_values, decimals=1, only_first_dim=True
                )

        return statistics

    def save_data(self, statistics, data, data_path):
        data_path = Path(data_path)

        for key in statistics:
            statistics_path = Path(data_path.parent).joinpath(data_path.stem + "_" + key + "_statistics.json")
            with open(statistics_path, "w") as f:
                f.write(jsbeautifier.beautify(json.dumps(statistics[key])))
            # print(statistics_path, "created")

        for key in data:
            delimiter = ","

            timestamps_path = Path(data_path.parent).joinpath(data_path.stem + "_" + key + "_timestamps" + ".csv")
            header = "timestamp"
            np.savetxt(
                timestamps_path,
                data[key]["timestamps"],
                delimiter=delimiter,
                header=header,
                comments="",
                fmt="%s",
            )
            # print(timestamps_path, "created")

            if "MURs" in data[key]:
                MURs_path = Path(data_path.parent).joinpath(data_path.stem + "_" + key + "_MURs" + ".csv")
                header = ""
                for ch_idx in range(1, 8 + 1):
                    for d_idx in range(1, 4 + 1):
                        header = header + delimiter + "MUR_%d_%d" % (ch_idx, d_idx)
                header = header[1:]
                np.savetxt(MURs_path, data[key]["MURs"], delimiter=delimiter, header=header, comments="", fmt="%.3f")
                # print(MURs_path, "created")

            if "NIs" in data[key]:
                NIs_path = Path(data_path.parent).joinpath(data_path.stem + "_" + key + "_NIs" + ".csv")
                header = ""
                for ch_idx in range(1, 8 + 1):
                    for d_idx in range(1, 4 + 1):
                        header = header + delimiter + "NI_%d_%d" % (ch_idx, d_idx)
                header = header[1:]
                np.savetxt(NIs_path, data[key]["NIs"], delimiter=delimiter, header=header, comments="", fmt="%.1f")
                # print(NIs_path, "created")


if __name__ == "__main__":
    log_zip_file = sys.argv[1]

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
