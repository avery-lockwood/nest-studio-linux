# Nest Studio on Linux

Nest Studio, the CAM program for Nestworks CNC machines, only ships for Windows and macOS.
This guide explains how to run it on Linux with the frontend native and the CAM backend under
Wine, what the build script does, and what to check when a new release comes out.

| | |
|---|---|
| Nest Studio | 1.2.0 |
| Electron | 39.8.10 |
| Tested on | Fedora 42, Wine 10.20 |
| Startup to ready | about 4 s |

> Nothing here redistributes Nest Studio. You need your own copy of the Windows installer;
> the script rearranges and patches what the installer already contains.

Contents: [Install](#install) · [How it works](#how-it-works) · [Updating](#updating) ·
[Troubleshooting](#troubleshooting) · [For maintainers](#for-maintainers) ·
[What is and isn't tested](#what-is-and-isnt-tested)

## Install

Packages first. On Fedora:

```sh
sudo dnf install wine nodejs unzip p7zip p7zip-plugins ImageMagick
```

Wine runs the backend, Node unpacks the app archive and applies the patches, 7z opens the
installer (without it the script falls back to the system libarchive through a small private
Python environment), and ImageMagick only makes the desktop icon. Other distributions need the
same packages under their own names.

**N10. Download the Windows installer.** The update feed at
`https://studio.worksbase.com/software/Nestworks/prod/win/latest.yml` names the current file
(`Nest Studio Setup 1.2.0.exe`, about 630 MB) and its checksum; the file sits in the same
directory as the yml.

**N20. Get the build script and run it on the installer.**

```sh
git clone https://github.com/avery-lockwood/nest-studio-linux.git
./nest-studio-linux/nest-studio-make-linux-build "Nest Studio Setup 1.2.0.exe"
```

The first run unpacks the installer and downloads the matching Electron build; after that a
rebuild takes about five seconds. The result lands in `~/opt/nest-studio` with a launcher in
`~/.local/bin/nest-studio`, an application-menu entry, and a `README-LINUX.md` describing the
layout. The script copies itself to `~/.local/bin` so later updates are one command.

**N30. Start it.** `nest-studio` in a terminal, or "Nest Studio" in the application menu. The
loading screen shows three phases; the CAM phase is the Wine backend coming up, two to six
seconds depending on whether Wine is already warm.

## How it works

The Windows release is an ordinary Electron app plus a separate CAM server. The frontend is
plain JavaScript and runs on the stock Linux Electron build. The server is a PyInstaller bundle
whose CAM kernels are compiled Windows Python extensions with no source, so it stays a Windows
program and runs under Wine. The app itself starts, health-checks and restarts the backend over
HTTP, exactly as it does on Windows.

```
 native Linux                              Windows code under Wine
┌──────────────────────────────┐          ┌──────────────────────────────────┐
│ Electron frontend            │  HTTP    │ CAM backend                      │
│ stock Electron 39, binary    │ 127.0.0.1│ "Nest Studio Service.exe",       │
│ renamed nest-studio, loads   │  :9630   │ started through a bash shim named │
│ the extracted resources/app/ │ ───────► │ "Nest Studio Service" (the file  │
│ serial ports work natively   │          │ the app looks for on non-Windows) │
└──────────────────────────────┘          └──────────────────────────────────┘
┌──────────────────────────────┐          ┌──────────────────────────────────┐
│ G-code check                 │          │ Patches                          │
│ shim gcode_validator_cli     │          │ four small edits to the main     │
│ execs Wine on the Windows    │          │ process, originals kept as .orig │
│ validator; Wine accepts      │          │ Linux allowed as backend platform│
│ Linux paths                  │          │ backend killed on quit, ss port  │
│                              │          │ diagnostics, auto-updater skipped│
└──────────────────────────────┘          └──────────────────────────────────┘
```

Two details carry most of the weight. Electron decides whether an app is "packaged" from the
executable's name, and only a packaged app resolves `resources/server` where the installer puts
it, which is why the binary is renamed rather than run as `electron app.asar`. And the app
spawns the backend with output pipes it never reads, so the shim redirects Wine's output to
`~/.config/Nest Studio/logs/cam-wine.log`; otherwise the backend would stall once the pipe
filled.

## Updating

```sh
nest-studio-make-linux-build "Nest Studio Setup <new version>.exe"
```

The previous build is kept as `~/opt/nest-studio.prev`, and settings and projects in
`~/.config/Nest Studio` are untouched. Each patch has to match the upstream code an exact
number of times; if a release changes that code, the script stops and names the patch instead
of producing a half-patched app. The maintainer section below lists where to look.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Nothing happens, exit code 0 | `ELECTRON_RUN_AS_NODE=1` is set in the environment; VS Code terminals do this. The launcher unsets it. If you run the binary directly, unset it first. |
| Loading screen ends in "failed" | The backend did not answer on port 9630 within 60 seconds. Run it by hand to see Wine's errors: `cd ~/opt/nest-studio/resources/server/3Axis && wine "Nest Studio Service.exe"`, then `curl 127.0.0.1:9630/api/version` from another terminal. |
| "CAM port 9630 is still occupied" | A stray backend from a crash: `pkill -f "Nest Studio Service.exe"`. |
| No serial ports | Add your user to the `dialout` group and log in again. |
| Log says the GPU is disabled | The app writes that block before its GPU process exists, so it always reads "disabled". WebGL works; ignore it. |

Logs live in `~/.config/Nest Studio/logs/`: `main.log` for the app, `processing.log` for the
backend supervisor, `cam-wine.log` for Wine.

## For maintainers

Things learned the hard way, so nobody has to learn them twice.

**The app kills processes by name.** On start and quit upstream runs
`pkill -9 -f "Nest Studio Service"`. That matches any process whose command line contains the
text: a terminal tailing a log at that path, an editor with the shim open, your own debugging
shell. The Linux patch narrows it to the Wine process (command line ending in
`3Axis\Nest Studio Service.exe`). If you ever debug an unpatched build and your shell dies with
exit code 1 the moment the app starts, this is why.

**Backslashes through four layers.** That pattern passes through a shell heredoc, a JS template
literal and a JS string before pkill sees it. One layer too few and `\3Axis` becomes a
back-reference, pkill exits 2, and the app treats that as a fatal startup error. The patch
builds the pattern from `String.fromCharCode(92)` so no layer can touch it.

**The installer is a 7z archive in disguise.** Inside the NSIS installer the app files sit as
one uncompressed 7z archive (`app-64.7z`), so the script finds the 7z signature and carves the
archive out with Python; any 7z tool or libarchive extracts it. Pure-Python readers fail
because the executables use the BCJ2 filter. Running the installer under Wine with `/S` does
not work; it aborts before writing anything.

**Where the patches anchor.** In `resources/app/out/main/index.js.orig`: the string
`"CAM backend is not supported on this platform:"` (platform check in `spawnServeProcess`), the
two `pkill -9 -f` call sites (`restartServeProcess`, `terminateServeProcess`),
`"/usr/sbin/lsof"` in `inspectCamPortOwners`, and `const CAM_HTTP_PORT`. In
`upgrade-<hash>.js`: `shouldSkipUnpackagedUpdateCheck`. Also confirm the non-Windows backend
path is still `./3Axis/Nest Studio Service` and the validator path
`gcodeCheck/gcode_validator_cli`; the shims are written under those names.

**Verifying a build.** `tools/smoke-test.py` launches the app, waits for the window and the
backend, checks `/api/version`, quits with SIGTERM and verifies nothing is left running. Expect
the backend ready in about 3 seconds and the window at about 4.

## What is and isn't tested

Verified: startup, backend health and supervision, quitting with no leftovers, the G-code
validator, WebGL rendering, repeated rebuilds from the installer. Not yet exercised: long
toolpath computations (the app's 3-second heartbeat needs the backend to keep answering while
busy), 4-axis and image-to-STL features (ONNX models under Wine), the account login and its
`neststudio://` callback, and connecting to a machine over the network or USB. Reports on any
of these are the most useful contribution.

---

Build script, smoke test and the full porting notes are in the **nest-studio-linux**
repository (https://github.com/avery-lockwood/nest-studio-linux). Scripts and notes are MIT licensed; Nest Studio belongs to its owners and is not
distributed.
