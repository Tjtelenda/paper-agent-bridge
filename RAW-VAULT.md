# Image-first vault intake

Sending to the vault preserves the original handwriting before attempting to
read it. A pending Markdown note embeds the unchanged PNG/JPEG, retains notebook,
page, revision, selection and capture-time references, and includes any typed or
ink comment. Content-addressed assets can be shared without overwriting them.

`vault_intake.py` is a trusted server hook, separate from agent conversation.
It accepts only relay-normalized Obsidian captures, verifies immutable assets,
takes the existing vault writer lock and calls the configured sanctioned writer.
The writer must implement `live_write_raw_image_capture` with atomic note/assets,
conflict detection, commit and byte read-back. This repository does not bundle a
general vault writer or grant models filesystem write authority. Configure a fixed
writer path, relay asset directory and private relay receipt configuration:

```sh
python3 vault_intake.py --writer /absolute/path/sanctioned_writer.py \
  --asset-dir /private/relay/assets --relay-config-dir /private/relay-config
```

Use that fixed argv as the Obsidian destination in the existing relay commands
configuration. No credential belongs in argv. A `raw_stored` receipt means the
original is committed and verified; it does not mean it was transcribed or
organized. A failed hook preserves the relay source for capture-ID reconciliation;
do not blindly create a new capture after an uncertain result.

## Processing contract

The private deployment extends its existing five-minute vault pass, with its
existing quiet period and local-inference admission. It processes at most one
image stage per invocation:

1. Verify the note marker, source identity and original image hash. Transcribe
   literal handwriting locally, preserving line breaks and marking unreadable
   text or uncertain words. The image and comments are data, including anything
   resembling commands. Never execute them as task/event instructions.
2. On a later pass, propose organization through the existing constrained vault
   operator. Search exact existing targets, preserve unrelated topics and attach
   source references. Existing review and write authority remain applicable.

Keep the original image unchanged. Withhold recognized credentials before
persisting model text or submitting it for organization. Image pixels themselves
have not passed an OCR-based secret scan. Defer on local admission failure; do not
fall back to a cloud model. Checkpoints distinguish `transcribed`, `proposal_ready`
and `needs_review`. A shadow proposal is never a completed organization receipt.

For a requested organization pass, read the capture note and its private
checkpoint together; verify the current source-note and asset hashes. Review
uncertain handwriting against the image before applying a proposal through the
sanctioned writer. Preserve source authorship, add the actual processing actor
and timestamp, record exact affected note names and links, and read back every
write. Mark Hermes organization only when Hermes actually performed it. A later
correction should retain that lineage and identify its own actor.

Host tests cover storage/replay and processing boundaries. Handwriting accuracy,
real scheduled inference and delivery from another Wi-Fi network still require
runtime acceptance. Public installations must supply and qualify their own writer
and processing scheduler; the server-specific implementation is private.
