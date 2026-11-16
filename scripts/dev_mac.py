#!/usr/bin/env python3
# The development loop on the Mac: rebuild the Mac host (make's own incremental build: only what changed) and start the game on what a full build already made, with no packing and no
# signing. A host change (dm/host, dm/gfx/nvn, dm/gfx/msl, ...) runs in the time make takes.
#
# By default it opens the Mac app's launcher with its settings window: a development app, <build>/dev/<the app's name>.app, made from the Mac app the full build packed (--platform mac-app)
# beside the build folder. Its Info.plist is a copy; its host is a link to dm/host/dmhost; its game files (romfs.img, msl, pso, romfs_overlay, the rewritten modules) are links to the packed
# app's, and its profile tables links to the repository's. Its launcher (nxnative) is the packed app's, or, once a launcher source (dm/mac/launcher.c, dm/mac/Launcher.swift, the iOS launcher's
# LauncherSettings.swift and LauncherRow.swift, save_sync) is newer than it, one built here as pack_app.py builds it; its settings list (launcher.json) is the packed app's, or, once
# dm/games/settings.py or settings.tsv is newer, one written here by settings.py with the packed app's graphics API, language and frame-rate counter default. So a launcher change runs at once too.
# It has the packed app's bundle identifier, so the window shows the choices saved there. The launcher writes the log to ~/Library/Logs/nxnative/<title ID>.log. --direct runs the host itself
# instead, as below.
#
# What it reuses from the game's build folder (<NSP folder>/<title ID>_build, made by build_ipa_from_nsp.py --platform mac or mac-app): the rewritten modules (patched_<version>), the
# RomFS image and its game file replacements (extract/<version>), the Mac shader libraries of the current chain (shader/cache/b-<chain>.img), the pipeline archive made offline for this Mac
# (pso/mac) and the button-icon font (font). It does not rebuild them: when an input of theirs is newer than what the build folder holds (the rewriter or a profile's patches.py, the shader
# translation chain or a profile's shader inputs, the launcher settings), it says so and names the full build, which is then needed for that change to take effect.
#
# --direct: the game runs as the Mac app would run it: the app's save (~/Library/Application Support/nxnative/<title ID>/save, so play goes on from there), the choices saved in the app's settings
# window (defaults read org.nxnative.<title id>), an environment file (--env-file) and KEY=VALUE arguments, each later one winning. The log goes to <build>/run/dev.log and the terminal.
#
# Usage: python3 scripts/dev_mac.py [--build DIR] [--direct] [--no-launcher] [--env-file FILE] [--save DIR] [--no-choices] [--no-make] [--dry-run] [KEY=VALUE ...]
#   (default) the launcher with its settings window; KEY=VALUE and --env-file go into its environment, where the window shows them as fixed
#   --no-launcher: the launcher without its window (its saved choices)
#   --direct: no launcher; the host run on the build folder's files, with the choices below
#   --build: the game's build folder (default: the only <title ID>_build beside this repository, or in the folder above it)
#   --save (--direct): the save folder (default: the Mac app's; `run` = <build>/run/save, the full build's own run)
#   --no-choices: leave out the app's saved settings-window choices
#   --no-make: run the host as it is (dm/host/dmhost)
#   --dry-run: build the host and print the command without running the game
# Example: python3 scripts/dev_mac.py DM_FRAMEGEN=1 DM_FPS_TARGET=30 DM_UPSCALER=game
import argparse, json, os, plistlib, re, shlex, shutil, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'dm/mac')); sys.path.insert(0, str(REPO / 'dm/tools'))
import pack_app   # the launcher's build (build_launcher, swift_sources, min_os)
import nacp
HOST = REPO / 'dm/host/dmhost'
TABLES = ('shader_map.tsv', 'pso_manifest.tsv', 'c1_full.tsv', 'cb_extent.tsv', 'shader_pairs.tsv', 'pso_pairs.tsv')   # the profile's, which the app carries (dm/mac/pack_app.py TABLES)
LAUNCHER_INPUTS = ['dm/mac/launcher.c', 'dm/mac/Launcher.swift', 'dm/ios/Sources/LauncherSettings.swift', 'dm/ios/Sources/LauncherRow.swift',
                   'dm/host/romfs_image.h', 'dm/host/save_sync.c', 'dm/host/save_sync.h']   # what the launcher is built from (pack_app.py build_launcher)
