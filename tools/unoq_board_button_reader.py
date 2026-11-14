#!/usr/bin/env python3
"""Runs ON the Uno Q's Linux side. Reads an input device and prints PRESS on each key-down (unbuffered).

Pushed and launched automatically by tools/unoq_button_adb.py over the USB/adb link. The Uno Q's on-board white
button is the power key at /dev/input/event1 (code 116, KEY_POWER); a short press is safe and does not shut down.
"""
import struct
import sys

dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/event1"
f = open(dev, "rb", buffering=0)
size = struct.calcsize("@llHHi")   # input_event: time(2 longs), type u16, code u16, value s32
while True:
    data = f.read(size)
    if not data or len(data) < size:
        continue
    _sec, _usec, etype, _code, value = struct.unpack("@llHHi", data)
    if etype == 1 and value == 1:   # EV_KEY, key down = a fresh press
        sys.stdout.write("PRESS\n")
        sys.stdout.flush()
