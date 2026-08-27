Foresight

Foresight is the workspace that holds the constellation together. It is not an application. It is the place where the boundaries between projects are declared, and where a claim that something is shared law has to be justified.

Each project is an independently owned repository. Foresight records what each one is, what role it plays, and where to look first for evidence about a given concern. When that record and the actual code disagree, the code wins and the record gets corrected. A routing hint is not a grant of authority.

The governing instruction is to purify before porting. Portable data, shapes, laws, validation, ledger semantics and pure functions default to code that runs on any Clojure host. Runtime specific code stays at the outer edge. When a useful function is tangled up with side effects, the effect gets separated from the decision rather than carried along.

There is also a ladder for choosing runtimes: prefer the lightest one that actually satisfies the requirement, and never let the runtime choice reshape the domain model. Moving outward on that ladder is an adapter decision. If a runtime migration forces the meaning of the system to fork, that is a signal the boundary was drawn in the wrong place.
