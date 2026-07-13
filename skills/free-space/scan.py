#!/usr/bin/env python3
"""free — map macOS storage hotspots and render an interactive HTML report.

This never deletes anything. It scans the home folder in a single `du` pass,
sizes a curated catalog of known macOS space hogs, groups them by how safe they
are to reclaim, and writes a self-contained report to
    ./storage-report/hot-spots-<timestamp>.html

Design notes (ponytail):
- stdlib only; the heavy lifting is `du`/`df` (native, fast, edge-case correct).
- one `du -kxd<depth> $HOME` walk feeds the treemap, the catalog lookups, and
  node_modules discovery, instead of re-walking each subtree.
- the report is progressive-enhancement HTML: fully readable with JS off, with
  the treemap / filters / copy buttons layered on top.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

KB = 1024

# --- catalog of known macOS storage hotspots -------------------------------
# Each entry: label, paths (glob/~ ok, summed), category, safety, why, reclaim.
# safety: "safe" (regenerable junk), "review" (you decide), "caution" (app data).
# Entries that don't exist on disk are dropped, so this list is noise-free.
CATALOG = [
    # Developer / build artifacts
    {"label": "Xcode DerivedData", "paths": ["~/Library/Developer/Xcode/DerivedData"],
     "category": "Developer", "safety": "safe",
     "why": "Xcode build intermediates and indexes. Rebuilt automatically on the next build.",
     "reclaim": "rm -rf ~/Library/Developer/Xcode/DerivedData/*"},
    {"label": "Xcode device support", "paths": ["~/Library/Developer/Xcode/*DeviceSupport"],
     "category": "Developer", "safety": "safe",
     "why": "Debug symbols for old iOS/watchOS/tvOS versions. Re-downloaded when you attach a device.",
     "reclaim": 'rm -rf ~/Library/Developer/Xcode/*DeviceSupport/*'},
    {"label": "iOS Simulators", "paths": ["~/Library/Developer/CoreSimulator"],
     "category": "Developer", "safety": "review",
     "why": "Simulator runtimes and devices. Old/unavailable ones are pure waste.",
     "reclaim": "xcrun simctl delete unavailable"},
    {"label": "Xcode Archives", "paths": ["~/Library/Developer/Xcode/Archives"],
     "category": "Developer", "safety": "review",
     "why": "Archived app builds. You may need these to re-submit or symbolicate crashes.",
     "reclaim": None},
    {"label": "Homebrew cache", "paths": ["~/Library/Caches/Homebrew", "/opt/homebrew/Caches",
                                          "/Library/Caches/Homebrew"],
     "category": "Developer", "safety": "safe",
     "why": "Downloaded bottles and old versions Homebrew keeps around.",
     "reclaim": "brew cleanup -s"},
    {"label": "npm cache", "paths": ["~/.npm"],
     "category": "Developer", "safety": "safe",
     "why": "npm's package cache. Rebuilt on next install.",
     "reclaim": "npm cache clean --force"},
    {"label": "Yarn cache", "paths": ["~/Library/Caches/Yarn", "~/.cache/yarn"],
     "category": "Developer", "safety": "safe",
     "why": "Yarn's global package cache.", "reclaim": "yarn cache clean"},
    {"label": "pnpm store", "paths": ["~/Library/pnpm/store", "~/.pnpm-store"],
     "category": "Developer", "safety": "review",
     "why": "pnpm's content-addressed store, shared across projects.",
     "reclaim": "pnpm store prune"},
    {"label": "CocoaPods cache", "paths": ["~/Library/Caches/CocoaPods", "~/.cocoapods/repos"],
     "category": "Developer", "safety": "safe",
     "why": "Downloaded pods and the spec repo mirror.", "reclaim": "pod cache clean --all"},
    {"label": "Gradle caches", "paths": ["~/.gradle/caches"],
     "category": "Developer", "safety": "safe",
     "why": "Gradle's downloaded dependencies and build cache. Re-downloaded on next build.",
     "reclaim": "rm -rf ~/.gradle/caches"},
    {"label": "Maven repository", "paths": ["~/.m2/repository"],
     "category": "Developer", "safety": "review",
     "why": "Downloaded Java dependencies. Re-downloadable but slow to refetch.",
     "reclaim": None},
    {"label": "Go build cache", "paths": ["~/Library/Caches/go-build"],
     "category": "Developer", "safety": "safe",
     "why": "Go's compiler cache. Rebuilt on next build.", "reclaim": "go clean -cache"},
    {"label": "Go modules", "paths": ["~/go/pkg/mod"],
     "category": "Developer", "safety": "review",
     "why": "Downloaded Go module sources.", "reclaim": "go clean -modcache"},
    {"label": "Cargo registry", "paths": ["~/.cargo/registry"],
     "category": "Developer", "safety": "review",
     "why": "Downloaded Rust crate sources and their cache.", "reclaim": None},
    {"label": "Rust toolchains", "paths": ["~/.rustup/toolchains"],
     "category": "Developer", "safety": "review",
     "why": "Installed Rust toolchains. Remove unused ones with rustup.",
     "reclaim": "rustup toolchain list"},
    {"label": "pip cache", "paths": ["~/Library/Caches/pip"],
     "category": "Developer", "safety": "safe",
     "why": "pip's download/wheel cache.", "reclaim": "pip cache purge"},
    {"label": "Playwright / Puppeteer browsers", "paths": ["~/Library/Caches/ms-playwright",
                                                           "~/.cache/puppeteer", "~/.cache/ms-playwright"],
     "category": "Developer", "safety": "safe",
     "why": "Headless browser binaries downloaded for test automation.", "reclaim": None},
    {"label": "Docker data", "paths": ["~/Library/Containers/com.docker.docker/Data"],
     "category": "Developer", "safety": "review",
     "why": "Docker's disk image (images, volumes, build cache). Can grow to tens of GB.",
     "reclaim": "docker system prune -a --volumes"},

    # General macOS junk
    {"label": "User caches", "paths": ["~/Library/Caches"],
     "category": "System", "safety": "safe",
     "why": "Per-app caches. Apps regenerate what they need; safe to clear when apps are closed.",
     "reclaim": None},
    {"label": "User logs", "paths": ["~/Library/Logs"],
     "category": "System", "safety": "safe",
     "why": "Application and diagnostic logs.", "reclaim": None},
    {"label": "Trash", "paths": ["~/.Trash"],
     "category": "System", "safety": "safe",
     "why": "Files you already deleted but never emptied.",
     "reclaim": "osascript -e 'tell app \"Finder\" to empty trash'"},

    # User data — review before touching
    {"label": "Downloads", "paths": ["~/Downloads"],
     "category": "Personal", "safety": "review",
     "why": "Often full of installers and one-off files you no longer need.", "reclaim": None},
    {"label": "iOS device backups", "paths": ["~/Library/Application Support/MobileSync/Backup"],
     "category": "Personal", "safety": "review",
     "why": "Local iPhone/iPad backups. Big, and possibly your only backup — check before deleting.",
     "reclaim": None},

    # App data — handle with care
    {"label": "Photos library", "paths": ["~/Pictures/Photos Library.photoslibrary"],
     "category": "Personal", "safety": "caution",
     "why": "Your photo library. Turn on iCloud Photos → Optimize Mac Storage instead of deleting.",
     "reclaim": None},
    {"label": "Mail", "paths": ["~/Library/Mail"],
     "category": "Personal", "safety": "caution",
     "why": "Downloaded mail and attachments. Manage from Mail, not the Finder.", "reclaim": None},
    {"label": "Music library", "paths": ["~/Music/Music/Media.localized", "~/Music/iTunes"],
     "category": "Personal", "safety": "review",
     "why": "Local music files.", "reclaim": None},
    {"label": "Application Support", "paths": ["~/Library/Application Support"],
     "category": "System", "safety": "caution",
     "why": "Working data for installed apps. Deleting blindly breaks apps — prune per-app.",
     "reclaim": None},
    {"label": "App containers", "paths": ["~/Library/Containers", "~/Library/Group Containers"],
     "category": "System", "safety": "caution",
     "why": "Sandboxed app data (Messages, Mail attachments, etc.). Prune per-app.", "reclaim": None},
]

SAFETY_ORDER = {"safe": 0, "review": 1, "caution": 2}


# --- disk scanning ---------------------------------------------------------

def run(cmd, timeout):
    """Run a command, return stdout (str) or None on any failure/timeout.

    stderr is discarded on purpose: du spews 'Permission denied' for TCC- and
    root-protected paths, which we treat as "unmeasurable", not fatal.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except (subprocess.TimeoutExpired, OSError):
        return None


