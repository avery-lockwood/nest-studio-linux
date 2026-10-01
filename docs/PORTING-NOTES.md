# Porting notes

How the Windows-only Nest Studio release was made to run on Linux, what was learned about the
app on the way, and what to check when a new upstream version breaks the build. Written for
people maintaining `nest-studio-make-linux-build`, so it assumes you are comfortable with
Electron, a shell and Wine.

Reference state when this was written (2026-10-01):

| Component | Version |
|---|---|
| Nest Studio | 1.2.0 (installer dated 2026-09-14) |
| Electron embedded in it | 39.8.10 (Chromium 142, Node 22.22) |
| CAM backend (`/api/version`) | 1.1.0-alpha5-hf1 |
| Host | Fedora 42, kernel 6.19, Wine 10.20 (staging), AMD Ryzen 7840U / Radeon 780M, Wayland |

## 1. What the Windows release contains

Unpacked, the installer is an electron-builder layout:

```
nest-studio.exe                      Electron shell (Chromium/Node), nothing app-specific in it
resources/app.asar                   the app: out/main (main process), out/preload, out/renderer (React + three.js)
resources/app.asar.unpacked/         native bits kept outside the archive:
  node_modules/@serialport/bindings-cpp/prebuilds/   prebuilds for every platform, linux-x64 included
  out/renderer/assets/               238 MB of renderer assets (textures, models, gifs)
resources/server/3Axis/
  Nest Studio Service.exe            CAM backend, PyInstaller "onedir" bundle (Python 3.11, GUI subsystem)
  cam_core/*/*.pyd                   the proprietary CAM kernels: ocl, four_axis_cnc, autoplacement,
                                     planedetection, img2stl (ONNX depth model), img2svg, mesh_* ...
  numpy, scipy, shapely, trimesh, open3d, skimage, onnxruntime, opencv, flask_restx, dash ...
resources/server/gcodeCheck/
  gcode_validator_cli.exe            small C++ console tool, rule-file driven G-code checker
resources/store.json                 settings template (tool library, defaults)
resources/grpcdata.proto             machine link protocol (the CNC is reached over gRPC / serial, not through the CAM backend)
resources/app-update.yml             electron-updater feed (Windows only)
resources/elevate.exe                Windows updater helper
```

Not shipped although the code references it: `ArcWelder.exe` (arc fitting). The app checks for
it and reports it missing on Windows too, so Linux is no worse off.

**Installer format.** `Nest Studio Setup <version>.exe` is an electron-builder NSIS installer.
The app files are inside it as a single 7z archive (`app-64.7z`, about 500 MB) that NSIS stores
without further compression, so the 7z signature (`37 7A BC AF 27 1C`) is findable in the raw
file and the archive can be carved out with nothing but Python: read the start header at
signature+12 (next-header offset and size, two little-endian uint64), the archive length is
`32 + offset + size`. That is what `nest-studio-make-linux-build` does, then extracts with any
7z tool, `bsdtar`, or the system libarchive through its Python binding. Pure-Python readers do
not work: the `.exe`/`.dll` entries use the BCJ2 branch filter, which py7zr cannot decode (it
silently produces empty files). libarchive 3.8 and 7-Zip 25 both handle it. Verified: the carved archive's files are byte-identical to a copy obtained
by other means. Running the installer under Wine with `/S` does not work: it exits with NSIS
status 2 ("aborted by script") before writing anything, in a fresh prefix as well.

The backend is the whole problem. Everything in `cam_core` is a compiled CPython 3.11 Windows
extension with no source, so there is no "just run it with system Python" route. Wine was the
only option, and it turned out to be an easy one: the backend started under a plain Wine 10
prefix (one that already had other apps in it, no winetricks) on the first try and answered
`GET /api/version` about six seconds later (three seconds once wineserver is warm).

## 2. How the app starts

All of this is in `out/main/index.js` (about 4,000 lines, readable, not minified). Function
names below are the ones in the 1.2.0 build; grep for them first when a new version arrives.

Bootstrap: `setupSingleInstanceLock()` then `bootstrapMainProcess()`, which initialises the
logger, registers the `neststudio://` protocol handler, and on `whenReady` starts
`startCamSupervisor()` and `bootstrapLicenseGate()`. The "license gate" in this build goes
straight into the main app flow; there is no activation check, only a login inside the app.

The loading window tracks three phases: `environment` (main window ready), `settings`
(`readStore()`), and `cam` (`waitForCamSupervisorReady()`). Any phase failing shows the
"failed" loading screen and the main window never appears.

CAM supervisor (`startCamSupervisor`):

