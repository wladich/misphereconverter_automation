#!/usr/bin/env python2
# coding: utf-8
import sys
import os
import subprocess
import argparse
import time
import tempfile
import shutil
import struct
import math
import numpy as np

msc_vm_name = 'MiSphereConverter'
#adb_exec = '/home/w/Android/Sdk/platform-tools/adb'
adb_exec = '/home/w/opt/genymotion/tools/adb'
vboxmanage_exec = 'vboxmanage'
vm_src_dir = '/mnt/sdcard/panosrc/'
vm_dest_dir = '/mnt/sdcard/MiSphereConverter/'
runner_class_name = 'com.example.w.sendtomsc/.MainActivity'


def expand_src(src_list):
    filenames = []
    for el in src_list:
        if os.path.isdir(el):
            for fn in os.listdir(el):
                if os.path.splitext(fn)[1].lower() == '.jpg':
                    filenames.append(os.path.join(el, fn))
        else:
            filenames.append(el)
    return filenames


def check_call_retry(*args, **kwargs):
    retries = 10
    while True:
        try:
            return subprocess.check_call(*args, **kwargs)
        except subprocess.CalledProcessError:
            retries -= 1
            if not retries:
                raise
            time.sleep(1)


def ensure_empty_vm_dir(vm_name, dir_):
    check_call_retry([adb_exec, 'shell', 'mkdir -p %s' % dir_])
    check_call_retry([adb_exec, 'shell', 'touch %s/dummy' % dir_])
    check_call_retry([adb_exec, 'shell', 'rm %s/*' % dir_])


def copy_file_to_vm(filename, dest_path):
    assert os.path.exists(filename)
    check_call_retry([adb_exec, 'push', filename, dest_path], stdout=subprocess.PIPE)


def copy_file_from_vm(filename, dest_path):
    check_call_retry([adb_exec, 'pull', filename, dest_path], stdout=subprocess.PIPE)


def check_file_valid(path):
    size = os.path.getsize(path)
    if size < 2:
        return False
    with open(path) as f:
        f.seek(size - 2)
        return f.read() == '\xff\xd9'


def start_msc(image_filename):
    retries = 10
    while retries:
        check_call_retry([adb_exec, 'shell',
                               'am force-stop com.hirota41.mijiaconverter'])
        check_call_retry([
            adb_exec, 'shell',
            'am start -a android.intent.action.SEND --eu android.intent.extra.STREAM file://%s%s com.hirota41.mijiaconverter/.IntentActivity' %
            (vm_src_dir, image_filename)], stdout=subprocess.PIPE)
        time.sleep(1)
        if check_msc_alive():
            return
        retries -= 1
    raise Exception('Too many retries to run MSC')


def list_vm_dir(dir_):
    return subprocess.check_output([adb_exec, 'shell', 'ls %s' % dir_]).splitlines()


