#!/usr/bin/env python3
"""Launch the Linux build of Nest Studio, wait until the window and the CAM backend are up,
quit it with SIGTERM and check that nothing is left behind.

Usage: tools/smoke-test.py [path-to-nest-studio-binary]   (default: ~/opt/nest-studio/nest-studio)
Exit status 0 = pass, 1 = fail. Prints what it found either way.
"""
import os, re, signal, subprocess, sys, time, urllib.request

home = os.path.expanduser("~")
binary = sys.argv[1] if len(sys.argv) > 1 else f"{home}/opt/nest-studio/nest-studio"
logd = f"{home}/.config/Nest Studio/logs"
backend_re = r"^Z:.*3Axis.*Nest Studio Service\.exe$"   # the Wine backend's command line


def pids(pattern):
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if int(p) != os.getpid()]


def read(name):
    try:
        return open(f"{logd}/{name}").read()
    except FileNotFoundError:
        return ""


if pids(f"^{re.escape(binary)}") or pids(backend_re):
    print("an instance (or its backend) is already running; close it first")
    sys.exit(1)
for f in ("main.log", "processing.log"):
    try:
        os.remove(f"{logd}/{f}")
    except FileNotFoundError:
        pass

env = dict(os.environ)
env.pop("ELECTRON_RUN_AS_NODE", None)
proc = subprocess.Popen([binary], cwd=home, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
t0 = time.time()
shown = cam = False
while time.time() - t0 < 90:
    time.sleep(1)
    shown = "startup-main-window-shown" in read("main.log")
    cam = "startup-cam-ready" in read("processing.log")
    if shown and cam:
        break
print(f"window shown={shown}  cam ready={cam}  after {time.time() - t0:.1f}s")
for line in re.findall(r".*(?:startup-cam-ready|startup-main-window-shown|\[error\]).*", read("main.log") + read("processing.log")):
    print("  ", line[:160])
try:
    print("api/version:", urllib.request.urlopen("http://127.0.0.1:9630/api/version", timeout=3).read().decode().strip())
except Exception as e:
    print("api/version failed:", e)
print("backend pids:", pids(backend_re))

print("sending SIGTERM to electron pid", proc.pid)
proc.send_signal(signal.SIGTERM)
try:
    proc.wait(timeout=20)
    print("electron exit code", proc.returncode)
except subprocess.TimeoutExpired:
    print("electron did not exit in 20s; killing it")
    proc.kill()
time.sleep(3)
left_e, left_w = pids(f"^{re.escape(binary)}"), pids(backend_re)
listening = ":9630" in subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout
print(f"leftover electron: {left_e}  leftover wine backend: {left_w}  port 9630 listening: {listening}")
ok = shown and cam and not left_e and not left_w and not listening
print("SMOKE TEST", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
