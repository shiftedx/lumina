# ADR 0017: Watch external roots by polling directory mtimes, and import only the changed folders

- Status: Accepted
- Date: 2026-10-02

## Context

The household's media lives on network shares mounted into an LXC container. Kernel change notification does not work there:

- inotify "does not catch remote events that occur on network filesystems" ([inotify(7)](https://man7.org/linux/man-pages/man7/inotify.7.html)).
- Jellyfin's own documentation says NFS and rclone do not support inotify, and that large libraries exhaust `max_user_watches`, which Docker users can only raise on the host ([Jellyfin troubleshooting](https://jellyfin.org/docs/general/administration/troubleshooting/)).
- Jellyfin disposes a watcher that fails ([`LibraryMonitor.cs`](https://github.com/jellyfin/jellyfin/blob/master/Emby.Server.Implementations/IO/LibraryMonitor.cs)), so on a share it silently does nothing.

A large TV root can hold 38,725 files, so a full import on every change is too slow.

## Decision

- Lumina watches an external root by **polling directory modification times**. Every known directory is `stat`ed round-robin, within a per-tick budget. A directory's last known-good mtime is persisted in `library_watch_dirs`.
- A changed directory is listed. Its media files must be **size-stable** (three equal `(size, mtime_ns)` observations 30 s apart) before anything is imported.
- Changes become one **scoped import run**: `import_runs.scope` lists the changed folders as shallow or deep entries, and the run goes through the same `LibraryImportService`. A scoped run marks a file missing only when `lstat` confirms it gone and the root is online. The existing mass-missing guard applies with the root's file count, and automation never confirms a held run.
- Scheduled full scans per root (off, 15 min, hourly, 6 h or nightly) are the safety net for what mtimes cannot show: in-place NFO and art edits, and union filesystems.
- A dedicated poller thread and driver threads do the work, not `reconcile_loop`. One automatic run is active at a time per process. Since #160 each root drives its automatic runs on its own thread, and a run stuck on an unresponsive share stops counting toward that limit: a thread blocked in an NFS syscall cannot be cancelled, so it is isolated and reported, and the other roots carry on.

## Consequences

- Detection latency is the poll interval plus at least 60 s of quiet (≈ 7 min by default) instead of near-instant. In exchange it works on NFS, SMB and local disks alike, needs no host sysctl, and its health shows up in Diagnostics.
- A hung NFS `stat` can stall the poller thread, though never the event loop or playback. Root probes run in their own threads with a timeout. A per-root poller is the upgrade path if this bites.
- Import runs gain `trigger` and `scope`. Rolling back to 2.0.1 deletes scoped runs before re-stamping, because 2.0.1 would treat them as full runs.
