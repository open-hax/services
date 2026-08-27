The Constellation

OpenHax is not one program. It is a group of repositories that grew from different problems and are being pulled toward a shared set of ideas. Foresight is the workspace that holds them together and decides what is common law and what is local detail.

The vocabulary is deliberately small. Alpha asks whether a thing is well formed before anyone uses it. Eta is transduction: a worker consumes an artifact and produces another one, usually through agents and tools. Mu is evaluation: comparing what happened against what was intended, and producing a judgment. Big Pi is representation: how content becomes something a reader can actually see. Katamorph sits between these as the shape and contract machinery they all reuse.

These are conceptual centres, not boxes. A workflow can compose any of them in any order, so long as what one stage produces satisfies what the next stage requires. There is no fixed pipeline everything must march through.

The practical rule underneath all of it is that portable meaning should be written once, in portable Clojure, and that runtimes are adapters at the edge. Shapes, laws, identity rules, validation, and state transitions belong in code that runs anywhere. Servers, databases, filesystems and browsers depend on that layer, and the layer never depends back on them.

When code is rescued from an older system, the shapes and laws come first and the runtime baggage stays behind. That is slower than copying, and it is the reason these repositories are converging rather than multiplying.
