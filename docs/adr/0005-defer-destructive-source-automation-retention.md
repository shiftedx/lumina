# ADR 0005: Defer destructive Source automation retention

- Status: Accepted
- Date: 2026-07-17

## Context

Source automation retention settings previously named keep-last, age-based, and watched-based deletion modes, but Lumina did not execute them. An Automation decision does not durably identify the Download job it queued, and an ordinary automation-created Download job does not durably identify the Library item produced from it. Inferring that relationship from provider identifiers would confuse manual acquisitions, media variants, multiple automations, and shared Library items. Lumina also has no settled automatic-deletion contract for timestamps, Household collections, concurrent playback or acquisition, events, or recovery.

## Decision

Lumina does not expose or execute destructive Source automation retention. Source automations may inspect sources and queue acquisitions, but removing local media remains an explicit Library action by the Library-item owner. Public automation and member-default contracts omit retention, and requests that still send a retention field are rejected instead of being silently accepted.

Destructive retention may be reconsidered only after Lumina has a durable Automation-decision-to-Download-job-to-Library-item provenance chain and separately decides ownership, policy conflicts, time anchors, missing-file idempotency, collection interaction, events, and recoverability.

## Consequences

- No unattended Source automation can remove local media.
- Source automations store no retention setting.
- Future retention work must introduce an explicit lifecycle design rather than infer provenance from provider metadata.