def du_tree(root, depth, timeout):
    """One `du` walk of `root`. Returns (path->bytes dict, timed_out)."""
    out = run(["du", "-k", "-x", "-d", str(depth), root], timeout)
    if out is None:
        return {}, True
    tree = {}
    for line in out.splitlines():
        kb, _, path = line.partition("\t")
        if path:
            try:
                tree[path] = int(kb) * KB
            except ValueError:
                pass
    return tree, False


def du_path(path, timeout):
    """Size a single path with `du -sk`. None if unreadable."""
    out = run(["du", "-s", "-k", "-x", path], timeout)
    if not out:
        return None
    kb = out.split("\t", 1)[0].strip()
    try:
        return int(kb) * KB
    except ValueError:
        return None


def size_of(pathspec, tree, timeout):
    """Total bytes for a catalog pathspec (~ and globs expanded, matches summed).

    Uses the du tree when the path was already measured, else a targeted du.
    Returns (bytes or None, matched_paths). None => nothing on disk / unreadable.
    """
    matches = glob.glob(os.path.expanduser(pathspec))
    if not matches:
        return None, []
    total, found = 0, []
    for m in matches:
        m = os.path.abspath(m)
        b = tree.get(m)
        if b is None:
            b = du_path(m, timeout)
        if b is not None:
            total += b
            found.append(m)
    return (total if found else None), found


