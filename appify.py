#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10"]
# ///
"""appify: turn a local web page into a real Mac app.

Pake (github.com/tw93/Pake) does the packaging. appify adds the three things a
bare Pake build is missing for a local dashboard:

  1. it installs into /Applications, replacing only apps appify built itself;
  2. it gives the app a real icon (generated when you don't pass one);
  3. it never opens on a dead page: a launcher inside the bundle starts the
     page's launchd service when the port is down and waits for it.

  appify 4142 --name Brain --style graph
  appify http://127.0.0.1:4040 --name Numen --color "#3fb68b"
  appify 4142 --name Brain -- --hide-title-bar      (extra args go to Pake)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ID_PREFIX = "local.appify."
BUILD_ROOT = Path.home() / ".appify" / "build"
FONT = "/System/Library/Fonts/SFNSRounded.ttf"

# Pake is pinned and patched, not fetched fresh by npx: stock Pake hard-codes
# "Pake" as the About item in every app's menu (src-tauri/src/app/menu.rs).
# A new version must be re-checked against MENU_ORIGINAL before bumping.
PAKE_VERSION = "3.17.2"
PAKE_HOME = Path.home() / ".appify" / "pake"
PAKE_PKG = PAKE_HOME / "node_modules" / "pake-cli"
PATCH_MARK = "// appify: menu uses the app's own name"
MENU_ORIGINAL = """    let app_menu = Submenu::new(app, "Pake", true)?;
    let about_metadata = AboutMetadata::default();
    app_menu.append(&PredefinedMenuItem::about(
        app,
        Some("Pake"),
        Some(about_metadata),
    )?)?;"""
MENU_PATCHED = f"""    {PATCH_MARK}
    let name = app.config().product_name.clone().unwrap_or_else(|| "App".to_string());
    let app_menu = Submenu::new(app, &name, true)?;
    let about_metadata = AboutMetadata {{
        name: Some(name.clone()),
        version: Some(app.package_info().version.to_string()),
        ..Default::default()
    }};
    app_menu.append(&PredefinedMenuItem::about(
        app,
        Some(&format!("About {{name}}")),
        Some(about_metadata),
    )?)?;"""


def log(msg: str) -> None:
    print(f"appify: {msg}", file=sys.stderr)


def die(msg: str) -> None:
    log(msg)
    sys.exit(1)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"


# ---------------------------------------------------------------- target

def resolve_url(target: str) -> tuple[str, str, int]:
    """'4142' or a URL → (url, host, port)."""
    if target.isdigit():
        target = f"http://127.0.0.1:{target}"
    u = urlparse(target)
    if u.scheme not in ("http", "https") or not u.hostname:
        die(f"not a port or http(s) URL: {target}")
    port = u.port or (443 if u.scheme == "https" else 80)
    return target, u.hostname, port


def is_local(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def detect_service(port: int) -> tuple[str | None, str | None]:
    """Find the launchd job serving this port: listening PID → launchctl label.

    Returns (label, plist path). Both None when the port is served by
    something launchd doesn't manage (a terminal, a dev server)."""
    pids = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                          capture_output=True, text=True).stdout.split()
    if not pids:
        return None, None
    rows = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    for line in rows.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] in pids:
            return parts[2], service_plist(parts[2])
    return None, None


def service_plist(label: str) -> str | None:
    out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                         capture_output=True, text=True).stdout
    m = re.search(r"^\s+path = (.+)$", out, re.M)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- icon

def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        die(f"--color needs #rrggbb, got {h}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def name_color(name: str) -> tuple[int, int, int]:
    """Stable, pleasant accent per app name, so rebuilds keep their colour."""
    hue = int(hashlib.sha1(name.encode()).hexdigest()[:4], 16) / 0xFFFF
    import colorsys
    r, g, b = colorsys.hls_to_rgb(hue, 0.58, 0.55)
    return int(r * 255), int(g * 255), int(b * 255)


def draw_icon(name: str, style: str, accent: tuple[int, int, int], glyph: str | None) -> Image.Image:
    """1024px macOS icon: rounded square on Apple's 824px grid, dark ground,
    one accent colour carrying the mark."""
    S, inset = 1024, 100
    box = (inset, inset, S - inset, S - inset)
    radius = 185

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 14, box[2], box[3] + 14),
                                             radius, fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))

    ground = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(ground)
    top, bottom = (34, 36, 42), (16, 17, 21)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    img.paste(ground, (0, 0), mask)

    mark = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    if style == "graph":
        rng = random.Random(name)
        cx = cy = S / 2
        nodes = []
        while len(nodes) < 30:            # spaced sampling: no touching nodes
            a = rng.uniform(0, 2 * math.pi)
            r = 280 * math.sqrt(rng.uniform(0.02, 1))
            pt = (cx + r * math.cos(a), cy + r * math.sin(a) * 0.86)
            if all((pt[0] - x) ** 2 + (pt[1] - y) ** 2 > 70 ** 2 for x, y in nodes):
                nodes.append(pt)
        for i, (x, y) in enumerate(nodes):
            near = sorted(range(len(nodes)), key=lambda j: (nodes[j][0] - x) ** 2 + (nodes[j][1] - y) ** 2)[1:3]
            for j in near:
                md.line([(x, y), nodes[j]], fill=accent + (160,), width=9)
        for i, (x, y) in enumerate(nodes):
            rad = 27 if i % 7 == 0 else 15
            md.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(236, 234, 228, 255) if i % 7 else accent + (255,))
    else:
        text = glyph or name[:1].upper()
        font = ImageFont.truetype(FONT, 520 if len(text) == 1 else 360)
        try:
            font.set_variation_by_name("Bold")
        except (OSError, ValueError):
            pass  # not a variable font; regular weight is still fine
        bb = md.textbbox((0, 0), text, font=font)
        md.text(((S - (bb[2] - bb[0])) / 2 - bb[0], (S - (bb[3] - bb[1])) / 2 - bb[1]),
                text, font=font, fill=accent + (255,))
    clip = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    clip.paste(mark, (0, 0), mask)
    img.alpha_composite(clip)
    return img


