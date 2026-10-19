# Notebook control: operations and limits

An agent instruction must distinguish a native notebook, an imported document,
a library folder and the Quick sheets notebook. Uploading a PDF does not create
an editable native notebook, and replacing an uploaded file does not append to it.
The device SDK supplies a Qt toolchain; it is not an external notebook editing API.

## Callable server tools

`library_control.py` uses the installed paired cloud client. Paths must be exact,
unambiguous library paths. Inspect before changing an existing item and pass its
observed ID and version. A move to a new full path also renames the item.

```sh
python3 library_control.py --rmapi /absolute/path/rmapi list /
python3 library_control.py --rmapi /absolute/path/rmapi inspect '/Projects/Notes'
python3 library_control.py --rmapi /absolute/path/rmapi create-folder '/Projects/New' \
  --request-id folder_request_00001 --state-dir /private/library-operations
python3 library_control.py --rmapi /absolute/path/rmapi move '/Projects/Notes' '/Projects/New/Notes' \
  --expected-id OBSERVED_ID --expected-version 3 \
  --request-id move_request_000001 --state-dir /private/library-operations
python3 library_control.py --rmapi /absolute/path/rmapi download '/Projects/New/Notes' \
  --expected-id OBSERVED_UUID --output-dir /private/notebook-backups
```

Credentials stay in the paired client's private configuration. No shell is used
to invoke the client. Mutations bind the request ID to the exact operation, reject
collisions and identity changes, persist uncertainty before dispatch, and read
back the result. Exit 2 / `dispatch_uncertain` requires read-only reconciliation;
reusing an ID never repeats the operation. Preserve receipts. The client lacks an
atomic compare-and-swap API: concurrent changes from another client can still
produce uncertainty. Avoid editing the same item concurrently.

`cloud_verified` means library metadata was verified; it does not prove tablet
sync. Download saves an unchanged private `.rmdoc` backup with a digest and source
ID. This is not extracted text or a rendered notebook. No replacement, deletion,
recursive mutation, raw notebook-file editing or cloud archive restoration is
exposed. For new PDFs/EPUBs, use `document_delivery.py` and its separate receipt.

## Full capability map

| Requested action | Available route / exact boundary |
| --- | --- |
| List folders, find a named item, inspect ID/version | `library_control.py list` and `inspect`; walk only relevant folders, reject duplicate names. |
| Download an original / notebook backup | `library_control.py download`; ID-bound `.rmdoc`, private directory, no replacement. |
| Create a library folder | `library_control.py create-folder`; one new exact path. Host-tested; live account mutation acceptance remains required. |
| Rename / move a document, notebook or folder | `library_control.py move`; same source ID, new full path, no merge or overwrite. Host-tested; live account mutation acceptance remains required. |
| Add generated text, pictures, diagrams or worksheets as a new document | Generate a PDF or DRM-free EPUB, then use the proven document delivery helper. This creates an annotatable imported document. |
| Create a native notebook | Firmware uses `LibraryController.createDocument(parentId,name)`, followed by orientation, template and cover setup. No callable agent adapter exists yet. |
| Add to Quick sheets | Firmware uses `LibraryController.quickSheets()`; reuse its empty last page or append when that page contains content. It is a singleton, not an arbitrary filename. No agent adapter yet. |
| Add / insert a native page | `DocumentController.addPageWithTemplateAndPageSize`; index `-1` appends. Requires native adapter, exact notebook identity, supported paper size/template, saved-state read-back. |
| Duplicate / copy pages or create a notebook from selected pages | `copyPages` / `LibraryController.createDocumentFromExisting`; native adapter and ordered source-page identity required. |
| Reorder / move pages between notebooks | `DocumentController.movePages`; native adapter, exact source/destination/page order and verified backup required. |
| Add typed text to a native page | Stock text mode and `sceneController.pasteClipboardContent(text,SceneController.KeepStyle)` operate on the active writable page/cursor. No background agent insertion adapter exists. |
| Insert an arbitrary picture into existing notebook ink | No qualified method found. Internal clipboard item objects are not an image-upload API. A new PDF containing the image is available; inserting into the requested native page is not. |
| List / change page templates | `Templates.list()` and `DocumentController.setTemplateForPage`; native adapter must use an installed template filename and exact page, preserving ink and orientation. Not remotely exposed. |
| Duplicate a whole notebook/document | Stock entity selection `selectionCopy`; no qualified agent adapter. Do not upload a `.rmdoc` backup as a clone: it retains document UUIDs. |
| Move an item to Trash / restore it | Stock selection `selectionMoveToTrash` / `selectionRestoreTrashed`; native adapter needed. The cloud client's `rm` is not Trash. |
| Permanently delete a file/folder | Stock selection `selectionRemove`; exact scope, backup and explicit user intent required. Not exposed by the agent wrapper. Never use bulk `nuke` or empty-Trash actions. |
| Delete pages | Stock `DocumentController.deletePages` permanently deletes them; no page Trash. Not exposed; requires verified backup, exact stable page IDs, scope confirmation and native read-back. |
| Read a lassoed section / current page visually | Existing user-confirmed capture path; full-page pixel/long-page acceptance remains pending. Stored images can be read by the processing agent. |
| Transcribe / organize submitted handwriting in Obsidian | Raw-image storage plus scheduled local transcription and reviewed organization; see [RAW-VAULT.md](RAW-VAULT.md). This does not modify tablet notebooks. |
| Search all handwriting or render every modern native notebook remotely | Not qualified. Library title search and downloading `.rmdoc` are not OCR/search/rendering. |

## Agent execution rules

Resolve names to exact paths and stable IDs before writing. For an instruction
such as “add this to Meeting notes,” first determine whether that means append to
an existing native page, add a new page, or deliver a separate document. Use known
context; ask only if the target or requested scope is genuinely ambiguous. Never
silently substitute a new PDF for an append to a named native notebook.

Expose only qualified tools to agents. Native method discovery is evidence for
an implementation plan, not a successful command. Native writes require an
authenticated in-process adapter with operation allowlists, expected document/page
state, durable request IDs, backup/recovery for destructive changes and saved-state
read-back. Do not use shell edits of live `.rm`, `.content` or `.metadata` files.
Remote SSH currently works only on the reachable maintenance network. Cloud
library operations use internet sync; native commands outside home still need
the authenticated tablet relay connected and the tablet awake.

## Evidence

Inspected firmware 3.29.0.153: CreateNotebookWindow, CreateCollectionWindow,
RenameEntityWindow, LibraryActions, PagesActions, Pages, ContentPagesMoveHeader,
DocumentView, TemplateSelector, ActionHeader and Navigator QML. Vendor resources
are private and are not redistributed here. These interfaces can change by build.
Cloud behavior was checked against the pinned
[rmapi source](https://github.com/ddvk/rmapi/tree/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373),
especially its shell commands, sync15 API and archive reader. It is third-party
software, not an official supported cloud API. The wrapper's host tests do not
qualify real cloud mutations or device sync.