SETTINGS_INPUTS = ['dm/games/settings.py', 'dm/games/settings.tsv']   # its launcher.json

# Inputs of the stages this loop does not rerun, as paths in the repository (a folder: every file under it), with what is to be checked against
REWRITE_INPUTS = ['dm/tools/patch_nso.py', 'dm/tools/nso.py', 'dm/tools/nso_code.py', 'dm/tools/nso_asm.py', 'dm/tools/nso_match.py', 'dm/tools/sdk_patches.py',
                  'dm/tools/ue_cvar.py', 'dm/tools/ue_malloc.py', 'dm/tools/ue_region.py', 'dm/tools/ue_ring.py', 'dm/tools/snowdrop_pools.py', 'dm/tools/loader_census.py']
CHAIN_INPUTS = ['dm/gfx/tools', 'dm/gfx/nvn/dmsh_abi.h', 'dm/gfx/nvn/dmsh_chain.h', 'dm/ios/tools/ios_metal.py']
PROFILE_SHADER_INPUTS = ['shader_map.tsv', 'shader_facts.tsv', 'shader_pairs.tsv', 'bf_want.tsv']


def die(msg): print(f'dev_mac: {msg}', file=sys.stderr); sys.exit(1)


def say(msg): print(f'dev_mac: {msg}', flush=True)


def find_build(arg):
    if arg:
        b = Path(arg).expanduser().resolve()
        if not b.is_dir(): die(f'no build folder at {b}')
        return b
    found = sorted({p.resolve() for d in (REPO.parent, REPO.parent.parent) for p in d.glob('*_build') if re.fullmatch(r'[0-9A-Fa-f]{16}_build', p.name) and p.is_dir()})
    if len(found) != 1: die('give the game\'s build folder with --build (found ' + (', '.join(map(str, found)) if found else 'none') + ' beside the repository)')
    return found[0]


def newest(paths):   # the newest file under these paths (files or folders), as (mtime, path); (0, None) when there is none
    best = (0, None)
    for p in paths:
        for f in ([p] if p.is_file() else [x for x in p.rglob('*') if x.is_file() and '__pycache__' not in x.parts] if p.is_dir() else []):
            m = f.stat().st_mtime
            if m > best[0]: best = (m, f)
    return best


def stale(what, inputs, made, platform='mac'):   # a warning when an input is newer than what the build folder (or the packed app) made from it
    if not made or not made.exists(): return
    m, f = newest(inputs)
    if f and m > made.stat().st_mtime:
        say(f'[NOTE] {f.relative_to(REPO) if f.is_relative_to(REPO) else f} is newer than the build folder\'s {what} ({made.name}): this loop does not rebuild it, so that change is not in the run; '
            f'run scripts/build_ipa_from_nsp.py --platform {platform} for it')


def saved_choices(bundle):   # the Mac app's settings-window choices (dm/mac/launcher.c saved_choices): launcher.DM_* keys of its defaults domain
    r = subprocess.run(['defaults', 'export', bundle, '-'], capture_output=True)
    if r.returncode: return {}
    try: d = plistlib.loads(r.stdout)
    except Exception: return {}
    return {k[len('launcher.'):]: str(v) for k, v in d.items() if k.startswith('launcher.DM_') and isinstance(v, (str, int, float))}


def env_file(path):   # KEY=VALUE lines, # comments (as the launcher's env.txt)
    out = {}
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k, v = line.split('=', 1); out[k.strip()] = v.strip()
    return out


def packed_app(build, gid):   # the newest Mac app of this game beside the build folder (build_ipa_from_nsp.py --platform mac-app), or None
    apps = []
    for app in build.parent.glob('*.app'):
        try: info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        except (OSError, plistlib.InvalidFileException): continue
        if str(info.get('DMTitleID', '')).upper() == gid and (app / 'Contents/MacOS/nxnative').is_file(): apps.append(app)
    return max(apps, key=lambda a: (a / 'Contents/MacOS/nxnative').stat().st_mtime) if apps else None


def link(dst, src):   # dst as a link to src (replacing what is there)
    if dst.is_symlink() or dst.is_file(): dst.unlink()
    elif dst.is_dir(): shutil.rmtree(dst)
    dst.symlink_to(src)


def copy_if_changed(src, dst):
    if dst.is_symlink(): dst.unlink()
    if not dst.is_file() or dst.stat().st_size != src.stat().st_size or dst.read_bytes() != src.read_bytes(): shutil.copy2(src, dst)