def to_icns(png: Image.Image, out: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                suffix = "" if scale == 1 else "@2x"
                png.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{size}x{size}{suffix}.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    return out


def make_icon(args, build: Path) -> Path:
    out = build / f"{slug(args.name)}.icns"
    if args.icon:
        src = Path(args.icon).expanduser()
        if not src.exists():
            die(f"icon not found: {src}")
        if src.suffix == ".icns":
            shutil.copy(src, out)
            return out
        return to_icns(Image.open(src).convert("RGBA").resize((1024, 1024), Image.LANCZOS), out)
    accent = hex_rgb(args.color) if args.color else name_color(args.name)
    img = draw_icon(args.name, args.style, accent, args.glyph)
    img.save(build / "icon.png")
    return to_icns(img, out)


# ---------------------------------------------------------------- build

def ensure_pake() -> Path:
    """Install the pinned Pake into ~/.appify/pake once, then patch its menu.
    Idempotent: a patched copy is left alone."""
    pkg = PAKE_PKG / "package.json"
    if not pkg.exists() or json.loads(pkg.read_text()).get("version") != PAKE_VERSION:
        log(f"installing pinned pake-cli@{PAKE_VERSION} into {PAKE_HOME}")
        PAKE_HOME.mkdir(parents=True, exist_ok=True)
        subprocess.run(["npm", "install", "--prefix", str(PAKE_HOME), "--no-fund", "--no-audit",
                        f"pake-cli@{PAKE_VERSION}"], check=True, capture_output=True)
    menu = PAKE_PKG / "src-tauri" / "src" / "app" / "menu.rs"
    src = menu.read_text()
    if PATCH_MARK not in src:
        if MENU_ORIGINAL not in src:
            die(f"Pake's menu.rs changed shape; update MENU_ORIGINAL for {PAKE_VERSION}")
        menu.write_text(src.replace(MENU_ORIGINAL, MENU_PATCHED))
        log("patched Pake menu to use the app's name")
    return PAKE_PKG / "dist" / "cli.js"


def pake_build(args, url: str, icns: Path, build: Path, bundle_id: str) -> Path:
    cmd = ["node", str(ensure_pake()), url, "--name", args.name, "--identifier", bundle_id,
           "--icon", str(icns), "--width", str(args.width), "--height", str(args.height),
           "--targets", "app", "--json", *args.pake_args]
    log("building with Pake (first build compiles Rust, ~5 min; later ones are faster)")
    r = subprocess.run(cmd, cwd=build, capture_output=True, text=True)
    (build / "pake.log").write_text(r.stderr)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        die(f"Pake gave no JSON result (exit {r.returncode}), see {build / 'pake.log'}")
    if not res.get("ok"):
        err = res.get("error") or {}
        die(f"Pake failed: {err.get('message')} ({err.get('hint')})")
    apps = [o["path"] for o in res["outputs"] if o["format"] == "app"]
    if not apps:
        die("Pake reported success but produced no .app")
    return Path(apps[0])


LAUNCHER = """#!/bin/bash
# Written by appify. Starts {name}'s server before the window opens, so the
# app never shows a dead page. The real Pake binary is {core}.
PORT={port}
LABEL={label}
PLIST={plist}
START={start}
WAIT={wait}
HERE="$(cd "$(dirname "$0")" && pwd)"

up() {{ /usr/bin/nc -z -G 1 127.0.0.1 "$PORT" >/dev/null 2>&1; }}

if ! up; then
  if [ -n "$LABEL" ]; then
    /bin/launchctl kickstart "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \\
      || {{ [ -n "$PLIST" ] && /bin/launchctl bootstrap "gui/$(id -u)" "$PLIST" >/dev/null 2>&1; }}
  elif [ -n "$START" ]; then
    PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" /bin/bash -c "$START" >/dev/null 2>&1 &
  fi
  for _ in $(seq 1 $((WAIT * 4))); do up && break; sleep 0.25; done
fi

if ! up; then
  choice=$(/usr/bin/osascript -e 'button returned of (display dialog "{name} could not reach its server on port {port}." & return & return & "{hint}" buttons {{"Quit", "Open anyway"}} default button "Quit" with icon caution)' 2>/dev/null)
  [ "$choice" = "Open anyway" ] || exit 0
fi

exec "$HERE/{core}" "$@"
"""


def add_launcher(app: Path, name: str, port: int, label: str | None, plist: str | None,
                 start: str | None, wait: int) -> None:
    info = app / "Contents" / "Info.plist"
    exe = subprocess.run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleExecutable", str(info)],
                         capture_output=True, text=True, check=True).stdout.strip()
    macos = app / "Contents" / "MacOS"
    core = f"{exe}-core"
    (macos / exe).rename(macos / core)
    hint = (f"launchd service {label} did not come up." if label
            else "Its start command ran but the server did not answer." if start
            else "No launchd service serves this port, so start it yourself.")
    script = LAUNCHER.format(name=name, core=core, port=port, wait=wait,
                             label=shlex.quote(label or ""), plist=shlex.quote(plist or ""),
                             start=shlex.quote(start or ""), hint=hint)
    (macos / exe).write_text(script)
    (macos / exe).chmod(0o755)
    # The bundle changed, so its ad hoc signature must be redone or macOS
    # refuses to launch it. Ad hoc is what Pake shipped, so nothing is lost.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)],
                   check=True, capture_output=True)


