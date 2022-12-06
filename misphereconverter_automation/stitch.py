#!/usr/bin/env python3
# coding: utf-8
import argparse
import os
import shlex
import subprocess
import time
from typing import Literal, NamedTuple, cast

VM_SRC_DIR = "/mnt/sdcard/panosrc/"
VM_DEST_DIR = "/mnt/sdcard/MiSphereConverter/"
SETTINGS_FILE = (
    "/data/data/com.hirota41.misphereconverter/shared_prefs/"
    "com.hirota41.misphereconverter_preferences.xml"
)
PACKAGE_NAME = "com.hirota41.misphereconverter"


class Pose(NamedTuple):
    yaw: float
    pitch: float
    roll: float


def check_file_valid(path: str, is_png: bool) -> bool:
    if is_png:
        ending = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"
    else:
        ending = b"\xff\xd9"
    size = os.path.getsize(path)
    if size < len(ending):
        return False
    with open(path, "rb") as f:
        f.seek(size - len(ending))
        return f.read() == ending


class MSCCleint:
    def __init__(self, adb_exec: str):
        self.adb_exec = adb_exec

    def call_adb(self, *args: str, raiseonerr: bool = True) -> tuple[int, str, str]:
        command_line = self.adb_exec + " " + " ".join(map(shlex.quote, args))
        retries = 10
        while True:
            try:
                proc = subprocess.run(
                    command_line,
                    shell=True,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if raiseonerr and proc.returncode != 0:
                    raise Exception(
                        "adb failed. Command: %s. Exit status: %s. Stdout: %s. Stderr: %s"
                        % (command_line, proc.returncode, proc.stdout, proc.stderr)
                    )
                return proc.returncode, proc.stdout, proc.stderr
            except subprocess.CalledProcessError:
                retries -= 1
                if not retries:
                    raise
                time.sleep(1)

    def ensure_empty_vm_dir(self, dir_: str) -> None:
        self.call_adb("shell", "mkdir -p %s" % dir_)
        self.call_adb("shell", "touch %s/dummy" % dir_)
        self.call_adb("shell", "rm %s/*" % dir_)

    def copy_file_to_vm(self, filename: str, dest_path: str) -> None:
        assert os.path.exists(filename), filename
        self.call_adb("push", filename, dest_path)

    def copy_file_from_vm(self, filename: str, dest_path: str) -> None:
        self.call_adb("pull", filename, dest_path)

    # pylint: disable-next=R0913
    def start_msc(
        self,
        image_filename: str,
        yaw_pitch_roll: Pose | None,
        jpeg_quality: int = 95,
        depurple: bool = True,
        png: bool = False,
        adaptive: int = 3,
    ) -> None:
        retries = 10
        while retries:
            self.call_adb("shell", "am force-stop %s" % PACKAGE_NAME)
            command = (
                "am start -a STITCH_AUTOMATED --eu android.intent.extra.STREAM file://%s%s"
                % (VM_SRC_DIR, image_filename)
            )
            if yaw_pitch_roll is not None:
                command += (
                    " --ez ignore_exif true --ef yaw %.2f --ef pitch %.2f --ef roll %.2f"
                    % yaw_pitch_roll
                )
            command += " --ei jpeg_q %s" % jpeg_quality
            command += " --ez depurple %s" % ("true" if depurple else "false")
            command += " --ez lossless %s" % ("true" if png else "false")
            command += " --ei adaptive %s" % adaptive
            command += " %s/.IntentActivity" % PACKAGE_NAME
            self.call_adb("shell", command)
            time.sleep(1)
            if self.check_msc_alive():
                return
            retries -= 1
        raise Exception("Too many retries to run MSC")

    def list_vm_dir(self, dir_: str) -> list[str]:
        _, stdout, _ = self.call_adb("shell", "ls %s" % dir_)
        return stdout.splitlines()

    def check_msc_alive(self) -> bool:
        retcode, stdout, stderr = self.call_adb(
            "shell", "ps | grep %s" % PACKAGE_NAME, raiseonerr=False
        )
        if not stderr:
            if retcode == 0 and stdout:
                return True
            if retcode in (0, 1) and not stdout:
                return False
        raise Exception(
            'Unexpected result from ps | grep: code="%s", stdout="%s", stderr="%s"'
            % (retcode, stdout, stderr)
        )


# pylint: disable-next=R0913
def process_image(
    src_filename: str,
    dest_filename: str,
    png: bool = False,
    calibration_filename: str | None = None,
    pose: Pose | None = None,
    jpeg_quality: int = 95,
    depurple: bool = True,
    adaptive: int = 3,
    adb_exec: str = "adb",
) -> None:
    client = MSCCleint(adb_exec)
    client.ensure_empty_vm_dir(VM_SRC_DIR)
    client.ensure_empty_vm_dir(VM_DEST_DIR)
    if calibration_filename:
        client.copy_file_to_vm(calibration_filename, VM_DEST_DIR)
    client.copy_file_to_vm(src_filename, VM_SRC_DIR)
    retries = 60
    ready_files = []
    client.start_msc(
        os.path.basename(src_filename), pose, jpeg_quality, depurple, png, adaptive
    )
    extension = ".png" if png else ".jpg"
    while retries:
        ready_files = [
            fn
            for fn in client.list_vm_dir(VM_DEST_DIR)
            if fn.lower().endswith(extension)
        ]
        if ready_files:
            break
        retries -= 1
        time.sleep(1)
    assert len(ready_files) == 1
    retries = 10
    while True:
        client.copy_file_from_vm(VM_DEST_DIR + ready_files[0], dest_filename)
        if check_file_valid(dest_filename, png):
            break
        retries -= 1
        if not retries:
            raise Exception("Too many retries while retrieving file")
        time.sleep(1)


class Args(argparse.Namespace):
    src: str
    dest: str
    quality: int
    png: bool
    no_depurple: bool
    distance: Literal[0, 1, 2, 3]
    calibration_file: str | None
    pose: str | None
    adb: str


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("src", help="Source file name")
    parser.add_argument("dest", help="Output filename")
    parser.add_argument("-q", "--quality", default=95, help="JPEG quality")
    parser.add_argument(
        "--png", action="store_true", default=False, help="Save file in PNG format"
    )
    parser.add_argument(
        "--no-depurple",
        action="store_true",
        default=False,
        help="Disable removing purple fringe",
    )
    parser.add_argument(
        "--distance",
        type=int,
        choices=[0, 1, 2, 3],
        help="0: 1-2 m, 1: 2-3 m, 2: < 5 m, 3: auto",
        default=3,
    )
    parser.add_argument("-c", "--calibration-file")
    parser.add_argument(
        "--pose",
        help="yaw,pitch,roll in degrees. If not specified use pose from image exif data.",
    )
    parser.add_argument("--adb", help="adb executable", default="adb")
    conf = parser.parse_args(namespace=Args())

    pose: Pose | None
    if conf.pose is not None:
        values = tuple(map(float, conf.pose.split(",")))
        assert len(values) == 3
        pose = cast(Pose, values)
    else:
        pose = None
    process_image(
        conf.src,
        conf.dest,
        jpeg_quality=conf.quality,
        depurple=not conf.no_depurple,
        png=conf.png,
        adaptive=conf.distance,
        calibration_filename=conf.calibration_file,
        pose=pose,
        adb_exec=conf.adb,
    )


if __name__ == "__main__":
    main()