1. Probe `http://127.0.0.1:9630/api/version` once, timeout 400 ms when packaged (2 s when
   not). If something answers, adopt it as an "existing process" and never spawn.
2. Otherwise `restartServeProcess()`: kill tracked child, `pkill -9 -f` the backend by name
   (non-Windows), wait for the port to be released, then `spawnServeProcess(true)`.
3. `waitForCamHealth()` polls every 500 ms for up to 60 s.
4. Once running, a heartbeat probes every 3 s with a 2 s timeout; three consecutive failures
   trigger recovery (up to three restart attempts, 30 s each, 5 s / 15 s backoff), then the
   "failed" state.

`spawnServeProcess` is where the platform matters:

```js
if (process.platform !== "win32" && process.platform !== "darwin") { warn("not supported"); return; }
const relativePath = process.platform === "win32" ? "./3Axis/Nest Studio Service.exe" : "./3Axis/Nest Studio Service";
const serveExePath = resolveServerBinPath(relativePath);
proc = child_process.spawn(serveExePath, [], { cwd: dirname(serveExePath), stdio: ["ignore", "pipe", "pipe"] });
```

Three facts fall out of that snippet and shape the whole port:

- On non-Windows it spawns a file literally named `3Axis/Nest Studio Service` with no
  extension. Put an executable shim there and the JS needs almost no change.
- `resolveServerBinPath` uses `process.resourcesPath/server` when packaged and
  `app.getAppPath()/resources/server` when not. Running `npx electron app.asar` is the
  unpackaged case with `getAppPath()` pointing inside the asar, so the backend is never found.
  Electron decides `app.isPackaged` purely from the executable's basename: anything other than
  `electron` counts as packaged. Renaming the stock binary to `nest-studio` makes every path
  resolve exactly as it does on Windows.
- stdout and stderr are pipes that nothing ever reads. A chatty child fills the 64 KB pipe
  buffer and blocks. Wine prints `fixme:` lines to stderr, so the shim must redirect output
  to a file (and set `WINEDEBUG=-all`).

Shutdown: `will-quit` calls `terminateServeProcess()`, which SIGKILLs the tracked child and,
on macOS only in the upstream code, runs `pkill -9 -f "Nest Studio Service"`.

The G-code validator (`getGcodeValidatorCliPath`) looks for `gcodeCheck/gcode_validator_cli`
(no extension) on non-Windows and passes absolute Linux paths as `--rule_file=` etc. Wine
resolves Unix absolute paths on its own, so the shim just execs wine. The result JSON starts
with a UTF-8 BOM; the app already strips it.

The updater (`upgrade-<hash>.js`, the hash changes per build) sets a generic feed at
`.../prod/win` or `.../prod/mac`. On Linux electron-updater would look for `latest-linux.yml`
there, get a 404 and surface an error toast. `shouldSkipUnpackagedUpdateCheck()` is the one
place to short-circuit it.

## 3. Decisions and why

**Packaged layout, not `electron app.asar`.** Mirrors the installer, fixes path resolution,
and lets the app manage the backend (start, heartbeat, recovery) instead of leaving it to the
user. User data lands in `~/.config/Nest Studio` like a real install.

**Directory app, not a repacked asar.** Electron loads `resources/app/` in preference to
`resources/app.asar`. Extracting avoids any extra tooling (the asar format is a JSON header
plus concatenated files; 30 lines of Node read it) and leaves the patched main process
editable. The `app.asar.unpacked` tree is merged into the same directory.

**Shims named exactly like the macOS binaries.** Keeps the JS patch to a one-line platform
check. The shim `exec`s wine so Electron's child PID is the Wine process itself (SIGKILL from
the app lands on the backend), and wraps it in `setpriv --pdeathsig KILL` so the kernel kills
the backend if Electron dies without running its quit handlers.

**Narrowed kill pattern.** Upstream's `pkill -9 -f "Nest Studio Service"` matches any process
whose command line contains that text: a terminal running `tail -f` on a log path, an editor
with the shim open, the shell of whoever is debugging the app. On Linux the patch replaces it
with an anchored pattern that only matches the Wine process
(`^Z:\\.*\\3Axis\\Nest Studio Service\.exe$`) at both call sites, and extends the quit-time
kill to Linux so nothing outlives the app.

**Default Wine prefix.** It worked in a prefix shared with several other Windows apps and
needs no winetricks, so the shim does not force a `WINEPREFIX`. Users who want isolation can
export one before launching.

**Updater off on Linux.** There is no Linux package in the feed. Updates are done by rebuilding
from the new Windows installer; the install's README-LINUX.md tells users how.

