# Paper Agent Bridge

Experimental reMarkable notebook-to-agent submissions with a Python relay,
persistent receipts, a Qt Quick panel and a firmware-specific native overlay.
This is an independent community project, not a supported reMarkable product.

Send a page or lassoed section to an agent, optionally with typed or handwritten
comments, or send it directly to vault intake. The UI uses native Share rows for
Send to agent and Send to vault, plus Agent submissions below Share in the parent
menu. There is no floating button or live chat. Queued contains unsent work;
History contains relay-accepted submissions and their actual processing status.
Agent responses belong in their established normal channels.

The panel uses native navigation, tabs, dropdowns, typography and confirmation
controls. Text fields use the native keyboard; tapping outside dismisses it.
Saved drafts and legacy queued messages remain preserved. Native row geometry and
keyboard behavior still require physical acceptance of the current build.

## Relay

Python 3 uses only its standard library. Supply distinct random device and trusted
receipt credentials (32–128 URL-safe characters) as `REMARKABLE_TOKEN` and
`REMARKABLE_RELAY_TOKEN`. Keep configuration owner-only and outside repositories.

```sh
python3 bridge.py --state-dir /private/bridge-state --commands /private/commands.json
```

It binds `127.0.0.1:8898`. Configure your own authenticated HTTPS ingress; no tunnel,
DNS, account or credential is bundled. Never expose tablet SSH publicly. Fixed
command arrays configure only adapters you operate and have qualified, for example:

```json
{"muse": ["python3", "/absolute/path/paper-agent-bridge/muse_handler.py"]}
```

`muse_handler.py` hands compact source references to an existing local gadget
socket. `agent_handler.py` supervises bounded Hermes/read-only Codex processing,
preserves private results and never posts new answers to the tablet. Hermes uses
authenticated loopback configuration `hermes/{url,token,routing}`; inherited routing
may use cloud providers and is not a local-only vault route. Codex configuration
is `codex/{bin,workspace}`. A normal response channel must already be configured
and authorized in the receiving agent; the bridge does not invent a destination.

The shell client uses private `url`/`token` files via `REMARKABLE_CONFIG_DIR`:

```sh
sh send.sh capture /private/capture.json
sh send.sh inbox 0
```

Captures use `POST /v1/captures`; status reconciliation uses `GET /v1/inbox`,
`GET /v1/events`, `GET /v1/threads/<request_id>` and immutable source retrieval.
Legacy conversation/reply wire routes remain for saved queue/history compatibility
only; the UI no longer creates chats or replies. Trusted adapters can post real
stage receipts using their separate credential. Never expose that credential to
a model. IDs are 16–80 safe ASCII characters; identical retries return the stored
receipt and changed payloads under an existing ID fail. Uncertain dispatches are
not automatically repeated. Acceptance means preserved/handed off, not processed.

## Vault and library tools

[Image-first vault intake](RAW-VAULT.md) stores unchanged attachments plus pending
Markdown notes before recognition. Later local transcription and organization
proposals preserve source references and uncertainty; final application stays
subject to the vault's reviewed writer policy. The trusted storage hook requires
your own qualified sanctioned writer; the general agent adapter refuses Obsidian
dispatch because deployed Hermes local-only execution is not enforced.

[Document delivery](DOCUMENT-DELIVERY.md) sends PDF/DRM-free EPUB into the normal
cloud library through your paired compatible client. One firmware 3.29.0.153
device passed cloud metadata, byte-for-byte tablet sync and opening both formats.
A different-Wi-Fi field test remains pending. This route does not wake a tablet.

[Notebook control](NOTEBOOK-CONTROL.md) provides a journaled native command
adapter, guarded SSH CLI and authenticated outbound HTTPS command relay. Native
creation, folders, rename/move, page append/templates, page copy/reorder and typed
text have saved-state device proof on 3.29.0.153. Read the operation map for exact
arguments, backup requirements and remaining acceptance limits. The tablet uses
outbound connections; no public SSH is needed. An accepted queue request is not a
completed edit. The paired-cloud helper remains available for library inspection
and original archive downloads.

## Build and recovery

Host backend tests need a C++17 compiler and libcurl development files. The native
extension also needs CMake and compatible Qt 6 Core/Gui/Qml/Quick tools. Use your
own firmware-compatible cross-toolchain and resource compiler; set `CXX`, compiler/
linker flags, `RCC` and `CMAKE_TOOLCHAIN_FILE`. No proprietary SDK is distributed.

```sh
sh device/build.sh
# With your compatible cross-build environment configured:
sh device/build.sh --package
```

Keep your own firmware outside Git. `device/extract_resources.py --help` describes
private extraction (host zstd required); `device/build_native_overlay.py --help`
builds guarded resource overrides. Unexpected executable digests or ambiguous
anchors are refused; do not weaken guards to force unsupported firmware.

Installation requires an existing compatible XOVI setup and a controlled test
window. Privately back up current drop-ins, module/override files and bridge state,
preserve SSH recovery, save drafts, stage separately and arm a timed rollback to
the stock app before activation. Device paths in this prototype are fixed and
require review for your layout. Never replace the stock executable or notebook
storage. No universal installer or recovery command is provided.

## Checks and remaining acceptance

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_bridge test_muse_adapter test_agent_handler test_document_delivery test_vault_intake test_library_control
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s device -p 'test_*.py'
node device/test_model.js
sh -n send.sh device/build.sh
```

Tests use synthetic data and mock agents. They do not prove live agent response
channels, firmware compatibility, visual spacing, handwriting accuracy or pen feel.
Selection capture and direct-to-vault queueing have device acceptance. Whole-page
rendering exists, but pixel accuracy and long-page coverage remain unqualified.
Whole-notebook sending, native remote editing, suspend/wake and away-network
end-to-end submissions are not qualified. Status polling cannot wake the tablet.
