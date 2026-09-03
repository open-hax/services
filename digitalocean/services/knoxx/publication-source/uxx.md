uxx

uxx is the user interface kit: a set of shared design tokens plus bindings for more than one component framework.

The constellation does not use a single frontend technology, and pretending otherwise would mean either forcing every project onto one choice or letting each grow its own visual language. uxx takes the third option. The tokens, the spacing, the colour, the type scale, are shared data. The component implementations are bindings that consume those tokens in whatever framework a given surface actually uses.

One implementation is canonical and the others are parity wrappers rather than independent rewrites. That word parity is doing real work: it means the wrappers are expected to match, and a difference between them is a defect rather than a dialect.
