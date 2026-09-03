How This Page Got Here

It is worth describing the path this page took, because the path is one of the things being demonstrated.

The English text began as a file in the deployment repository, shipped read-only alongside the service that reads it. A Document contract named that file and declared its language. A Garden contract declared which languages this collection should exist in. Publication contracts declared where each language should appear.

A reconciler then compared what should be published against what actually was. For English it found a document with no translation requirement, rendered the text into an artifact addressed by the hash of its own content, wrote that artifact beside the existing manifest, and renamed the new manifest into place. Renaming within a filesystem is atomic, so no reader can ever observe a half-written manifest. If the write had failed, nothing new would have become public and the site would have kept serving what it had.

For the other four languages the gate found no translation and derived work rather than an error. That work was claimed, and an agent was given the source text and the target language, and asked to produce a translation and submit it. The submission became evidence: immutable, tied to a specific revision, attributed.

Then it stopped, on purpose. A translation that exists is not a translation that is published. A person has to read it and approve that exact revision. Only then does the next reconciliation find the evidence it needs and make the page public.

If you are reading this in a language other than English, someone approved this specific revision of this specific translation, and the record of that decision is why you can see it.
