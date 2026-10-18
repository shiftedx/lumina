# Lumina household media

Lumina presents a household's registered media alongside acquisition and playback services.

## Language

**Observed inventory**:
Media known to a connected manager, including items the household added outside Lumina. An observation does not establish that a member can play a file.
_Avoid_: Imported library, playable media

**Registered media**:
Files the normal library import has linked to a title and storage artifact. A member's access also depends on current visibility and storage availability.
_Avoid_: Download complete

**Music release group**:
The album-level identity shared by its editions, and the identity used for a Lidarr album request.
_Avoid_: Release ID, edition ID

**Music edition**:
One particular release of an album, identified separately from its release group.
_Avoid_: Release group

**Listening queue entry**:
An ordered listening intention that names either a registered track or a music acquisition request. An acquisition entry becomes playable through visible registered tracks.
_Avoid_: Placeholder track

**Listening play**:
One actual listening attempt whose elapsed playback can become eligible for history export. Seeking changes playback position without adding elapsed listening.
_Avoid_: Playback progress, completion flag
