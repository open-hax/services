Proxx

Proxx is a model proxy. It presents one familiar interface and routes requests across multiple providers behind it.

The practical value is mundane and considerable. Providers differ in their protocols, their model names, their pricing and their failure modes. Proxx absorbs those differences so that the systems above it can ask for a model and get an answer, rather than each learning the particulars of every vendor.

It is also where pricing policy lives as data, and where account rotation across provider-scoped credentials happens. Credentials themselves stay local and are never part of what is published or deployed from a public repository.

Proxx is the piece the rest of the constellation leans on whenever a model is involved, including the translation agent that produces the other four languages of this page.
