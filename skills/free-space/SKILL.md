---
name: free-space
description: >
  Find what is eating a Mac's storage and explain what is safe to delete. Scans
  known macOS space hogs (Xcode DerivedData, caches, node_modules, Trash,
  Downloads, iOS backups, Docker, …), sizes them, and writes a beautiful
  interactive HTML report to ./storage-report/. Never deletes anything — it
  measures and advises. Use when the user says "free up space", "free space",
  "clean up my Mac", "what's taking up storage", "disk is full", "storage
  hotspots", "why is my disk full", or "reclaim disk space".
---

# free — map macOS storage hotspots

A read-only storage analyzer for macOS. It **never deletes anything.** It runs a
bundled Python scanner that measures known space hogs, then you interpret the
results and advise the user.

## Steps

1. **Run the scanner** (stdlib Python 3, already on the user's Mac):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/free-space/scan.py" --out-dir "./storage-report"
   ```

   - It prints a compact JSON summary to stdout and writes the full interactive
     report to `./storage-report/hot-spots-<timestamp>.html`.
   - A full-disk scan can take a minute or two. If the user is impatient or the
     disk is huge, add `--fast` (skips the home-folder walk, catalog only).
   - If `${CLAUDE_PLUGIN_ROOT}` is empty (skill run standalone), use the actual
     path to `scan.py` next to this file.

2. **Read the JSON summary** (not the HTML — it is large). It gives the volume
   usage, `reclaimable_gb` (sum of the *safe* category), a `summary_gb`
   breakdown by safety, `node_modules` totals, and the top hotspots with their
   `reclaim` commands.

3. **Advise the user** in chat, briefly and concretely:
   - Lead with the headline: `X GB used, Y GB free, ~Z GB safe to reclaim now`.
   - List the top 3–5 wins with sizes and the exact reclaim command.
   - Respect the safety tiers: **safe** = regenerable junk (caches, build
     output, Trash); **review** = user's call (Downloads, backups, big media);
     **caution** = live app data (Photos, Mail, Containers) — never suggest
     `rm` on these; point to the in-app option (e.g. iCloud Optimize Storage).
   - Point them at the report and offer to open it: `open <report-path>`.

## Hard rules

- **Do not delete or move anything.** Present reclaim commands for the user to
  run themselves. If they explicitly ask you to run one, confirm the exact
  command and target first, and never touch **caution**-tier paths.
- Sizes come from `du`; on APFS, clones and snapshots mean actual freed space
  can differ. Say so if it matters.
- If the scan reports errors (permission denied, timeout), relay them — some
  folders need the terminal to have Full Disk Access in System Settings →
  Privacy & Security.

## Options

`--fast` (catalog only, skip home walk) · `--depth N` (home-walk depth, default
6) · `--max-seconds N` (home-walk timeout) · `--json` (full JSON to stdout) ·
`--home PATH` (scan a different folder) · `--selftest`.
