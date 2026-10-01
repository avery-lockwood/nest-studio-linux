# nest-studio-linux

Run [Nest Studio](https://www.nestworks.ai/) (the CAM software for Nestworks CNC machines,
by Elephant Robotics) on Linux. Upstream only ships Windows and macOS builds. This repository
holds a build script and notes that turn the Windows installer into a working Linux install:
the Electron frontend runs natively, and the Windows-only CAM backend runs under Wine, started
and supervised by the app exactly as on Windows.

**No Nest Studio files are included.** You need your own copy of the Windows installer; the
script only rearranges and patches what the installer contains. The scripts and docs here are
MIT licensed; Nest Studio itself remains under its own license.

Tested on Fedora 42 with Wine 10.20 and Nest Studio 1.2.0 (September 2026). See
[docs/GUIDE.md](docs/GUIDE.md) for a one-page guide (also as [docs/guide.html](docs/guide.html)
for opening in a browser) and [docs/PORTING-NOTES.md](docs/PORTING-NOTES.md) for how it works
and what to do when a new upstream version breaks a patch.

## Requirements

```sh
# Fedora
sudo dnf install wine nodejs unzip p7zip p7zip-plugins ImageMagick
```

- `wine` (64-bit prefix; the default `~/.wine` is fine, no winetricks needed)
- `node` (to unpack the app archive and apply the patches)
- `unzip` (for the Electron zip, which is downloaded with `npx` if not already cached)
- a 7z extractor for the installer: `7z`, `7zz`, `7za` or `bsdtar`. Without one the script
  falls back to the system libarchive through a small private Python venv (`libarchive-c`),
  which only needs network access the first time
- `ImageMagick` is optional (desktop icon); `setpriv` from util-linux is used when present
- about 1.5 GB of disk per build (copy-on-write filesystems such as btrfs share most of it)

Other distributions: the same packages under their local names. Nothing is Fedora-specific.

## Install

1. Download the Windows installer. The update feed
   <https://studio.worksbase.com/software/Nestworks/prod/win/latest.yml> names the current
   file (`Nest Studio Setup <version>.exe`) and its sha512; the file sits next to the yml.
2. Build:
   ```sh
   git clone https://github.com/avery-lockwood/nest-studio-linux.git
   ./nest-studio-linux/nest-studio-make-linux-build "Nest Studio Setup 1.2.0.exe"
   ```
   About five seconds after the one-time unpack and Electron download. The result lands in
   `~/opt/nest-studio`, with a launcher in `~/.local/bin/nest-studio`, an application menu
   entry, and a `README-LINUX.md` explaining the layout. The script also copies itself to
   `~/.local/bin` so updates are one command.
3. Run `nest-studio` or pick "Nest Studio" from the application menu. The first start takes a
   few seconds longer while Wine warms up.

Already have the installer unpacked? Pass that directory instead of the `.exe`.

## What the build does

- Uses the stock Electron linux-x64 build of the exact version embedded in `nest-studio.exe`,
  renamed so Electron considers the app packaged (that is how it decides where `resources/`
  live).
- Extracts `app.asar` into `resources/app/` and merges the unpacked native modules; the
  serialport module ships a linux-x64 prebuild, so USB serial works without rebuilding.
- Copies the CAM backend (`Nest Studio Service.exe`, a PyInstaller bundle with proprietary
  compiled kernels) and the G-code validator unchanged, and places bash shims next to them
  under the names the app looks for on non-Windows platforms. The shims exec `wine`.
- Applies four small patches to the main process (originals kept as `*.orig`): allow Linux as a
  backend platform, make sure the backend is killed on quit (with a pattern narrowed to the
  Wine process; the upstream one kills unrelated shells), `ss`-based port diagnostics, and skip
  the auto-updater, whose feed has no Linux packages.

## Updating

```sh
nest-studio-make-linux-build "Nest Studio Setup <new version>.exe"
```

The previous build is kept as `~/opt/nest-studio.prev`; settings and projects in
`~/.config/Nest Studio` are untouched. If upstream changed the code a patch anchors on, the
script stops and names the patch. [docs/PORTING-NOTES.md](docs/PORTING-NOTES.md) section 6
lists the anchors to look for.

## Verifying

`tools/smoke-test.py` launches the app, waits for the window and the backend, checks the
backend's `/api/version`, quits with SIGTERM and verifies nothing is left running. It exits 0
on success and prints what it saw either way.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Nothing happens, exit code 0 | `ELECTRON_RUN_AS_NODE=1` in the environment (VS Code terminals). The launcher unsets it. |
| Loading screen ends in "failed" | The backend did not answer on port 9630 within 60 s. Run it by hand: `cd ~/opt/nest-studio/resources/server/3Axis && wine "Nest Studio Service.exe"`, then `curl 127.0.0.1:9630/api/version`. Wine's output is in `~/.config/Nest Studio/logs/cam-wine.log`. |
| "CAM port 9630 is still occupied" | A stray backend: `pkill -f "Nest Studio Service.exe"`. |
| No serial ports | Add yourself to the `dialout` group and log in again. |
| GPU shows as disabled in `main.log` | Logged before the GPU process starts; not real. WebGL works. |

Logs: `~/.config/Nest Studio/logs/main.log` (app), `processing.log` (backend supervisor),
`cam-wine.log` (Wine).

## Not yet exercised

Long toolpath computations (the app's 3 s heartbeat needs the backend to keep answering
`/api/version`), 4-axis and image-to-STL features (ONNX under Wine), the account login and
`neststudio://` callback, and machine connections over the network or USB. Reports welcome.

## License

MIT for everything in this repository. Nest Studio and its components belong to their
respective owners and are not distributed here.