def volume_info(mount="/"):
    out = run(["df", "-k", mount], 15)
    if not out:
        return None
    rows = out.splitlines()
    if len(rows) < 2:
        return None
    f = rows[1].split()
    try:
        total, free = int(f[1]) * KB, int(f[3]) * KB
    except (IndexError, ValueError):
        return None
    # On APFS, df's "Used" column counts only this volume's own files, not the
    # sibling volumes sharing the container — so it wildly understates a full
    # disk. total - available is the honest "used" (matches About This Mac).
    used = max(total - free, 0)
    return {"mount": mount, "total": total, "used": used, "free": free,
            "capacity_pct": round(used / total * 100) if total else 0}


def local_snapshots():
    """Time Machine local snapshots — invisible space macOS reclaims under pressure."""
    out = run(["tmutil", "listlocalsnapshots", "/"], 15)
    if not out:
        return 0
    return sum(1 for ln in out.splitlines() if "com.apple.TimeMachine" in ln)


def find_node_modules(tree):
    """Top-level node_modules dirs from the du tree (nested ones excluded)."""
    out = []
    for path, size in tree.items():
        if os.path.basename(path) != "node_modules":
            continue
        head = path[: path.rfind("/node_modules")]
        if "/node_modules/" in head or head.endswith("/node_modules"):
            continue  # nested inside another node_modules, already counted in its parent
        out.append({"path": path, "bytes": size})
    out.sort(key=lambda d: d["bytes"], reverse=True)
    return out


def cap_breakdown(items, n=28):
    """Keep the top n home folders; roll the long tail into one 'Other' cell so
    the treemap stays legible instead of dissolving into slivers."""
    items = [i for i in items if i["bytes"] > 0]
    if len(items) <= n + 1:
        return items
    tail = items[n:]
    return items[:n] + [{"name": f"Other ({len(tail)} folders)", "path": "",
                         "bytes": sum(i["bytes"] for i in tail)}]


def scan(home, depth, broad_timeout, target_timeout, fast):
    started = datetime.now()
    errors = []

    tree, timed_out = ({}, False)
    if not fast:
        tree, timed_out = du_tree(home, depth, broad_timeout)
        if timed_out:
            errors.append(f"Home scan exceeded {broad_timeout}s; showing known hotspots only.")

    # In fast mode there is no tree; don't walk all of home just for the total.
    home_total = tree.get(home) or (None if fast else du_path(home, broad_timeout))

    # Home children -> treemap breakdown.
    breakdown = []
    for path, size in tree.items():
        if os.path.dirname(path) == home and size > 0:
            breakdown.append({"name": os.path.basename(path), "path": path, "bytes": size})
    breakdown.sort(key=lambda d: d["bytes"], reverse=True)
    breakdown = cap_breakdown(breakdown)

    # Catalog hotspots. A catalog entry's paths are distinct locations of the
    # same junk (e.g. Homebrew cache in ~/Library and /opt/homebrew) — sum them.
    hotspots = []
    for entry in CATALOG:
        total, paths_found = 0, []
        for spec in entry["paths"]:
            b, found = size_of(spec, tree, target_timeout)
            if b is not None:
                total += b
                paths_found += found
        if not paths_found or total < KB * KB:  # skip missing / negligible (<1 MB)
            continue
        hotspots.append({
            "label": entry["label"], "bytes": total, "paths": paths_found,
            "category": entry["category"], "safety": entry["safety"],
            "why": entry["why"], "reclaim": entry["reclaim"],
        })
    hotspots.sort(key=lambda h: (SAFETY_ORDER[h["safety"]], -h["bytes"]))

    node_modules = find_node_modules(tree)
    nm_total = sum(n["bytes"] for n in node_modules)

    summary = {"safe": 0, "review": 0, "caution": 0}
    for h in hotspots:
        summary[h["safety"]] += h["bytes"]

    snap_count = local_snapshots()

    return {
        "generated_at": started.strftime("%Y-%m-%d %H:%M"),
        "generated_iso": started.isoformat(timespec="seconds"),
        "host": os.uname().nodename,
        "home": home,
        "home_total": home_total,
        "volume": volume_info("/"),
        "breakdown": breakdown,
        "hotspots": hotspots,
        "node_modules": node_modules[:25],
        "node_modules_total": nm_total,
        "node_modules_count": len(node_modules),
        "reclaimable": summary["safe"],
        "summary": summary,
        "snapshots": snap_count,
        "scan": {"depth": depth, "duration_s": round((datetime.now() - started).total_seconds(), 1),
                 "timed_out": timed_out, "fast": fast, "errors": errors},
    }


