# ADR 0003: Separate household collections from source playlist provenance

- Status: Accepted
- Date: 2026-07-16

## Context

Provider playlists describe how media appeared at inspection time, while household members need stable organization that survives provider changes and can combine items from unrelated sources. Treating both as one concept would make provider metadata an accidental access and organization boundary.

## Decision

Lumina records Source playlist provenance as descriptive acquisition metadata and models Household collections separately. A Household collection has one owner, explicit private or shared visibility, and independent Library-item membership. Only its owner may mutate it. Collection membership is not an access grant: a viewer sees only member items already visible to that viewer. Acquisition batches retain the source provenance used for a request without creating or changing a Household collection.

## Consequences

- Provider playlist changes do not rename, delete, or reorder Household collections.
- Collections can combine Library items from multiple providers and sources.
- Shared collection views may omit private member items for non-owners.
- Future provider synchronization and collection automation require explicit policy rather than implicit coupling.

## Alternatives considered

- **Use provider playlists as collections.** Rejected because it couples household organization and visibility to external mutable state.
- **Make collection membership grant item access.** Rejected because adding an item would become a surprising authorization change.
