Katamorph

Katamorph is a portable contract language and interpreter toolkit for runtimes that are described by data rather than assembled by code.

The idea is straightforward and the consequences are not. Put identities, capabilities, policies, triggers, actions, sources, stores, providers and agent configuration into plain data. Katamorph then gives those declarations meaning: it validates them, resolves references between them, and hands a host a structure it can actually run.

Because the declarations are data, the same contract can be interpreted by more than one runtime, and two stages of a pipeline can agree on a shape without either of them owning it. That is the point. A contract that both sides reuse is a boundary. A shape that each side restates in its own words is a future disagreement.

Katamorph is the successor to an earlier contract runtime and is pinned by revision where it is used, so that a change to the language is something a repository adopts deliberately rather than receives by surprise.