# --- HTML report -----------------------------------------------------------

def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit in ("B", "KB", "MB"):
                return f"{n:.0f} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


SAFETY_META = {
    "safe": ("Safe to reclaim", "safe"),
    "review": ("Review first", "review"),
    "caution": ("Handle with care", "caution"),
}

CSS = """
:root{
  --bg:#f4f5f7; --panel:#ffffff; --ink:#16181d; --muted:#666c78; --line:#e6e8ec;
  --accent:#0a84ff; --safe:#2ba24c; --review:#e08600; --caution:#e0483a;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 8px 24px rgba(0,0,0,.05);
}
:root[data-theme=dark]{
  --bg:#0d1117; --panel:#161b22; --ink:#e6edf3; --muted:#9aa4b2; --line:#2a313c;
  --accent:#4aa3ff; --safe:#3fb950; --review:#e3a008; --caution:#f8664f;
  --shadow:0 1px 2px rgba(0,0,0,.4);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme]){
    --bg:#0d1117; --panel:#161b22; --ink:#e6edf3; --muted:#9aa4b2; --line:#2a313c;
    --accent:#4aa3ff; --safe:#3fb950; --review:#e3a008; --caution:#f8664f;
    --shadow:0 1px 2px rgba(0,0,0,.4);
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1060px;margin:0 auto;padding:32px 20px 80px}
a{color:var(--accent)}
code,.mono{font-family:ui-monospace,"SF Mono",Menlo,monospace}
h1{font-size:26px;margin:0;letter-spacing:-.02em}
h2{font-size:18px;margin:40px 0 14px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.theme{border:1px solid var(--line);background:var(--panel);color:var(--muted);
  border-radius:8px;padding:6px 10px;cursor:pointer;font-size:13px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  box-shadow:var(--shadow)}
/* gauge */
.gauge{padding:20px 22px;margin-top:22px}
.gauge .bar{height:16px;border-radius:8px;background:var(--line);overflow:hidden;display:flex}
.gauge .used{background:linear-gradient(90deg,var(--accent),#7db8ff)}
.gauge .row{display:flex;justify-content:space-between;font-size:13px;color:var(--muted);margin-top:10px}
/* stat tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-top:14px}
.tile{padding:16px 18px}
.tile .k{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.tile .v{font-size:26px;font-weight:650;margin-top:4px;letter-spacing:-.02em}
.tile.hi .v{color:var(--safe)}
/* treemap */
#treemap{position:relative;width:100%;height:min(58vh,480px);margin-top:6px;
  border-radius:14px;overflow:hidden;border:1px solid var(--line)}
.cell{position:absolute;overflow:hidden;color:#fff;padding:8px 10px;
  border:1px solid rgba(0,0,0,.14);transition:filter .12s}
.cell:hover{filter:brightness(1.08)}
.cell .n{font-size:12px;font-weight:600}
.cell .s{font-size:11px;opacity:.9}
.tm-list{list-style:none;margin:0;padding:14px 18px}
.tm-list li{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--line)}
/* filters */
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 16px}
.filters button{border:1px solid var(--line);background:var(--panel);color:var(--ink);
  border-radius:999px;padding:6px 14px;cursor:pointer;font-size:13px}
.filters button[aria-pressed=true]{background:var(--accent);color:#fff;border-color:var(--accent)}
/* hotspot cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.card{padding:16px 18px;border-left:4px solid var(--line)}
.card[data-safety=safe]{border-left-color:var(--safe)}
.card[data-safety=review]{border-left-color:var(--review)}
.card[data-safety=caution]{border-left-color:var(--caution)}
.card .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.card .label{font-weight:620}
.card .size{font-variant-numeric:tabular-nums;font-weight:650;white-space:nowrap}
.card .meter{height:6px;background:var(--line);border-radius:4px;margin:10px 0;overflow:hidden}
.card .meter i{display:block;height:100%;border-radius:4px;background:var(--accent)}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px;
  margin-right:6px}
.badge.safe{background:color-mix(in srgb,var(--safe) 18%,transparent);color:var(--safe)}
.badge.review{background:color-mix(in srgb,var(--review) 20%,transparent);color:var(--review)}
.badge.caution{background:color-mix(in srgb,var(--caution) 18%,transparent);color:var(--caution)}
.badge.cat{background:var(--line);color:var(--muted)}
.card .why{color:var(--muted);font-size:13px;margin:8px 0 0}
.cmd{display:flex;align-items:center;gap:8px;margin-top:10px;background:var(--bg);
  border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.cmd code{font-size:12px;overflow-x:auto;white-space:nowrap;flex:1}
.cmd button{border:1px solid var(--line);background:var(--panel);color:var(--muted);
  border-radius:6px;padding:3px 8px;cursor:pointer;font-size:11px;white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th:last-child,td:last-child{text-align:right;font-variant-numeric:tabular-nums}
.note{color:var(--muted);font-size:12px;margin-top:8px}
.warn{background:color-mix(in srgb,var(--review) 12%,var(--panel));
  border:1px solid color-mix(in srgb,var(--review) 40%,var(--line))}
.warn .panel-in{padding:14px 18px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = """
const DATA = JSON.parse(document.getElementById('report-data').textContent);
// theme toggle
const root = document.documentElement;
document.getElementById('theme').onclick = () => {
  const dark = getComputedStyle(root).getPropertyValue('--bg').trim().startsWith('#0');
  root.setAttribute('data-theme', dark ? 'light' : 'dark');
};
// copy buttons
for (const b of document.querySelectorAll('button[data-cmd]')) {
  b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.cmd);
      const t = b.textContent; b.textContent = 'Copied'; setTimeout(()=>b.textContent=t,1200); }
    catch(e){}
  };
}
// filters
const buttons = document.querySelectorAll('.filters button');
for (const b of buttons) b.onclick = () => {
  buttons.forEach(x=>x.setAttribute('aria-pressed', x===b));
  const f = b.dataset.filter;
  for (const c of document.querySelectorAll('.card'))
    c.style.display = (f==='all'||c.dataset.safety===f) ? '' : 'none';
};
// treemap (squarified)
function worst(row, thick){ let m=0; for(const it of row){const len=it._a/thick;
  const ar=Math.max(thick/len,len/thick); if(ar>m)m=ar;} return m; }