def newer(inputs, made):   # whether a file under these repository paths is newer than made (or made is missing)
    m, f = newest([REPO / p for p in inputs])
    return not made.is_file() or (f is not None and m > made.stat().st_mtime)


def launcher(app, dev, dry):
    """The dev app's launcher: the packed app's while no launcher source is newer than it; else one built here (pack_app.build_launcher, for the host's deployment target), again only when a
    source is newer than the last one built. A built launcher stays until the packed app's is newer (a full build that packed a newer one)"""
    packed, out, stamp = app / 'Contents/MacOS' / pack_app.EXE, dev / 'Contents/MacOS' / pack_app.EXE, dev.with_name(dev.name + '.launcher-built')   # beside the bundle, not in it
    built = stamp.is_file() and out.is_file() and not out.is_symlink() and out.stat().st_mtime >= packed.stat().st_mtime
    if not newer(LAUNCHER_INPUTS, packed) and not built:
        copy_if_changed(packed, out); stamp.unlink(missing_ok=True); return
    if built and not newer(LAUNCHER_INPUTS, out): return
    if dry: say('the launcher sources are newer than the launcher: it would be built (dry run: not built)'); return
    say('building the launcher (its sources are newer than the packed app\'s)')
    run = lambda c: subprocess.run([str(x) for x in c], capture_output=True, text=True, check=True).stdout.strip()
    try:
        clang, swiftc = Path(run(['xcrun', '--find', 'clang'])).resolve(), Path(run(['xcrun', '--find', 'swiftc']))   # swiftc not resolved, as pack_app.py
        sdk = Path(run(['xcrun', '--sdk', 'macosx', '--show-sdk-path'])).resolve()
    except (OSError, subprocess.CalledProcessError) as e: die(f'no Xcode compiler or macOS SDK: {e}')
    if out.is_symlink() or out.exists(): out.unlink()
    pack_app.build_launcher(out, pack_app.min_os(HOST if HOST.is_file() else packed), clang, swiftc, sdk)
    stamp.write_text('')


def settings_list(app, dev, gid, patched, ext, dry):
    """The dev app's launcher.json: a link to the packed app's while settings.py and settings.tsv are not newer than it; else written here by settings.py (SETTINGS_PLATFORM=mac) with what the
    full build gave it: the graphics API (the packed shader library: b-<chain> is NVN, vk Vulkan, gl GL), the language (Info.plist DMLanguage) and the frame-rate counter's default (the packed list's)"""
    packed, out = app / 'Contents/Resources/dm/launcher.json', dev / 'Contents/Resources/dm/launcher.json'
    if not packed.is_file(): return
    if not newer(SETTINGS_INPUTS, packed): link(out, packed); return
    if out.is_file() and not out.is_symlink() and not newer(SETTINGS_INPUTS + ['scripts/dev_mac.py'], out) and out.stat().st_mtime >= packed.stat().st_mtime: return
    libs = [p.name for p in (app / 'Contents/Resources/msl').iterdir()] if (app / 'Contents/Resources/msl').is_dir() else []
    gfx = 'vulkan' if 'vk' in libs else 'gl' if 'gl' in libs else 'nvn'
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    old = {s['env']: s for s in json.loads(packed.read_text(encoding='utf-8')).get('settings', [])}
    fps = old.get('DM_SHOW_FPS', {}); fps = '1' if fps and fps.get('default') == fps.get('on', '1') else '0'
    controls = [ext / 'control.nacp'] + sorted(p for p in ext.parent.glob('*/control.nacp') if p != ext / 'control.nacp')   # the packed version's, then the others' (a NUP's update may carry none)
    control = next((p for p in controls if p.is_file() and nacp.languages(p)), controls[0])
    if dry: say(f'the launcher settings are newer than the packed app\'s list: launcher.json would be written (GFXAPI={gfx}, dry run: not written)'); return
    say(f'writing the launcher settings (settings.py: GFXAPI={gfx}, DM_LANG={info.get("DMLanguage", "")}, {control.relative_to(ext.parent.parent)})')
    if out.is_symlink(): out.unlink()
    env = {**os.environ, 'GFXAPI': gfx, 'SETTINGS_PLATFORM': 'mac', 'DM_LANG': str(info.get('DMLanguage', '')), 'DM_SHOW_FPS': fps}
    r = subprocess.run([sys.executable, str(REPO / 'dm/games/settings.py'), gid, str(patched), str(control), str(out)], env=env, capture_output=True, text=True)
    if r.returncode: die(f'settings.py failed: {(r.stderr or r.stdout).strip()[-1500:]}')