# ---------------------------------------------------------------- install

def bundle_id_of(app: Path) -> str | None:
    r = subprocess.run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier",
                        str(app / "Contents" / "Info.plist")], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def install(app: Path, name: str, bundle_id: str) -> Path:
    dest = Path("/Applications") / f"{name}.app"
    if dest.exists():
        existing = bundle_id_of(dest)
        if not (existing or "").startswith(ID_PREFIX):
            die(f"{dest} exists and was not built by appify ({existing}); refusing to replace it")
        subprocess.run(["osascript", "-e", f'tell application id "{bundle_id}" to quit'],
                       capture_output=True)
        subprocess.run(["pkill", "-f", f"{dest}/Contents/MacOS/"], capture_output=True)
        shutil.rmtree(dest)
    subprocess.run(["ditto", str(app), str(dest)], check=True)
    # Nudge Finder and the Dock to pick up the new icon instead of a cached one.
    os.utime(dest, None)
    return dest


# ---------------------------------------------------------------- main

def main() -> None:
    argv = sys.argv[1:]
    pake_args: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, pake_args = argv[:i], argv[i + 1:]

    p = argparse.ArgumentParser(prog="appify", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", help="port (4142) or URL")
    p.add_argument("--name", required=True, help="app name, e.g. Brain")
    p.add_argument("--icon", help=".icns or image file; generated when omitted")
    p.add_argument("--style", choices=["letter", "graph"], default="letter",
                   help="generated icon mark (default: first letter)")
    p.add_argument("--glyph", help="text for a letter icon instead of the first letter")
    p.add_argument("--color", help="accent #rrggbb for a generated icon")
    p.add_argument("--service", help="launchd label to start when the port is down (auto-detected)")
    p.add_argument("--start-cmd", help="shell command that starts the server, used when no launchd service runs it")
    p.add_argument("--wait", type=int, default=15, help="seconds the launcher waits for the server (default 15)")
    p.add_argument("--no-service", action="store_true", help="skip the launcher entirely")
    p.add_argument("--width", type=int, default=1400)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--no-install", action="store_true", help="build only, leave the app in ~/.appify/build")
    p.add_argument("--icon-only", action="store_true", help="just write the generated icon.png and stop")
    args = p.parse_args(argv)
    args.pake_args = pake_args

    url, host, port = resolve_url(args.target)
    build = BUILD_ROOT / slug(args.name)
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    bundle_id = ID_PREFIX + slug(args.name)

    icns = make_icon(args, build)
    if args.icon_only:
        print(build / "icon.png")
        return

    label = plist = None
    if not args.no_service and is_local(host):
        if args.service:
            label, plist = args.service, service_plist(args.service)
        elif not args.start_cmd:
            label, plist = detect_service(port)
        how = label or ("start command" if args.start_cmd else None)
        log(f"server on :{port} → {how or 'no launchd service found (launcher will only wait)'}")

    app = pake_build(args, url, icns, build, bundle_id)
    if not args.no_service and is_local(host):
        add_launcher(app, args.name, port, label, plist, args.start_cmd, args.wait)

    final = app if args.no_install else install(app, args.name, bundle_id)
    print(json.dumps({"ok": True, "app": str(final), "url": url, "bundle_id": bundle_id,
                      "service": label, "start_cmd": args.start_cmd, "icon": str(icns)}))


if __name__ == "__main__":
    main()
