# Send a document to the normal library

`document_delivery.py` sends an explicitly selected PDF or DRM-free EPUB into an
existing folder in the normal reMarkable cloud library. It is separate from the
agent bridge inbox. The server and tablet need internet connectivity, not the
same home network; the tablet receives the document through normal cloud sync
when connected. It does not wake the tablet or prove that a sleeping device has
received the file.

This path requires an installed, paired, compatible `rmapi` client and a tablet
paired with the same cloud account. No client or account credentials are bundled.
The implementation was checked against the maintained
[ddvk/rmapi fork](https://github.com/ddvk/rmapi), commit
`aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373` (2026-08-23).
The original upstream is archived. Cloud protocol/schema changes can break
third-party clients independently of tablet firmware. Compatibility with a
particular account or firmware must be checked using the steps below. One
3.29.0.153 tablet has passed synthetic PDF and EPUB uploads, cloud metadata
read-back, and byte-for-byte physical-tablet file verification. The user also
confirmed both files open normally. A different-Wi-Fi field test is still pending. This is not an official reMarkable API integration.

## Prepare and verify

1. Review and install a compatible client from its upstream release/build process.
   This project does not install packages or download executables automatically.
2. Pair the tablet with its cloud account and pair `rmapi` interactively following
   its upstream instructions. Keep tokens in private client configuration outside
   Git; do not pass credentials in agent messages or command arguments.
3. Confirm the exact existing destination in the native library or companion app.
   Read its parent with `rmapi -ni -json ls /`, then inspect each level, for example
   `rmapi -ni -json ls /Reading`. Do not publish the output: it contains private
   library titles and IDs. Create the desired folder in the normal library first.
4. Make one disposable PDF with no personal information, run the delivery command,
   then confirm on the tablet that it appears in the chosen folder and opens.
   An offline/mock test cannot establish this final sync result.

## Explicit delivery

Run from this project directory, with a private state directory:

```sh
python3 document_delivery.py /private/Brief.pdf \
  --folder '/Reading' \
  --request-id 'reading_brief_000001' \
  --state-dir /private/document-delivery-state
```

Use `--rmapi /absolute/path/rmapi` when the client is outside PATH. The configured
client owns authentication. Calls use noninteractive mode, bounded timeouts and
argument arrays without a shell. Trace logging is disabled, and raw client error
output is not printed. The state directory must be user-owned with mode 700 and
stores private staged documents and receipts; keep it outside all repositories.
Receipt updates use exclusive temporary files and atomic replacement. Receipt
and lock entries must be owned regular files, with symlinks and hardlinks refused.

The adapter validates the file type/signature and a 100 MiB limit, verifies every
folder component against a unique cloud directory and its parent ID, and refuses
an existing document name. It never creates folders, uses replacement flags,
deletes cloud documents or directly edits tablet notebook storage.

Each request ID binds one file digest, filename and destination. After validation,
a durable uncertain receipt is written before the one upload attempt. The
adapter checks the folder identity again and reads back the uploaded document's
name and ID. `cloud_visible` means that cloud metadata read-back succeeded;
`device_sync_verified` stays false until a human checks the tablet. This check
does not download and compare remote document bytes. Concurrent library changes
between commands may yield uncertainty; avoid reorganizing the target folder
during upload.

Reusing the same request ID returns its existing receipt without another upload.
Changing its content or destination is refused. A timeout or failed read-back
remains `dispatch_uncertain` and exits 2. Inspect the cloud library and reconcile
that receipt before any new request; do not blindly retry with a different ID.
There is intentionally no automatic retry, overwrite, cleanup or scheduling.
A crash before the upload checkpoint may leave private staged data requiring
manual inspection; preserve it rather than deleting state to force a retry.

## Verification and references

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_document_delivery
```

Tests use a fake cloud client and synthetic documents. They cover folder ambiguity,
name collisions, timeout/no-replay, changed-request refusal, private state access,
input checks and noninteractive command construction. They do not establish cloud
authentication, client installation, actual upload or on-tablet delivery.

Official documentation explains
[PDF/EPUB imports and sync](https://support.remarkable.com/articles/Knowledge/importing-and-exporting-files)
and [tablet cloud pairing](https://support.remarkable.com/s/article/Pair-your-reMarkable-with-the-cloud).
The command contract comes from upstream
[main.go](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/main.go),
[put.go](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/shell/put.go)
and [ls.go](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/shell/ls.go).
