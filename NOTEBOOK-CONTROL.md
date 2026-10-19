# Notebook control

The native adapter runs inside the notebook app on firmware 3.29.0.153. It uses
native controllers; it never edits live notebook files. The SDK is a Qt toolchain,
not an officially supported notebook editing API. Firmware updates require a new
qualification pass. Keep the tablet unlocked and avoid simultaneous manual edits.

## Execute a command

Resolve a name through `list_folder`, inspect the exact stable ID, then use the
returned snapshot as `expected`. Duplicate names require disambiguation. Root's
ID is the empty string. A native notebook, imported PDF/EPUB, folder and Quick
sheets are distinct targets; never substitute a new PDF for a native append.

Save a private JSON request, with one retained ID for each intended action:

```json
{"request_id":"notebook_request_00001","operation":"inspect","arguments":{"id":""}}
```

On the reachable maintenance network:

```sh
python3 native_control.py /private/request.json --ssh-host remarkable \
  --state-dir /private/native-receipts
```

For images add `--image /private/picture.png`. For permanent deletion, moving
pages or creating a notebook by moving pages, add `--confirm-delete` only within
the user's exact authorized scope. The helper obtains an unchanged, hashed native
backup before dispatch. A backup failure prevents the operation. It currently
backs up individual documents, not recursive folder trees.

`verified` means the native result was read back. Mutations also require saved
metadata/content or page bytes; open-page only checks navigation. `needs_review`
is not success and must not be retried with a new ID. Preserve the request and
receipt. `--reconcile` queries the tablet's durable journal without replay;
`--retry-undispatched` permits the original request only after reconciliation
proves it never reached that journal. Repeating a completed ID returns its receipt;
changing its input is rejected. A crash during a mutation remains uncertain.

## Operation arguments

Every command uses `{request_id,operation,arguments}`. Reject unknown arguments.
`expected` is the complete fresh inspect snapshot; `target_expected` is the same
for a destination document. Page identities are stable UUIDs, never page numbers.

| Operation | Arguments / behavior |
| --- | --- |
| `inspect` | Optional `id`; returns identity, name, parent, Trash state, ordered page IDs/templates, and active page/text length when applicable. Root also reports active editor. |
| `list_folder` | Optional `id`; immediate children only. Walk relevant folders. |
| `list_templates` | No arguments; installed filenames and orientation. |
| `create_folder` | `parent_id,name`; collision refused. |
| `create_notebook` | `parent_id,name`; optional installed `template,landscape`. Host adds stable `document_id,page_id`. Creates a writable native notebook. |
| `quick_sheet` | No arguments; reuse empty last Quick sheets page or append. Never treats another notebook as Quick sheets. |
| `rename` | `id,expected,name`; no overwrite. |
| `move_item` | `id,expected,parent_id`; documents or folders, no cycles/merges. |
| `copy_item` | `id,expected,parent_id`; native document cloning, native-generated copy name; folder recursion unavailable. |
| `append_page` | `id,expected,template`; optional `index` (`-1` appends), stable `page_id` supplied by helper. |
| `set_template` | `id,expected,page_id,template`; installed template on native note page; preserves ink. |
| `copy_pages` | `id,expected,page_ids,target_id,target_expected`; optional `index`, defaults append. |
| `move_pages` | Same as copy; backup/confirmation required. Same-document move reorders. Index refers to the original order. Leave at least one source page for cross-document moves. |
| `create_from_pages` | `id,expected,page_ids,parent_id,name`; **moves** selected pages into a new notebook, requires backup/confirmation, leaves at least one source page. |
| `open_page` | `id,page_id`; navigates the existing editor. |
| `insert_text` | `id,page_id,expected,text,expected_text_length`; exact active writable native page, appends 1–16000 characters. Open it and inspect again first. |
| `insert_image` | `id,page_id,expected,x,y`; helper stages a PNG/JPEG and supplies `image_sha256,image_format`. Coordinates are explicit native page coordinates. Exact active writable page required. |
| `trash` / `restore` | `id,expected`; recoverable native library action. Restore uses stock behavior; inspect its resulting parent. |
| `delete_pages` | `id,expected,page_ids`; permanent, backup/confirmation required, target must be closed, cannot delete every page. |
| `delete_item` | `id,expected`; permanent, already in Trash, closed, document backup/confirmation required. No bulk deletion or empty Trash. |

Never fabricate `confirmed` or a backup digest. Those fields are supplied only
after an actual scoped backup. Operations absent from this table are unavailable;
there is no arbitrary shell command or general script execution endpoint.

## Away from home

`control_relay.py --config-dir /private/relay-config submit /private/request.json
--wait 30` submits through the authenticated relay. `status REQUEST_ID` reads its
state; `upload-image /private/image.png` stages an immutable image and returns its
digest/format for an insertion request. Keep credentials in private configuration,
not command arguments or request bodies. A queue acknowledgment only means queued.
Only a completed native `verified` receipt permits reporting a tablet change.

The tablet's `control-relay` process makes outbound HTTPS long polls, invokes the
same local adapter and durably retries receipts. No public SSH or tablet VPN is
needed. Production requires the existing tunnel's route and private tablet relay
configuration; this is not yet an end-to-end outside-home acceptance claim.
An offline, sleeping or locked tablet can leave commands pending. Destructive
remote actions still need a verified backup; the relay does not invent one.

## Other capabilities

`library_control.py` remains the separate paired-cloud list/inspect/download and
folder/move helper. Its `cloud_verified` status proves cloud metadata, not tablet
sync. Downloads are original `.rmdoc` backups, not rendered or recognized text.
The cloud client's `rm` bypasses native Trash and must not be used.

PDF and DRM-free EPUB delivery uses `document_delivery.py`; it creates imported
library documents and works through cloud sync. User-confirmed lasso/page captures
can be processed visually and stored in Obsidian; scheduled local transcription
and reviewed organization are documented in [RAW-VAULT.md](RAW-VAULT.md).
An existing native `<document-UUID>.thumbnails/<page-UUID>.png` may provide a
read-only cached preview through authorized maintenance SSH. Check source identity
and freshness; low resolution can leave writing unreadable. This does not require
a new on-device viewport or prove full-resolution rendering.
Whole-vault handwriting search, arbitrary modern notebook rendering, recursive
folder deletion and raw notebook-file rewriting are not provided.

## Qualification

Disposable on-tablet tests have verified native creation, folders, rename/move,
page append/templates, page copy/reorder and cross-document moves, typed text, native images, whole
notebook copies, creation from selected pages, Trash/restore and scoped permanent
deletion against saved state. Quick sheets remains implemented without a separate
field test. Remaining acceptance results and exact firmware/build/deployment
limits are maintained in the private device handoff. Host tests alone do not prove
screen appearance, handwriting quality, cloud synchronization or battery cost.
