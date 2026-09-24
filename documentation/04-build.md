# Building KiCad master on Arch

Verified 2026-08-09: KiCad `10.99.0`, **0 compile errors** on GCC 16.1.1,
protobuf 35.1, CMake 4.4.2.

## Dependencies

`install-deps.sh` upstream is Debian-only. On Arch, almost everything is already
present in a normal dev install. Of 37 packages checked, only **one** was
genuinely missing:

```bash
sudo pacman -S --needed boost ccache mold python-pytest python-cairosvg
```

- **`boost`** — the actual blocker. Arch splits headers (`boost`) from runtime
  (`boost-libs`); a stock system has the libs but not the headers. Also supplies
  `unit_test_framework`, needed by `KICAD_BUILD_QA_TESTS=ON`.
- **`ccache`, `mold`** — not optional in our configure script, and they matter on
  a memory-constrained box.
- **`python-pytest`, `python-cairosvg`** — the pytest half of the QA suite.

### Package-name traps

- **`ngspice`, not `libngspice`.** `find_package( ngspice REQUIRED )` at
  `CMakeLists.txt:856` is **ungated** — not behind `KICAD_SPICE` — so it is a
  hard requirement. Arch's `ngspice` provides both `/usr/lib/libngspice.so` and
  `/usr/include/ngspice/sharedspice.h`.
- **wx `webview`** is a `REQUIRED` component. Satisfied by `wxwidgets-gtk3` plus
  `webkit2gtk-4.1`; check for `/usr/lib/libwx_gtk3u_webview-3.2.so`.

### Versions that satisfied the minimums

| Dependency | Installed | Required |
|---|---|---|
| wxWidgets | 3.2.11 | ≥ 3.2.0 |
| Boost | 1.91.0 | ≥ 1.71.0 |
| protobuf | 35.1 | ≥ 3.12 |
| OpenCASCADE | 7.9.3 | ≥ 7.6.0 |
| libgit2 | 1.9.6 | ≥ 1.5 |
| Cairo / Pixman | 1.18.4 / 0.46.4 | ≥ 1.12 / ≥ 0.30 |
| GLM / FreeType | 1.0.3 / 2.14.3 | ≥ 0.9.8 / ≥ 2.11.1 |

## The CMake 4 trap

CMake 4.x **removed** support for `cmake_minimum_required(VERSION < 3.5)`.
`thirdparty/nanodbc/CMakeLists.txt` declares `3.0.0` and is added
**unconditionally** at `thirdparty/CMakeLists.txt:63`, so configure hard-fails
without:

```
-DCMAKE_POLICY_VERSION_MINIMUM=3.5
```

That is the supported escape hatch. (`sentry-native`'s bundled zlib also declares
3.0, but `KICAD_USE_SENTRY` defaults OFF so it never loads.)

## Configuring

Use `build/configure.sh`, which is commented and gitignored via `/build*`.
The non-obvious flags:

| Flag | Why |
|---|---|
| `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` | nanodbc, above |
| `-DCMAKE_LINKER_TYPE=MOLD` | mold links KiCad's large DSOs with far less peak memory than GNU ld |
| `-DCMAKE_CXX_FLAGS=-gsplit-dwarf` | keeps debug info out of the link: lower peak memory, less rebuild churn |
| `-DCMAKE_JOB_POOLS="compile=4;link=1"` | caps concurrency; serialises the big links |
| `-DKICAD_BUILD_PNS_DEBUG_TOOL=ON` | builds `qa/tools/pns` — the router bench |
| `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` | `compile_commands.json` for clangd |

`RelWithDebInfo` + split-DWARF is the chosen tradeoff: usable stack traces for
router debugging without full `Debug` footprint.

## Resource reality

Measured on 8 cores / 9 GB RAM / 16 GB swap:

| | |
|---|---|
| Wall clock | ~6 h (across two runs, `-j4` then `-j3`) |
| Targets | 3397 |
| Sustained rate | 9.4 targets/min at `-j4`, 6.5 at `-j3` |
| Build output | **20 GB** |
| Peak RAM | dips to ~2.6 GB available; swap to ~2.3 GB |

**Estimating from early rates does not work.** Early targets are cheap
`thirdparty` objects; the expensive `pcbnew`/`eeschema` dialog TUs and the
serialised link phase are all at the end. Our early extrapolations were wrong
twice, in the optimistic direction. Budget 6 h and 20 GB.

Largest artifacts: `_pcbnew.kiface` 922 MB, `_eeschema.kiface` 709 MB,
`pns_debug_tool` 382 MB.

## ccache and PCH

`KICAD_USE_PCH` defaults **ON** (`CMakeLists.txt:240`), and ccache refuses to
cache PCH-using compiles by default — we measured **76% uncacheable**. Fix:

```bash
ccache --set-config sloppiness=pch_defines,time_macros
ccache --set-config max_size=20G
```

Do this *before* the first build; compiles already done uncached do not come
back. Incremental rebuilds do not depend on ccache — ninja skips unchanged
targets regardless — but clean rebuilds and flag changes do.

## Running long builds safely

Use `setsid`, not just `nohup`:

```bash
cd build && setsid nohup ninja -k 5 -j3 >> build.log 2>&1 < /dev/null &
```

We lost a build at target 2264 when the kernel OOM-killed **Claude Desktop**
(`oom_score_adj: 200`, triggered by an unrelated process) and ninja died as
collateral damage — its log said `interrupted by user`. `nohup` survives SIGHUP
but not session teardown. Confirm detachment with `ps -o pid,sid` — `sid == pid`
means it is its own session leader.

Recovery is cheap either way: ninja resumes from `.ninja_log` and only the
in-flight target is redone. After the kill, 2259 of 2264 targets survived.

## Post-build notes

`kicad-cli` run from the build tree warns:

```
schema file '/usr/local/share/kicad/schemas/api.v1.schema.json' not found
```

It resolves schema paths against the install prefix. Harmless for most
subcommands; may matter for `api-server`.

## AppImage environment leakage

Unrelated to building, but it breaks every `kicad-cli` invocation from inside an
AppImage-hosted shell. Claude Desktop exports `APPDIR=/tmp/.mount_claudeXXXX`,
and KiCad honours `APPDIR` for path resolution:

```
Failed to load shared library '/tmp/.mount_claudeXXXX/usr/bin/_pcbnew.kiface'
```

Strip `APPDIR`, `APPIMAGE`, `OWD`, `ARGV0` from the child environment.
`autokicad.backend.clean_env()` does this for every call; regression-tested by
`test_clean_env_strips_appimage_vars`.
