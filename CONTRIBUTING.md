# Contributing

Keep changes small and run the checks in README.md. Include the behavior changed,
its regression coverage, and which hardware checks remain unverified. Do not
commit notebook content, screenshots containing private information, credentials,
firmware resources, compiled packages or proprietary development materials.

Firmware changes must retain digest and anchor checks and document compatibility
limits. Preserve durable request IDs, authentication separation and uncertain
no-replay behavior. Test against synthetic data before using real messages.

This repository is a reviewed export of a private development project. Open an
issue or pull request for proposed public changes. Maintainers reconcile accepted
changes into the source project before exporting a new snapshot; avoid editing
the release manifest manually as part of ordinary feature work.

The MIT license covers this project's original code. It grants no rights to
third-party firmware, Qt, XOVI, or agent software, which remain subject to their
respective terms. This repository distributes no firmware resource extraction.
