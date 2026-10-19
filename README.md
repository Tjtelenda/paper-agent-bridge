# Paper Agent Bridge

Experimental reMarkable notebook-to-agent bridge with a Python relay, persistent
threads and receipts, a Qt Quick client, and a firmware-specific native overlay.
This is an independent community project, not a supported reMarkable product.

The relay accepts explicit page/crop submissions and source-free text conversations.
It preserves source images and request identities, separates device and agent-reply
credentials, and provides an inbox that can recover after restart. The tablet UI
includes compose, inbox, outbox, threaded replies and optional ink comments.
The overlay uses stock Share rows for Send to agent and Send to vault, with Agent
inbox directly below Share in the parent menu and no floating notebook shortcut.
Share preserves an existing draft, otherwise captures the active lasso or renders
the full current page. Whole-page rendering is implemented; pixel acceptance on
the test device remains pending.
The panel reuses native navigation, tabs, dropdown, typography and confirmation
controls; multiline text and chat history retain custom layouts.

Host tests do not establish compatibility with your tablet. Selection capture has
been exercised on one Paper Pro Move firmware build; UI refinements still require
manual device acceptance. Whole-notebook sending, reliable full-page capture,
handwriting recognition, source navigation, suspend/resume and end-to-end agent
round trips are not qualified. Inbox polling does not wake a sleeping tablet.
Obsidian dispatch deliberately fails closed pending a qualified local-only writer.

## Relay and adapters

Python 3 uses only its standard library. Start from a private configuration directory
with restrictive permissions. Create separate random device and relay credentials
(32–128 URL-safe characters), and supply them as `REMARKABLE_TOKEN` and
`REMARKABLE_RELAY_TOKEN` in the server environment. Never commit credentials or
actual notebook data. Run:

```sh
python3 bridge.py --state-dir /private/bridge-state --commands /private/commands.json
```

The server binds `127.0.0.1:8898`. Device access requires an independently configured
HTTPS ingress. No tunnel, DNS, accounts, credentials or hosting service is provided.
Keep the server authenticated and avoid exposing tablet SSH through an ingress.

The commands file maps destination names to fixed argument arrays, for example:

```json
{"muse": ["python3", "/absolute/path/paper-agent-bridge/muse_handler.py"]}
```

Only configure destinations whose adapter you operate and have qualified.
`muse_handler.py` implements a compact local socket protocol; it sends references
to immutable assets rather than embedding images. `agent_handler.py` is an optional
Linux user-service adapter for Hermes and read-only Codex. Its private configuration
has `relay/{url,token}`, `hermes/{url,token,routing}` and `codex/{bin,workspace}`.
Hermes uses authenticated loopback HTTP with `routing=inherited`; that route may
use a cloud provider and must not be assumed local. No agent credentials are included.
`relay_client.py` posts actual agent responses with the separate relay credential;
a hook acknowledgement alone never means an answer has arrived.

Configure the shell client's private `url` and `token` files with
`REMARKABLE_CONFIG_DIR`. Its URL is an HTTPS origin. Submit a private JSON file:

```sh
sh send.sh conversation /private/conversation.json
sh send.sh capture /private/capture.json
sh send.sh inbox 0
```

A minimal conversation file is:

```json
{
  "request_id": "chat_example_00001",
  "destination": "hermes",
  "content": {"mode": "raw_text", "text": "Help me think through an idea."}
}
```

Destinations for conversations are `muse`, `hermes`, and `codex`. Captures also
accept `obsidian`, subject to the fail-closed adapter boundary above. Capture
validation and complete synthetic examples live in `test_bridge.py`.

Device routes are `POST /v1/captures`, `POST /v1/conversations`, `POST /v1/replies`,
`GET /v1/inbox?after=N`, `GET /v1/events?after=N&wait=20`,
`GET /v1/threads/<request_id>` and `GET /v1/sources/<request_id>`.
Trusted adapters use `POST /v1/agent/replies` with the separate relay credential.
IDs are 16–80 ASCII letters, digits, underscores or hyphens. An identical retry
returns the stored receipt; changed content under an existing ID returns 409.
Uncertain dispatches are never automatically repeated. Reconcile them with the
receiving agent before retrying. A stored or organized receipt is an authenticated
adapter report, not independent verification of a note in a vault.

## Deliver PDFs and EPUBs to the library

See [document delivery](DOCUMENT-DELIVERY.md) for the explicit server/agent CLI.
It targets an existing folder in the normal cloud library, independently of the
agent inbox and home network. It requires your own paired compatible cloud client.
Synthetic PDF and EPUB uploads, cloud read-back and byte-for-byte tablet file
sync passed on one 3.29.0.153 device, and the user confirmed both files open
normally. A different-Wi-Fi test remains pending; qualify your own account/device
before relying on delivery.

## Build and device recovery

Host backend tests need a C++17 compiler and libcurl development headers/libraries.
The native extension additionally needs CMake and compatible Qt 6 Core, Gui, Qml
and Quick development files. Cross-building requires your own firmware-compatible
compiler and Qt resource compiler. Set `CXX`, compiler/linker flags, `RCC` and
`CMAKE_TOOLCHAIN_FILE` as appropriate; no proprietary toolchain is distributed.

```sh
sh device/build.sh
# With your compatible cross-build environment already configured:
sh device/build.sh --package
```

Copy your own tablet executable into a private directory outside every Git checkout.
Use `python3 device/extract_resources.py --help` to extract its resources into a new
private directory (requires host `zstd`), then
`python3 device/build_native_overlay.py --help` for the guarded overlay builder.
It reads a privately obtained firmware resource extraction and refuses an
unexpected executable digest or missing/ambiguous UI anchors. These guards are
compatibility checks; do not weaken them to force another firmware to work.
Vendor resources, firmware images and compiled packages are excluded here.

Installation is deliberately manual and requires an existing compatible XOVI setup.
Before changing the tablet, privately back up its current drop-ins and record their
paths, retain SSH recovery access, save any open draft, and verify a route back to
the stock notebook application. Stage compiled assets separately. Review the exact
overlay and destination paths before activating its runtime drop-in. Device paths
in this prototype are fixed for its layout and must be reviewed for yours.

To recover, disable only this extension's runtime drop-in using your existing
loader procedure, restore its previously backed-up configuration, then restart
the notebook application in a controlled maintenance session. Preserve notebooks,
bridge state, drafts and receipts. Do not replace the stock executable or delete
notebook storage. This repository provides neither an automatic installer nor a
universal recovery command; qualify recovery before activating on your device.

## Checks

Run from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_bridge test_muse_adapter test_agent_handler test_document_delivery
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s device -p 'test_*.py'
node device/test_model.js
sh -n send.sh device/build.sh
```

These checks use synthetic inputs and mock agent services; they do not prove live
agent delivery, device pen quality, handwriting recognition or firmware compatibility.

## Native menu shortcuts

The current revision uses native rows inside Share for Send to agent and Send to vault. Agent inbox sits
immediately below Share in the parent three-dot menu; there is no floating document
button. Share resumes an existing draft, captures an active lasso, or renders the
full current page in a separate native viewport. Both sources use the same review
and send controls. Full-page rendering is implemented and installed on the test
device; pixel accuracy and long-page coverage still need manual acceptance. Send to vault retains a raw capture for the Obsidian destination, but
the downstream scheduled handoff remains blocked pending a qualified image intake
path. Queued does not mean stored or organized.
