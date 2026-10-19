# Backend capabilities and proof limits

Checked 2026-10-19 against the paired cloud client, project source and private
firmware inspection. A client command existing is not proof of a working agent
workflow. No folders or notebook content were changed during this capability check.

| Operation | Current state |
| --- | --- |
| Deliver PDF / DRM-free EPUB to an existing library folder | Implemented through document_delivery.py; cloud metadata, original-byte tablet sync and opening both formats verified. |
| List library, folders and document metadata | Paired cloud read verified. Installed client exposes ls/find; filename/tag search is not handwriting search. |
| Download original documents / native notebook archives | Installed client supports get, including stable document IDs. Bulk backup/read-and-interpret automation is not integrated. |
| Create folders; move / rename files or folders | Installed client supports mkdir/mv; account write behavior and an idempotent agent wrapper have not been qualified. The delivery helper intentionally does not perform these operations. |
| Read a selected handwritten crop | Native image capture verified; the image can be inspected by a capable agent with authorized access. This does not establish an operational remote relay or automatic OCR. |
| Read a whole handwritten page | Native full-page capture implemented and installed; rendered pixels and long-page coverage await tablet acceptance. |
| Search all handwritten content | Not provided by the current bridge. Needs qualified export/rendering, OCR and an index with notebook/page provenance. Cloud listing is not OCR. |
| Create a native writable notebook | Stock firmware has LibraryController.createDocument and native template/orientation setup. No server-to-device command adapter or live creation test is implemented. An uploaded PDF is a document that can be annotated, not a native notebook. |
| Add pages to a native notebook | Stock DocumentController.addPageWithTemplateAndPageSize exists. No qualified agent command or live append test is implemented. |
| Insert typed text / images into existing notebook content | Stock scene/clipboard operations exist. A device command adapter must validate notebook/page identity, save state and read back the result. Not implemented or tested for agent writes. |
| Generate briefs, worksheets or reference packs as new documents | Can generate a PDF/EPUB and use the proven delivery helper. Existing handwritten pages are not modified. |
| Store raw handwriting images in a vault | Obsidian supports images embedded in Markdown. OCR is not needed for storage. The current guarded writer accepts Markdown only, so attachment intake still needs implementation. |
| Organize handwritten sends in a vault | Capture queue exists; scheduled image intake and the qualified writer remain disconnected. Queued is not stored or organized. |

## How an agent reaches it

An agent with an existing authorized execution connection to the paired server can
use the same delivery helper; no native Muse SDK is required for that operation.
Keep cloud credentials on the server. The private sending skill records the local
paths and retry contract; this public capability map contains no account details.

The cloud-library path does not require the server and tablet on the same Wi-Fi.
The tablet must connect and sync; this is not a wake mechanism. A field test from
a different Wi-Fi is still pending. Device-side SSH currently provides development
access on the reachable local network, not a verified public/away-from-home
command channel. Remote native writes need the authenticated relay path connected
and a running device; don't expose SSH publicly to substitute for that path.

## Native writes must use the notebook application

Prefer stock library/document/scene commands over writing live notebook files.
Bind a request to the exact notebook/page and expected source state, deduplicate
retries, preserve the user's draft and editing position, and verify the saved
result. Do not replace a document to simulate append. Cloud upload replacement
flags are not a qualified API for editing native handwriting.

Examples enabled by the proven document route: send a reading pack, a project
brief, or a worksheet to an existing folder. Native operations that still need
implementation could create a project notebook, append a meeting-summary page,
or insert agent-generated material into a chosen page.

## Sources

Cloud client inspected: ddvk/rmapi v0.0.35; source revision
[aa60dac8](https://github.com/ddvk/rmapi/tree/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373).
See its [commands](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/README.md),
[download](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/shell/get.go),
[folder creation](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/shell/mkdir.go),
and [move](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/shell/mv.go)
implementations. The client is third-party, not an official cloud API.
