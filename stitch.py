#!/usr/bin/env python3
# coding: utf-8
import os
import subprocess
import argparse
import time
import tempfile
import shlex

vm_src_dir = '/mnt/sdcard/panosrc/'
vm_dest_dir = '/mnt/sdcard/MiSphereConverter/'
settings_file = '/data/data/com.hirota41.misphereconverter/shared_prefs/com.hirota41.misphereconverter_preferences.xml'
package_name = 'com.hirota41.misphereconverter'


def check_file_valid(path, is_png):
    if is_png:
        ending = b'\x00\x00\x00\x00IEND\xae\x42\x60\x82'
    else:
        ending = b'\xff\xd9'
    size = os.path.getsize(path)
    if size < len(ending):
        return False
    with open(path, 'rb') as f:
        f.seek(size - len(ending))
        return f.read() == ending


class MSCCleint:
    def __init__(self, adb_exec):
        self.adb_exec = adb_exec

    def call_adb(self, *args, raiseonerr=True):
        command_line = self.adb_exec + ' ' + ' '.join(map(shlex.quote, args))
        retries = 10
        while True:
            try:
                p = subprocess.Popen(command_line, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = p.communicate()
                if raiseonerr and p.returncode != 0:
                    raise Exception('adb failed. Command: %s. Exit status: %s. Stdout: %s. Stderr: %s'
                                    % (command_line, p.returncode, stdout, stderr))
                return p, stdout, stderr
            except subprocess.CalledProcessError:
                retries -= 1
                if not retries:
                    raise
                time.sleep(1)

    def ensure_empty_vm_dir(self, dir_):
        self.call_adb('shell', 'mkdir -p %s' % dir_)
        self.call_adb('shell', 'touch %s/dummy' % dir_)
        self.call_adb('shell', 'rm %s/*' % dir_)

    def copy_file_to_vm(self, filename, dest_path):
        assert os.path.exists(filename), filename
        self.call_adb('push', filename, dest_path)

    def copy_file_from_vm(self, filename, dest_path):
        self.call_adb('pull', filename, dest_path)

    def start_msc(self, image_filename, yaw_pitch_roll):
        retries = 10
        while retries:
            self.call_adb('shell', 'am force-stop %s' % package_name)
            command = 'am start -a STITCH_AUTOMATED --eu android.intent.extra.STREAM file://%s%s' % (vm_src_dir, image_filename)
            if yaw_pitch_roll is not None:
                command += ' --ez ignore_exif true --ef yaw %.2f --ef pitch %.2f --ef roll %.2f' % tuple(yaw_pitch_roll)
            command += ' %s/.IntentActivity' % package_name
            self.call_adb('shell', command)
            time.sleep(1)
            if self.check_msc_alive():
                return
            retries -= 1
        raise Exception('Too many retries to run MSC')

    def list_vm_dir(self, dir_):
        _, stdout, _ = self.call_adb('shell', 'ls %s' % dir_)
        return stdout.decode('utf-8').splitlines()

    def check_msc_alive(self):
        p, stdout, stderr = self.call_adb('shell', 'ps | grep %s' % package_name, raiseonerr=False)
        if not stderr:
            if p.returncode == 0 and stdout:
                return True
            if p.returncode in (0, 1) and not stdout:
                return False
        raise Exception('Unexpected result from ps | grep: code="%s", stdout="%s", stderr="%s"' % (p.returncode, stdout, stderr))

    def write_settings(self, jpeg_quality=95, depurple=True, png=False, adaptive=3):
        xml = '''<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
            <map>
                <int name="adaptive" value="{adaptive}" />
                <boolean name="depurple" value="{depurple}" />
                <boolean name="tiff" value="{png}" />
                <int name="jpg_q" value="{quality}" />
            </map>
        '''.format(quality=jpeg_quality, adaptive=adaptive,
               depurple='true' if depurple else 'false', png='true' if png else 'false')
        with tempfile.NamedTemporaryFile(mode='w') as config_file:
            config_file.write(xml)
            config_file.flush()
            self.copy_file_to_vm(config_file.name, settings_file)


def process_image(src_filename, dest_filename, png=False, calibration_filename=None, pose=None,
                  jpeg_quality=95, depurple=True, adaptive=3, adb_exec='adb'):
    client = MSCCleint(adb_exec)
    client.write_settings(jpeg_quality, depurple, png, adaptive)
    client.ensure_empty_vm_dir(vm_src_dir)
    client.ensure_empty_vm_dir(vm_dest_dir)
    if calibration_filename:
        client.copy_file_to_vm(calibration_filename, vm_dest_dir)
    client.copy_file_to_vm(src_filename, vm_src_dir)
    retries = 60
    ready_files = []
    client.start_msc(os.path.basename(src_filename), pose)
    extension = '.png' if png else '.jpg'
    while retries:
        ready_files = [fn for fn in client.list_vm_dir(vm_dest_dir) if fn.lower().endswith(extension)]
        if ready_files:
            break
        retries -= 1
        time.sleep(1)
    assert len(ready_files) == 1
    retries = 10
    while True:
        client.copy_file_from_vm(vm_dest_dir + ready_files[0], dest_filename)
        if check_file_valid(dest_filename, png):
            break
        retries -= 1
        if not retries:
            raise Exception('Too many retries while retrieving file')
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('src', help='Source file name')
    parser.add_argument('dest', help='Output filename')
    parser.add_argument('-q', '--quality', default=95, help='JPEG quality')
    parser.add_argument('--png', action='store_true', default=False, help='Save file in PNG format')
    parser.add_argument('--no-depurple', action='store_true', default=False, help='Disable removing purple fringe')
    parser.add_argument('--distance', type=int, choices=[0, 1, 2, 3], help='0: 1-2 m, 1: 2-3 m, 2: < 5 m, 3: auto',
                        default=3)
    parser.add_argument('-c', '--calibration-file')
    parser.add_argument('--pose', help='yaw,pitch,roll in degrees. If not specified use pose from image exif data.')
    parser.add_argument('--adb', help='adb executable', default='adb')
    conf = parser.parse_args()

    if conf.pose is not None:
        pose = map(float, conf.pose.split(','))
    else:
        pose = None
    process_image(conf.src, conf.dest,
                  jpeg_quality=conf.quality, depurple=not conf.no_depurple, png=conf.png, adaptive=conf.distance,
                  calibration_filename=conf.calibration_file, pose=pose, adb_exec=conf.adb)


if __name__ == '__main__':
    main()
