Muse

Muse is a compatibility and compilation workspace. It authors host-agnostic resources as data, links implementations to the places they are exposed, and emits artifacts in the native shape each agent harness expects.

Different agent hosts want different things. One wants a tool protocol, another wants plugins, another wants configuration in its own format. Writing the same capability four times is how four subtly different capabilities come to exist. Muse writes it once as data and compiles outward.

Muse is explicitly not a new agent harness, and it does not own the canonical model of actors, sessions, policies, capabilities, events or workflows. Those live elsewhere. This distinction is stated plainly in the repository because a compiler that quietly becomes an authority is a difficult problem to unwind later.