def check_msc_alive():
    p = subprocess.Popen([adb_exec, 'shell', 'ps | grep com.hirota41.mijiaconverter'],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    if not stderr:
        if p.returncode == 0 and stdout:
            return True
        if p.returncode in (0, 1) and not stdout:
            return False
    raise Exception('Unexpected result from ps | grep: code="%s", stdout="%s", stderr="%s"' % (p.returncode, stdout, stderr))


def set_jpeg_quality(quality):
    xml = '''<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <int name="jpg_q" value="%s" />
</map>''' % quality
    with tempfile.NamedTemporaryFile() as config_file:
        config_file.write(xml)
        config_file.flush()
        copy_file_to_vm(config_file.name, '/data/data/com.hirota41.mijiaconverter/shared_prefs/com.hirota41.mijiaconverter_preferences.xml')


def set_user_comment(filename, data):
    user_comment_start = '\x86\x92\x07\x00$\x00\x00\x00'
    with open(filename, 'r+b') as f:
        s = f.read(4096)
        i = s.index(user_comment_start)
        if i > 4096 - (len(user_comment_start) + 2):
            raise Exception
        i += len(user_comment_start)
        offset = struct.unpack('I', s[i:i + 4])[0] + 12
        f.seek(offset)
        f.write(data)


def make_rotation_matrix(z, x, y):
    mats = []

    z = math.radians(z)
    cos_z = math.cos(z)
    sin_z = math.sin(z)
    z_mat = [
        [cos_z, -sin_z, 0],
        [sin_z, cos_z, 0],
        [0, 0, 1]
    ]
    mats.append(np.array(z_mat))

    y = math.radians(y)
    cos_y = math.cos(y)
    sin_y = math.sin(y)
    y_mat = [
        [cos_y, 0, sin_y],
        [0, 1, 0],
        [-sin_y, 0, cos_y]
    ]
    mats.append(np.array(y_mat))

    x = math.radians(x)
    cos_x = math.cos(x)
    sin_x = math.sin(x)
    x_mat = [
        [1, 0, 0],
        [0, cos_x, -sin_x],
        [0, sin_x, cos_x]
    ]
    mats.append(np.array(x_mat))

    return reduce(np.matrix.dot, mats)


def make_exif_matrix(yaw, pitch, roll):
    mat = list(make_rotation_matrix(-yaw, -pitch, roll).flatten())
    s = struct.pack('f' * 9, *mat)
    return s


def process_image(src_filename, dest_filename, calibration_filename=None, pose=None):
    ensure_empty_vm_dir(msc_vm_name, vm_src_dir)
    ensure_empty_vm_dir(msc_vm_name, vm_dest_dir)
    if calibration_filename:
        copy_file_to_vm(calibration_filename, vm_dest_dir)
    if pose is None:
        work_filename = src_filename
        copy_file_to_vm(work_filename, vm_src_dir)
    else:
        yaw, pitch, roll = pose
        with tempfile.NamedTemporaryFile() as temp_file:
            work_filename = temp_file.name
            shutil.copy(src_filename, work_filename)
            mat = make_exif_matrix(yaw, pitch, roll)
            set_user_comment(work_filename, mat)
            copy_file_to_vm(work_filename, vm_src_dir)
    retries = 60
    ready_files = []
    start_msc(os.path.basename(work_filename))
    while retries:
        ready_files = [fn for fn in list_vm_dir(vm_dest_dir) if fn.lower().endswith('.jpg')]
        if ready_files:
            break
        retries -= 1
        time.sleep(1)
    assert len(ready_files) == 1
    retries = 10
    while True:
        copy_file_from_vm(vm_dest_dir + ready_files[0], dest_filename)
        if check_file_valid(dest_filename):
            break
        retries -= 1
        if not retries:
            raise Exception('Too many retries while retrieving file')
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('src', nargs='+')
    parser.add_argument('dest', help='Destination directory. If single source provided can also be a filename.')
    parser.add_argument('-q', '--quality', default=80, help='JPEG quality')
    parser.add_argument('-c', '--calibration-file')
    parser.add_argument('--pose', help='yaw,pitch,roll  in degrees. If not specified use pose from image exif data.')
    conf = parser.parse_args()

    is_src_single_file = len(conf.src) == 1 and os.path.isfile(conf.src[0])
    if not is_src_single_file and not os.path.isdir(conf.dest):
        print '%s is not a directory or does not exists' % conf.dest
        exit(1)

    src_filenames = expand_src(conf.src)
    if conf.pose is not None:
        pose = map(float, conf.pose.split(','))
    else:
        pose = None
    set_jpeg_quality(conf.quality)
    for i, filename in enumerate(src_filenames):
        print '\r%s / %s' % (i, len(src_filenames)),
        sys.stdout.flush()
        if is_src_single_file and not os.path.isdir(conf.dest):
            dest_filename = conf.dest
        else:
            dest_filename = os.path.join(conf.dest, os.path.basename(filename))
        process_image(filename, dest_filename, conf.calibration_file, pose)
        print '\r%s / %s' % (i + 1, len(src_filenames)),
        sys.stdout.flush()


if __name__ == '__main__':
    main()
