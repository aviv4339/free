<div align="center">

# 🧹 free

### Find what's eating your Mac's storage — and know exactly what's safe to delete.

[![License: MIT](https://img.shields.io/badge/License-MIT-informational.svg)](./LICENSE)
&nbsp;![Platform: macOS](https://img.shields.io/badge/platform-macOS-black.svg?logo=apple)
&nbsp;![Python 3](https://img.shields.io/badge/python-3.x-blue.svg?logo=python&logoColor=white)
&nbsp;![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)
&nbsp;![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A63D2.svg)

</div>

**`free`** is a [Claude Code](https://claude.com/claude-code) plugin that maps the storage hotspots on your Mac, explains what each one is and whether it's safe to remove, and generates a **beautiful, interactive HTML report** you can open in any browser.

> [!IMPORTANT]
> **`free` never deletes anything.** It only measures and advises. You run the reclaim commands yourself, after reviewing them — nothing is removed for you, and nothing ever leaves your machine.

---

## ✨ Why

Storage is expensive — especially on Apple hardware — so most people are always a little short. The gigabytes are usually hiding in the same places: build caches, `node_modules`, old iOS backups, a Docker disk image, a Downloads folder nobody has opened in a year. `free` finds them in one fast, read-only scan and tells you, in plain language, what you can safely reclaim.

## 🎛️ What you get

| | |
|---|---|
| 🗺️ **Interactive treemap** | See at a glance where your home folder space actually went. |
| 🚦 **Safety tiers** | Every hotspot is tagged **Safe**, **Review**, or **Careful** — no guessing. |
| 📋 **Curated catalog** | ~30 known macOS space hogs: Xcode, Homebrew, npm, Docker, caches, Trash, backups… |
| 🔍 **Drill into any hotspot** | Expand a card to see the biggest folders *inside* it, exact paths, and a targeted tip. |
| 🐳 **Docker breakdown** | If Docker is running, a dedicated section shows images / containers / volumes by size and what's reclaimable. |
| 📦 **`node_modules` finder** | Totals every `node_modules` on disk, biggest first. |
| 🧾 **Copy-paste commands** | The exact reclaim command for each safe item — one click to copy. |
| 📄 **One self-contained file** | The report is a single HTML file: no server, no assets, no tracking. |

---

## 📦 Install

This repo is its own Claude Code marketplace. From inside Claude Code:

```text
/plugin marketplace add aviv4339/free
/plugin install free@free
```

<details>
<summary>Prefer a plain skill (no plugin)?</summary>

```bash
git clone https://github.com/aviv4339/free.git
cp -r free/skills/free-space ~/.claude/skills/
```

</details>

---

## 🚀 Use

Just ask Claude, in any project:

```text
free up some space on my mac
what's taking up all my storage?
my disk is almost full — what can I delete?
```

Claude runs the scan, reads the results, gives you a prioritized cleanup plan, and points you at the report:

```text
You ▸ free up some space

Claude ▸ You're at 78% (780 GB used, 220 GB free). About 34 GB is safe to
         reclaim right now:
           • User caches ........ 18 GB
           • npm cache .......... 9 GB    →  npm cache clean --force
           • Homebrew cache ..... 3 GB    →  brew cleanup -s
           • Xcode DerivedData .. 4 GB    →  rm -rf ~/Library/Developer/Xcode/DerivedData/*
         96 node_modules folders add up to another 11 GB.
         Full report → ./storage-report/hot-spots-2026-05-12-10-30.html
```

Open the report any time:

```bash
open ./storage-report/hot-spots-*.html
```

### Or run the scanner directly

```bash
python3 skills/free-space/scan.py            # full scan → ./storage-report/
python3 skills/free-space/scan.py --fast     # quick: skip the home-folder walk
python3 skills/free-space/scan.py --json     # print the full report as JSON
```

---

## 🗂️ What it finds

Every hotspot is sorted into one of three tiers, so you always know the risk:

| Tier | Meaning | Examples |
|:--|:--|:--|
| 🟢 **Safe** | Regenerable junk — clear it freely | Xcode DerivedData · Homebrew / npm / pip / Gradle / Go caches · `~/Library/Caches` · logs · Trash |
| 🟡 **Review** | Your call — probably fine, look first | Downloads · iOS device backups · Docker data · Go / Cargo / Maven modules · big media |
| 🔴 **Careful** | Live app data — don't `rm` it | Photos library · Mail · `Application Support` · app Containers (prune from inside the app) |

Plus a full **`node_modules`** sweep — safe to delete in any project you're not actively building; `npm install` rebuilds them.

## 📊 Inside the report

The generated HTML page is fully interactive and works offline:

- **Disk gauge** — real capacity, used, and free (correct on APFS, where `df`'s own "used" column understates a full disk).
- **Treemap** — a proportional map of your home folder; each cell shows its path and share of home, and hovering reveals the full location, size, and percentage.
- **Docker section** — when Docker is running: a `docker system df` summary plus the biggest images and containers, and how much is reclaimable.
- **Hotspot cards** — size, safety badge, a plain-English explanation, and a copy-to-clipboard reclaim command. Filter by tier, and **expand any card** to drill into the biggest folders *inside* that hotspot, with exact paths and a targeted cleanup tip.
- **node_modules table** — every project, biggest first.
- **Light & dark** — follows your system theme, with a manual toggle.

---

## ⚙️ Options

| Flag | Default | Description |
|:--|:--|:--|
| `--out-dir DIR` | `./storage-report` | Where to write the HTML report. |
| `--fast` | off | Skip the full home-folder walk (catalog only — much faster). |
| `--depth N` | `6` | Depth of the `du` walk that feeds the treemap. |
| `--max-seconds N` | `300` | Timeout for the home-folder walk (falls back to fast mode). |
| `--home PATH` | `~` | Scan a different folder. |
| `--json` | off | Print the full report as JSON to stdout. |
| `--selftest` | — | Run the built-in checks and exit. |

## 🧠 How it works

No dependencies, no network. A single Python script shells out to the native `du` and `df` (fast and battle-tested), sizes a curated catalog of known hotspots in one pass, groups everything by safety, and renders a self-contained HTML report. Claude then reads a compact JSON summary and turns it into advice.

```
free/
├── .claude-plugin/
│   ├── marketplace.json   # makes this repo installable in one command
│   └── plugin.json
└── skills/free-space/
    ├── SKILL.md           # tells Claude when to scan and how to advise
    └── scan.py            # the engine — stdlib only, read-only, zero deps
```

## ✅ Requirements

- **macOS** (other platforms may come later).
- **Python 3** — already present on any Mac with the Xcode Command Line Tools.

## ⚠️ Caveats

- Sizes come from `du`. On APFS, cloned files and local snapshots are counted per copy, so the space you actually free back can differ from what's shown.
- If folders like **Downloads** or **Desktop** look under-counted, grant your terminal **Full Disk Access** in *System Settings → Privacy & Security*.

## 🔒 Privacy

Everything runs locally. The scan reads file **sizes**, never contents; the report is a plain file on your disk; nothing is uploaded anywhere.

## 📄 License

[MIT](./LICENSE) © [avive](https://github.com/aviv4339)