def dev_app(build, gid, prof, app, patched, ext, dry):
    """<build>/dev/<app name>: the packed app's Info.plist (a copy), the current host, the app's game files and the repository's profile tables as links, and the launcher and its settings list,
    the packed app's or built here from newer sources (launcher, settings_list). Made again in place each time: links cost nothing and the copies are small"""
    dev = build / 'dev' / app.name; c = dev / 'Contents'
    for d in (c / 'MacOS', c / 'Resources/dm'): d.mkdir(parents=True, exist_ok=True)
    copy_if_changed(app / 'Contents/Info.plist', c / 'Info.plist')
    if (app / 'Contents/PkgInfo').is_file(): copy_if_changed(app / 'Contents/PkgInfo', c / 'PkgInfo')
    launcher(app, dev, dry)
    link(c / 'MacOS/dmhost', HOST)
    res, dm = app / 'Contents/Resources', c / 'Resources/dm'
    want = {p.name for p in res.iterdir() if p.name != 'dm'}
    for p in (c / 'Resources').iterdir():
        if p.name != 'dm' and p.name not in want: p.unlink() if p.is_symlink() or p.is_file() else shutil.rmtree(p)
    for n in want: link(c / 'Resources' / n, res / n)
    want = {p.name for p in (res / 'dm').iterdir()}
    for p in dm.iterdir():
        if p.name not in want: p.unlink() if p.is_symlink() or p.is_file() else shutil.rmtree(p)
    for n in want - {'launcher.json'}: link(dm / n, prof / n if n in TABLES and (prof / n).is_file() else res / 'dm' / n)
    settings_list(app, dev, gid, patched, ext, dry)
    return dev


