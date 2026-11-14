"""Print (and optionally append to a file) a board's serial log for a number of seconds. Needs pyserial."""
import argparse
import subprocess
import sys
import time

import serial


def open_port(name: str) -> serial.Serial:
    port = serial.Serial()
    port.port = name
    port.baudrate = 115200
    port.timeout = 0.2
    # Native USB-Serial/JTAG: toggling DTR/RTS can drop the chip into download mode, so leave both low.
    port.dtr = False
    port.rts = False
    for _ in range(50):
        try:
            port.open()
            return port
        except serial.SerialException:
            time.sleep(0.1)
    sys.exit(f"cannot open {name} (another program has it, or wrong port?)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("seconds", type=float)
    parser.add_argument("outfile", nargs="?")
    parser.add_argument("--reset", action="store_true",
                        help="restart the board first (watchdog reset; a plain reset leaves it in download mode)")
    args = parser.parse_args()
    if args.reset:
        subprocess.run([sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", args.port,
                        "--after", "watchdog_reset", "read_mac"], capture_output=True)
        time.sleep(0.3)
    port = open_port(args.port)
    out = open(args.outfile, "a", encoding="utf-8") if args.outfile else None
    stop_at = time.time() + args.seconds
    try:
        while time.time() < stop_at:
            try:
                raw = port.readline()
            except serial.SerialException:
                time.sleep(0.2)
                continue
            if not raw:
                continue
            line = time.strftime("%H:%M:%S ") + raw.decode("utf-8", "replace").rstrip()
            print(line, flush=True)
            if out:
                out.write(line + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
        if out:
            out.close()


if __name__ == "__main__":
    main()