function squarify(items, W, H){
  items = items.filter(i=>i.bytes>0).sort((a,b)=>b.bytes-a.bytes);
  const total = items.reduce((s,i)=>s+i.bytes,0); if(!total) return [];
  const scale=(W*H)/total; items.forEach(i=>i._a=i.bytes*scale);
  const out=[]; let x=0,y=0,w=W,h=H,i=0;
  while(i<items.length){
    const shorter=Math.min(w,h);
    let row=[items[i]], sum=items[i]._a, j=i+1;
    while(j<items.length){
      const a=worst(row, sum/shorter), b=worst(row.concat(items[j]),(sum+items[j]._a)/shorter);
      if(b<=a){row.push(items[j]); sum+=items[j]._a; j++;} else break;
    }
    const thick=sum/shorter; let off=0;
    for(const it of row){ const len=it._a/thick;
      if(w>=h){it.x=x; it.y=y+off; it.w=thick; it.h=len;}
      else {it.x=x+off; it.y=y; it.w=len; it.h=thick;}
      off+=len; out.push(it); }
    if(w>=h){x+=thick; w-=thick;} else {y+=thick; h-=thick;}
    i=j;
  }
  return out;
}
function fmt(n){ n=+n; const u=['B','KB','MB','GB','TB']; let k=0;
  while(n>=1024&&k<4){n/=1024;k++;} return (k<3?n.toFixed(0):n.toFixed(1))+' '+u[k]; }
