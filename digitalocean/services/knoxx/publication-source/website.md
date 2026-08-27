The Website

This site is a static artifact. There is no application server behind it, no database, and nothing to attack that would yield anything interesting.

Its own copy, the navigation and headings and product descriptions, is compiled into the build in five languages. A view never contains a sentence directly. It asks a dictionary for one, and a test reads the source files and fails if a literal sentence appears in a view. The dictionaries are checked against each other for identical key sets, identical placeholders, and identical proper nouns, so adding an English phrase breaks the other four languages until someone translates it. That is the intended order of events.

Published documents, including the one you are reading, are not compiled in. They arrive through a directory the site can read and a manifest that says what is public. Removing an entry from that manifest un-publishes the document and requires nothing else to happen anywhere. A file that no manifest entry names is not public, whatever is sitting on disk.

An absent manifest is a valid state, and the site is required to serve correctly with nothing published at all. A malformed manifest is not: the reader fails loudly, because a reader that renders a blank page turns someone else's defect into an invisible outage.
