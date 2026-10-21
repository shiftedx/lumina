import Foundation

struct QueuePresentationRow<Item>: Identifiable {
    let id: UUID
    let item: Item
    let duplicated: Bool
    let index: Int
}

enum QueuePresentation {
    @inline(__always) static func rows<Item>(items: [Item], occurrenceIDs: [UUID], itemID: (Item) -> String) -> [QueuePresentationRow<Item>] {
        precondition(items.count == occurrenceIDs.count)
        let counts = Dictionary(grouping: items, by: itemID).mapValues(\.count)
        return zip(occurrenceIDs, items).enumerated().map {
            QueuePresentationRow(id: $0.element.0, item: $0.element.1,
                                 duplicated: (counts[itemID($0.element.1)] ?? 0) > 1, index: $0.offset)
        }
    }
}