function drawTreemap(){
  const el=document.getElementById('treemap'); if(!el) return;
  const W=el.clientWidth, H=el.clientHeight; if(W<10||H<10) return;
  const cells=squarify(DATA.breakdown.map(d=>({...d})), W, H);
  const max=Math.max(...cells.map(c=>c.bytes));
  el.innerHTML='';
  for(const c of cells){
    const t=c.bytes/max; const L=66-t*32; // bigger => deeper blue
    const light=L>52;
    const d=document.createElement('div'); d.className='cell';
    d.style.left=c.x+'px'; d.style.top=c.y+'px'; d.style.width=c.w+'px'; d.style.height=c.h+'px';
    d.style.background='hsl(212 68% '+L+'%)';
    d.style.color=light?'#0a1a2f':'#fff';
    d.style.textShadow=light?'none':'0 1px 2px rgba(0,0,0,.35)';
    d.title=c.name+' — '+fmt(c.bytes);
    if(c.w>62&&c.h>30) d.innerHTML='<div class="n">'+c.name+'</div><div class="s">'+fmt(c.bytes)+'</div>';
    el.appendChild(d);
  }
}
drawTreemap();
let rid; addEventListener('resize',()=>{clearTimeout(rid); rid=setTimeout(drawTreemap,150);});
"""


def render_html(d):
    vol = d["volume"] or {"total": 0, "used": 0, "free": 0, "capacity_pct": 0}
    used_pct = vol["capacity_pct"]
    max_hot = max((h["bytes"] for h in d["hotspots"]), default=1)

    p = []
    p.append('<!doctype html><html lang="en"><head><meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    p.append(f'<title>Storage report — {esc(d["host"])}</title>')
    p.append("<style>"); p.append(CSS); p.append("</style></head><body><div class='wrap'>")

    # header
    p.append("<header><div>")
    p.append("<h1>Storage report</h1>")
    home_bit = f' · home {human(d["home_total"])}' if d["home_total"] else ""
    p.append(f'<div class="sub">{esc(d["host"])} · {esc(d["generated_at"])}{home_bit}</div>')
    p.append("</div><button class='theme' id='theme'>Toggle theme</button></header>")

    # disk gauge
    p.append('<div class="panel gauge">')
    p.append(f'<div style="font-weight:620;margin-bottom:10px">Startup disk — {used_pct}% full</div>')
    p.append(f'<div class="bar"><div class="used" style="width:{used_pct}%"></div></div>')
    p.append(f'<div class="row"><span>{human(vol["used"])} used</span>'
             f'<span>{human(vol["free"])} free of {human(vol["total"])}</span></div></div>')

    # stat tiles
    p.append('<div class="tiles">')
    p.append(f'<div class="panel tile hi"><div class="k">Reclaim now (safe)</div>'
             f'<div class="v">{human(d["reclaimable"])}</div></div>')
    p.append(f'<div class="panel tile"><div class="k">Review candidates</div>'
             f'<div class="v">{human(d["summary"]["review"])}</div></div>')
    p.append(f'<div class="panel tile"><div class="k">node_modules ×{d["node_modules_count"]}</div>'
             f'<div class="v">{human(d["node_modules_total"])}</div></div>')
    snap = d["snapshots"]
    p.append(f'<div class="panel tile"><div class="k">Local snapshots</div>'
             f'<div class="v">{snap}</div></div>')
    p.append("</div>")

    # treemap
    p.append("<h2>Where your home folder space goes</h2>")
    p.append('<div id="treemap"><ul class="tm-list">')
    for b in d["breakdown"][:20]:
        pct = (b["bytes"] / d["home_total"] * 100) if d["home_total"] else 0
        p.append(f'<li><span>{esc(b["name"])}</span><span>{human(b["bytes"])} '
                 f'({pct:.0f}%)</span></li>')
    p.append("</ul></div>")
    if not d["breakdown"]:
        p.append('<p class="note">Home breakdown unavailable (fast mode or scan timed out).</p>')

    # hotspots
    p.append("<h2>Cleanup hotspots</h2>")
    p.append('<div class="filters">'
             '<button data-filter="all" aria-pressed="true">All</button>'
             '<button data-filter="safe">Safe</button>'
             '<button data-filter="review">Review</button>'
             '<button data-filter="caution">Careful</button></div>')
    p.append('<div class="cards">')
    for h in d["hotspots"]:
        label_txt, cls = SAFETY_META[h["safety"]]
        pct = h["bytes"] / max_hot * 100
        p.append(f'<div class="card" data-safety="{cls}">')
        p.append(f'<div class="top"><span class="label">{esc(h["label"])}</span>'
                 f'<span class="size">{human(h["bytes"])}</span></div>')
        p.append(f'<div class="meter"><i style="width:{pct:.1f}%"></i></div>')
        p.append(f'<span class="badge {cls}">{label_txt}</span>'
                 f'<span class="badge cat">{esc(h["category"])}</span>')
        p.append(f'<p class="why">{esc(h["why"])}</p>')
        if h["reclaim"]:
            p.append(f'<div class="cmd"><code>{esc(h["reclaim"])}</code>'
                     f'<button data-cmd="{esc(h["reclaim"])}">Copy</button></div>')
        p.append("</div>")
    p.append("</div>")

    # node_modules
    if d["node_modules"]:
        p.append("<h2>Biggest node_modules</h2>")
        p.append('<div class="panel" style="padding:6px 8px"><table><thead><tr>'
                 "<th>Project</th><th>Size</th></tr></thead><tbody>")
        for n in d["node_modules"]:
            proj = n["path"].replace(d["home"], "~")
            p.append(f'<tr><td class="mono">{esc(proj)}</td><td>{human(n["bytes"])}</td></tr>')
        p.append("</tbody></table></div>")
        p.append('<p class="note">Safe to delete in projects you are not actively building — '
                 "<code>npm install</code> rebuilds them.</p>")

    # safety footer
    p.append("<h2>Before you delete anything</h2>")
    p.append('<div class="panel warn"><div class="panel-in">')
    p.append("<b>This tool never deletes anything.</b> It only measures and explains. "
             "You run the reclaim commands yourself, after reviewing them.")
    p.append('<ul style="margin:10px 0 0;padding-left:20px;color:var(--muted);font-size:13px">')
    p.append("<li><b>Safe</b> = regenerable caches/build output. <b>Review</b> = you decide. "
             "<b>Careful</b> = live app data; prune from within the app.</li>")
    p.append("<li>Quit the relevant app before clearing its cache.</li>")
    p.append("<li>Sizes come from <code>du</code>; APFS clones/snapshots mean freed space "
             "can differ from what is shown.</li>")
    if d["scan"]["errors"]:
        p.append("<li>" + "; ".join(esc(e) for e in d["scan"]["errors"]) + "</li>")
    p.append("<li>Some folders (Desktop, Documents, Downloads) may be under-counted if the "
             "terminal lacks Full Disk Access in System Settings → Privacy &amp; Security.</li>")
    p.append("</ul></div></div>")

    p.append(f'<p class="note">Scanned in {d["scan"]["duration_s"]}s · depth {d["scan"]["depth"]}'
             f'{" · fast mode" if d["scan"]["fast"] else ""}. Generated by the '
             "<code>free</code> plugin.</p>")

    p.append('<script type="application/json" id="report-data">')
    p.append(json.dumps(d)); p.append("</script><script>"); p.append(JS)
    p.append("</script></div></body></html>")
    return "".join(p)


# --- compact stdout summary for the calling agent --------------------------

def stdout_summary(d, report_path):
    def gb(n):
        return round((n or 0) / KB ** 3, 2)
    return {
        "report": report_path,
        "host": d["host"],
        "volume_gb": {"total": gb(d["volume"]["total"]), "used": gb(d["volume"]["used"]),
                      "free": gb(d["volume"]["free"]), "pct_full": d["volume"]["capacity_pct"]}
        if d["volume"] else None,
        "reclaimable_gb": gb(d["reclaimable"]),
        "summary_gb": {k: gb(v) for k, v in d["summary"].items()},
        "node_modules": {"count": d["node_modules_count"], "gb": gb(d["node_modules_total"])},
        "top_hotspots": [{"label": h["label"], "gb": gb(h["bytes"]), "safety": h["safety"],
                          "category": h["category"], "reclaim": h["reclaim"]}
                         for h in sorted(d["hotspots"], key=lambda h: -h["bytes"])[:12]],
        "scan": d["scan"],
    }


# --- self-check (ponytail: one runnable check on the non-trivial logic) -----

def selftest():
    assert human(0) == "0 B"
    assert human(1023) == "1023 B"
    assert human(1024) == "1 KB"
    assert human(5 * KB ** 3) == "5.0 GB"
    assert esc('<a href="x">&') == "&lt;a href=&quot;x&quot;&gt;&amp;"

    # du parsing + node_modules dedup (nested must not double count)
    fake = ("2048\t/h/a/node_modules\n"
            "512\t/h/a/node_modules/dep/node_modules\n"
            "4096\t/h/b\n"
            "100\t/h\n")
    tree = {}
    for line in fake.splitlines():
        kb, _, path = line.partition("\t")
        tree[path] = int(kb) * KB
    nm = find_node_modules(tree)
    assert [n["path"] for n in nm] == ["/h/a/node_modules"], nm
    assert nm[0]["bytes"] == 2048 * KB

    # cap_breakdown rolls the long tail into a single 'Other' cell
    big = [{"name": f"d{i}", "path": f"/h/d{i}", "bytes": (40 - i) * KB} for i in range(40)]
    capped = cap_breakdown(big, n=5)
    assert len(capped) == 6 and capped[-1]["name"] == "Other (35 folders)"
    assert capped[-1]["bytes"] == sum(d["bytes"] for d in big[5:])
    assert cap_breakdown(big[:3], n=5) == big[:3]  # under threshold, unchanged

    # size_of resolves against the real FS, then prefers the du tree value.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        real = os.path.abspath(td)
        b, found = size_of(real, {real: 4096 * KB}, 5)
        assert b == 4096 * KB and found == [real], (b, found)
        assert size_of(os.path.join(real, "nope-xyzzy"), {}, 5) == (None, [])

    # render_html must not throw and must embed the data island
    sample = {
        "generated_at": "2026-07-13 20:00", "generated_iso": "2026-07-13T20:00:00",
        "host": "mac", "home": "/h", "home_total": 6244, "volume":
        {"total": KB ** 4, "used": KB ** 3, "free": KB ** 4 - KB ** 3, "capacity_pct": 6},
        "breakdown": [{"name": "b", "path": "/h/b", "bytes": 4096 * KB},
                      {"name": "a", "path": "/h/a", "bytes": 2048 * KB}],
        "hotspots": [{"label": "Trash", "bytes": 2048 * KB, "paths": ["/h/.Trash"],
                      "category": "System", "safety": "safe", "why": "junk",
                      "reclaim": "echo hi"}],
        "node_modules": nm, "node_modules_total": nm[0]["bytes"], "node_modules_count": 1,
        "reclaimable": 2048 * KB, "summary": {"safe": 2048 * KB, "review": 0, "caution": 0},
        "snapshots": 0,
        "scan": {"depth": 6, "duration_s": 0.1, "timed_out": False, "fast": False, "errors": []},
    }
    out = render_html(sample)
    assert out.startswith("<!doctype html>")
    assert 'id="report-data"' in out and "squarify" in out

    s = stdout_summary(sample, "x.html")
    assert s["report"] == "x.html"
    assert s["reclaimable_gb"] == round(2048 * KB / KB ** 3, 2)
    assert s["node_modules"]["count"] == 1
    print("selftest ok")


# --- entry point -----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Map macOS storage hotspots (read-only).")
    ap.add_argument("--out-dir", default="./storage-report", help="where to write the HTML report")
    ap.add_argument("--home", default=os.path.expanduser("~"), help="folder to scan")
    ap.add_argument("--depth", type=int, default=6, help="du depth for the home walk")
    ap.add_argument("--max-seconds", type=int, default=300, help="timeout for the home walk")
    ap.add_argument("--target-timeout", type=int, default=90, help="timeout per targeted du")
    ap.add_argument("--fast", action="store_true", help="skip the home walk; catalog only")
    ap.add_argument("--json", action="store_true", help="print the full report JSON to stdout")
    ap.add_argument("--selftest", action="store_true", help="run internal checks and exit")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    home = os.path.abspath(os.path.expanduser(args.home))
    print(f"Scanning {home} … (this can take a minute on a full disk)", file=sys.stderr)
    d = scan(home, args.depth, args.max_seconds, args.target_timeout, args.fast)

    os.makedirs(args.out_dir, exist_ok=True)
    fname = f"hot-spots-{datetime.now().strftime('%Y-%m-%d-%H-%M')}.html"
    report_path = os.path.abspath(os.path.join(args.out_dir, fname))
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(render_html(d))

    print(json.dumps(d if args.json else stdout_summary(d, report_path), indent=2))
    print(f"\nReport: {report_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
