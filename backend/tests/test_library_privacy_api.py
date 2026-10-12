from app.models import LibraryNote, LibraryItem, LibraryTag, User


def test_vault_owner_api_sees_shared_curation_without_private_or_mutation_bypass(db_factory, api_client) -> None:
    member = User(id="privacy-member", username="member", display_name="Member", role="viewer", is_active=True)
    vault_owner = User(id="privacy-admin", username="vault-owner", display_name="Vault Owner", role="admin", is_active=True)
    private_item = LibraryItem(
        id="api-private-item",
        user_id=member.id,
        visibility="private",
        title="saffronprivateapi",
        file_path="/private/member.mp4",
        metadata_json={},
        status="available",
    )
    shared_item = LibraryItem(
        id="api-shared-item",
        user_id=member.id,
        visibility="shared",
        title="Household film",
        file_path="/private/shared.mp4",
        metadata_json={},
        status="available",
    )
    with db_factory.begin() as session:
        session.add_all(
            [
                member,
                vault_owner,
                private_item,
                shared_item,
                LibraryNote(id="api-private-comment", item_id=shared_item.id, user_id=member.id, visibility="private", body="cobaltprivateapi"),
                LibraryNote(id="api-shared-comment", item_id=shared_item.id, user_id=member.id, visibility="household", body="Household note"),
                LibraryTag(id="api-owner-tag", item_id=shared_item.id, user_id=member.id, tag="owner-only"),
            ]
        )

    current = {"user": vault_owner}
    client = api_client(user=lambda: current["user"], base_url="http://localhost")
    listed = client.get("/api/library")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [shared_item.id]
    assert "file_path" not in listed.json()["items"][0]

    assert client.get(f"/api/library/{private_item.id}").status_code == 404
    assert client.put(f"/api/library/{private_item.id}/visibility", json={"visibility": "shared"}).status_code == 404
    assert client.put(f"/api/library/{shared_item.id}/visibility", json={"visibility": "private"}).status_code == 403

    comments = client.get(f"/api/library/{shared_item.id}/notes")
    assert comments.status_code == 200
    assert [comment["id"] for comment in comments.json()] == ["api-shared-comment"]
    assert client.put("/api/library/notes/api-private-comment", json={"body": "No", "visibility": "household"}).status_code == 404
    assert client.put("/api/library/notes/api-shared-comment", json={"body": "No", "visibility": "household"}).status_code == 403
    assert [note["can_delete"] for note in comments.json()] == [True]  # admin moderates household notes
    assert client.post(f"/api/library/{shared_item.id}/notes", json={"body": "   ", "visibility": "private"}).status_code == 400
    assert client.get(f"/api/library/{shared_item.id}/tags").json() == []
    assert client.delete(f"/api/library/{shared_item.id}/tags/owner-only").status_code == 404

    assert client.get("/api/library", params={"search": "saffronprivateapi"}).json()["items"] == []
    semantic = client.get("/api/search", params={"q": "cobaltprivateapi"})
    assert semantic.status_code == 200
    assert semantic.json()["items"] == []

    current["user"] = member
    updated = client.put(
        "/api/library/notes/api-private-comment",
        json={"body": "Updated private note", "visibility": "household"},
    )
    assert updated.status_code == 200
    assert updated.json()["body"] == "Updated private note"
    assert updated.json()["visibility"] == "household"
    assert updated.json()["is_owner"] is True
    assert client.delete("/api/library/notes/api-private-comment").status_code == 204
    assert client.put("/api/library/notes/missing", json={"body": "No", "visibility": "private"}).status_code == 404
    deleted_file = client.post(f"/api/library/{shared_item.id}/delete-file")
    # Authorized (not 403), but a raw stored path outside every storage root is never deleted.
    assert deleted_file.status_code == 409
