"""Standing jobs (v0.5 phase 06): proactive, scheduled turns.

The first time Ubongo speaks unprompted. A `StandingJobsLoop` (a fourth
`DaemonLoop`) runs config-defined jobs on their schedule through the one
orchestration seam (`master.handle`) and delivers via `notification_queue`.
Additive over the daemon lifecycle, the queue (ADR-0002), the resumable approval
seam (ADR-0018), and the grant registry (ADR-0019). See ADR-0021.
"""