def main():
    ap = argparse.ArgumentParser(description='Rebuild the Mac host and run it on what a full build made (no app, no signing)')
    ap.add_argument('--build'); ap.add_argument('--env-file'); ap.add_argument('--save')
    ap.add_argument('--direct', action='store_true'); ap.add_argument('--no-launcher', action='store_true')
    ap.add_argument('--no-choices', action='store_true'); ap.add_argument('--no-make', action='store_true'); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('vars', nargs='*', metavar='KEY=VALUE')
    a = ap.parse_args()
    for v in a.vars:
        if not re.fullmatch(r'[A-Z_][A-Z0-9_]*=.*', v): die(f'not KEY=VALUE: {v}')

    build = find_build(a.build); gid = build.name.removesuffix('_build').upper()
    prof = REPO / 'dm/games' / gid
    if not (prof / 'game.tsv').is_file(): die(f'no profile dm/games/{gid} in this repository')

    # What the full build made
    patched = sorted((p for p in build.glob('patched_*') if p.is_dir() and any(p.glob('*.patched.bin'))), key=lambda p: p.stat().st_mtime)
    if not patched: die(f'no rewritten modules (patched_*) in {build}: run scripts/build_ipa_from_nsp.py --platform mac first')
    patched = patched[-1]; ver = patched.name.removeprefix('patched_')
    ext = build / 'extract' / ver
    romfs = ext / 'RomFS.img' if (ext / 'RomFS.img').is_file() else ext / 'RomFS'
    if not romfs.exists(): die(f'no RomFS in {ext}')
    m = re.search(r'#define DMSH_CHAIN "([0-9a-f]+)"', (REPO / 'dm/gfx/nvn/dmsh_chain.h').read_text())
    chain = m.group(1) if m else ''
    msl = build / 'shader/cache'
    if not any(msl.glob(f'b-{chain}*')):
        have = sorted(p.name for p in msl.glob('b-*')) if msl.is_dir() else []
        die(f'no Mac shader libraries for the chain {chain} in {msl} (there: {", ".join(have) or "none"}): the chain changed, run scripts/build_ipa_from_nsp.py --platform mac')
    font = build / 'font'
    if not (font / 'nintendo_ext.ttf').is_file(): die(f'no button-icon font in {font}: run scripts/build_ipa_from_nsp.py --platform mac first')

    stale('rewritten modules', [REPO / p for p in REWRITE_INPUTS] + [prof / 'patches.py', prof / 'game.tsv'], build / f'{patched.name}.key')
    lib = next(iter(sorted(msl.glob(f'b-{chain}*'))))
    stale('shader libraries', [REPO / p for p in CHAIN_INPUTS] + [prof / f for f in PROFILE_SHADER_INPUTS], lib)

    # The host: make's incremental build
    if not a.no_make:
        say(f'building the Mac host (make -C dm/host GAME={gid})')
        r = subprocess.run(['make', '-C', str(REPO / 'dm/host'), f'GAME={gid}', f'-j{os.cpu_count() or 8}'])
        if r.returncode: die('the host did not build')
    if not HOST.is_file(): die(f'no host at {HOST}')

    if not a.direct:   # the launcher, with its settings window
        app = packed_app(build, gid)
        if not app: die(f'no Mac app of {gid} beside {build}: run scripts/build_ipa_from_nsp.py --platform mac-app once (or use --direct)')
        dev = dev_app(build, gid, prof, app, patched, ext, a.dry_run)
        env = dict(env_file(a.env_file)) if a.env_file else {}
        env.update(dict(v.split('=', 1) for v in a.vars))
        exe = dev / 'Contents/MacOS/nxnative'; args = [str(exe)] + (['--no-launcher'] if a.no_launcher else [])
        log = Path.home() / 'Library/Logs/nxnative' / f'{gid}.log'
        if a.dry_run: print(' '.join(f'{k}={shlex.quote(v)}' for k, v in sorted(env.items())) + ' ' + ' '.join(map(shlex.quote, args))); return 0
        say(f'opening the launcher of {dev} (host: {HOST}, packed app: {app.name}); log: {log}')
        try: return subprocess.run(args, env={**os.environ, **env}).returncode
        except KeyboardInterrupt: return 130

    # The environment, as the app's launcher gives it (dm/mac/launcher.c), later ones winning: saved choices, env file, KEY=VALUE
    sup = Path.home() / 'Library/Application Support/nxnative' / gid
    env = {}
    if not a.no_choices:
        ch = saved_choices(f'org.nxnative.{gid.lower()}')
        if ch: say('the app\'s saved choices: ' + ' '.join(f'{k}={v}' for k, v in sorted(ch.items())))
        env.update(ch)
    if a.env_file: env.update(env_file(a.env_file))
    env.update(dict(v.split('=', 1) for v in a.vars))
    run = build / 'run'
    save = run / 'save' if a.save == 'run' else Path(a.save).expanduser() if a.save else sup / 'save'
    for d in (save, run / 'temp'): d.mkdir(parents=True, exist_ok=True)
    lang = 'en-US'
    for app in sorted(build.parent.glob('*.app')):
        try: info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        except OSError: continue
        if info.get('DMTitleID', '').upper() == gid and info.get('DMLanguage'): lang = info['DMLanguage']; break
    paths = {'DM_ROOT': build, 'DM_ROMFS': romfs, 'DM_SAVE': save, 'DM_TEMP': run / 'temp', 'DM_MSL_DIR': msl, 'DM_FONT_DIR': font,
             'DM_NVN_SHMAP': prof / 'shader_map.tsv', 'DM_LANG': lang, 'DM_WINDOW': '1', 'DM_AUDIO': '1'}
    if (build / 'pso/mac/prebuilt.tsv').is_file(): paths['DM_PSO_BUNDLE'] = build / 'pso/mac'   # the pipeline bases made offline for this Mac
    overlay = next((d for d in (ext / 'romfs_overlay_mac', ext / 'romfs_overlay') if d.is_dir()), None)
    if overlay: paths['DM_ROMFS_OVERLAY'] = overlay
    for k, v in paths.items(): env.setdefault(k, str(v))

    log = run / 'dev.log'
    q = lambda s: shlex.quote(str(s))
    cmd = f'cd {q(run)} && ' + ' '.join(f'{k}={q(v)}' for k, v in sorted(env.items())) + f' {q(HOST)} {q(patched)}'
    if a.dry_run: print(cmd); return 0
    say(f'running {gid} ({patched.name}, chain {chain}); log: {log}')
    with open(log, 'w') as lf:
        p = subprocess.Popen([str(HOST), str(patched)], cwd=run, env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for line in p.stdout:
                sys.stdout.buffer.write(line); sys.stdout.flush(); lf.write(line.decode('utf-8', 'replace'))
        except KeyboardInterrupt:
            p.terminate()
        return p.wait()


if __name__ == '__main__':
    sys.exit(main())
