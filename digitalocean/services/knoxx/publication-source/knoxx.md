Knoxx

Knoxx is a local-first knowledge operations and agent workbench. It is the largest single piece of the constellation and the one most people would actually sit in front of.

It combines a backend, a browser frontend, an ingestion worker, and contract-backed definitions of policies, actors and models. Around that core sit adapters for model proxying, tool protocols, voice, chat, audio, and translation. The pieces are separable on purpose: the contracts describe what should happen, and the adapters are how it happens on a particular host.

What makes Knoxx unusual is that a great deal of its behaviour is data rather than code. Which agents exist, what capabilities they hold, which models they may use, what a trigger agrees to do when it observes an event: these are declarations that can be read, validated and changed without editing the program.

Knoxx is also the writer behind this garden. When a document should be published, Knoxx resolves the desired state from contracts, checks whether the evidence permits it, renders the artifact, and writes it where the website can read it. It never reaches into the website, and the website never reaches into it. They meet at a directory and a manifest, and each side is allowed to fail on its own.
