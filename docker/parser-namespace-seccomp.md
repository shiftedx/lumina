# Parser namespace seccomp profile

`parser-namespace-seccomp.json` is a Lumina modification of the Apache-2.0
[Moby default profile](https://github.com/moby/profiles/blob/6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef/seccomp/default.json).
Its license is retained in `parser-namespace-seccomp.LICENSE`.

The upstream commit is `6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef`; the original
file SHA256 is `6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`.
All upstream settings, architecture mappings and syscall rules remain unchanged.
Lumina appends these three rules for the shipped Bubblewrap 0.12.0:

| Added allowance | Purpose |
| --- | --- |
| `clone`, argument 0 exactly `0x7c020011` | User, mount, PID, network, IPC and UTS namespaces, with SIGCHLD |
| `unshare`, argument 0 exactly `0x10000000` | Nested user namespace setup |
| `mount`, `umount2`, `pivot_root` | Construct the private filesystem |

The resulting file SHA256 is
`3847a4c89969db4700857bf595a902e9d08a7b3a1930f9ce022048d1e58274ab`.
This exact policy was qualified on native Linux arm64 and amd64. It preserves
Docker's masked/read-only paths, capability restrictions and no-new-privileges.
No `systempaths=unconfined`, `seccomp=unconfined`, privileged mode, added
`SYS_ADMIN` capability or Docker socket is required.

The outer policy applies to the whole application container. Before codec
imports, the parser's separate inner libseccomp filter denies namespace and
mount reconfiguration in addition to its existing socket, process, signal and
metadata restrictions. Bubblewrap presents a read-only root, empty `/proc`,
only `/dev/null`, trusted runtime/code and the selected input read-only; only the
private photo output directory is writable. There is no general writable `/tmp`.
The PID namespace reaper and parent-death handling remove decoder descendants
when the supervisor is cancelled or times out.

Use the optional `docker-compose.parser-namespace.yml` after the existing Compose
files. Keep the JSON file at a stable location outside application mounts and
protected from application writes. Docker reads it when creating the container;
do not mount it into Lumina. Missing namespace support or a helper failure stops
the affected operation rather than weakening isolation.

To update, start from a verified upstream commit, preserve the complete default
policy and review only the appended bootstrap allowances. Keep the Bubblewrap
package pin in both Dockerfiles consistent with the tested invocation. Re-run
the packaging contract and actual parser contracts with
`LUMINA_PARSER_SANDBOX=namespace` under the proposed profile on native arm64 and
amd64: JPEG/TIFF/HEIF derivatives, PDF and solid CBR first/late pages, inaccessible
unrelated files, read-only input/root, namespace/socket/process denials, working
codec threads, and timeout/parent-death descendant cleanup. Update the recorded
digests only after those checks pass. Do not replace a failed qualification with
an unconfined profile.