## 4. Gotchas, roughly in the order they cost time

1. **`ELECTRON_RUN_AS_NODE=1` in the environment.** VS Code's extension host sets it and every
   terminal spawned from there inherits it. An Electron binary started with it is a plain Node
   process: no window, exit code 0, no output, and Chromium flags fail with "bad option". The
   launcher unsets it. Check `env | grep ELECTRON` before anything else when the app "does
   nothing".
2. **The app kills your shell.** During start and quit the unpatched app runs the broad
   `pkill -f` above. If your terminal's command line contains "Nest Studio Service" (a `pgrep`
   you typed, a path, a heredoc) it dies silently. Symptom: a script that just stops with exit
   code 1 the moment the app starts. Fixed by the narrowed pattern, but keep it in mind when
   debugging an unpatched build.
3. **Backslash escaping across layers.** The kill pattern passed through Python, a bash
   heredoc, a JS template literal and a JS string before reaching pkill; one layer too few and
   `\3Axis` becomes an invalid back-reference, pkill exits 2, and the app treats that as a
   fatal startup error. The patch now builds the pattern from `String.fromCharCode(92)` so no
   escaping layer can touch it.
4. **Patch order.** A patch that rewrites a line another patch still expects to find must
   run after it. The patcher checks the exact match count for every edit and aborts on a
   mismatch, which is also how an upstream change announces itself.
5. **GPU looks disabled in the app's own log.** `runtime-gpu-diagnostics` is written about
   300 ms after launch, before the GPU process exists, so it always says `webgl: disabled_off`.
   A separate probe with the same Electron version reports WebGL and compositing enabled, and
   the app writes several MB to `~/.config/Nest Studio/GPUCache`, which only a live GPU process
   does. Do not chase this.
6. **Pipes nobody reads.** See section 2; redirect the shim's output or the backend will stall
   after a while.
7. **SIGTERM during a failed startup.** If the CAM phase has failed and the "failed" loading
   screen is up, the app can ignore SIGTERM. Close it from the window or SIGKILL it.

## 5. Verifying a build

`tools/smoke-test.py` does this automatically (launch, wait for both readiness lines, hit the
API, SIGTERM, check for leftovers). By hand:

```sh
nest-studio &                                   # or the desktop entry
grep -E "startup-cam-ready|startup-main-window-shown" ~/.config/Nest\ Studio/logs/*.log
curl 127.0.0.1:9630/api/version                 # {"time":"...","version":"1.1.0-alpha5-hf1"}
# quit from the window, then:
pgrep -af "Nest Studio Service.exe"             # nothing
ss -ltn | grep 9630                             # nothing
```

Expected timings on a 2023 laptop: CAM ready in about 3 s, main window at about 4 s.

Things that were not exercised and would be the first to test on real work: a full toolpath
computation (the heartbeat tolerates a busy backend only if it still answers `/api/version`
within 2 s), 4-axis and image-to-STL (ONNX under Wine, CPU only), the login flow and the
`neststudio://` callback, and connecting to a machine over gRPC or USB serial.

## 6. Updating for a new upstream version

1. Get the new installer. The feed is
   `https://studio.worksbase.com/software/Nestworks/prod/win/latest.yml`; it names the
   installer file (`Nest Studio Setup <version>.exe`) and its sha512.
2. Unpack it as the README describes and run `nest-studio-make-linux-build`.
3. If a patch fails to match, open `resources/app/out/main/index.js.orig` in the new build
   and look for these anchors:
   - `"CAM backend is not supported on this platform:"` (platform check in `spawnServeProcess`)
   - `pkill -9 -f "Nest Studio Service"` (two call sites: `restartServeProcess`, `terminateServeProcess`)
   - `"/usr/sbin/lsof"` (`inspectCamPortOwners`)
   - `const CAM_HTTP_PORT = ` (port constant; also confirm the renderer CSP still allows that port)
   - in `upgrade-*.js`: `function shouldSkipUnpackagedUpdateCheck`
   Update the `from` strings in the script to the new text; keep the `to` strings' intent.
4. Confirm the non-Windows backend path is still `./3Axis/Nest Studio Service` and the
   validator path `gcodeCheck/gcode_validator_cli`; the shims are written to those names.
5. Check that `app.asar.unpacked/node_modules/@serialport/bindings-cpp/prebuilds/linux-x64`
   still exists. If upstream drops it, serial support needs a rebuilt binding.
6. Run `tools/smoke-test.py`.

If the upstream Electron major changes, nothing in the script cares; it reads the version
string out of `nest-studio.exe` and downloads that exact Electron.
