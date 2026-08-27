Services

Services owns deployment. It holds the topology, the environment schemas, the host inventory, and the runbooks that describe how software gets onto a machine and how anyone can tell whether it worked.

The boundary is strict and worth stating. Application repositories own application code and tests. This repository owns where those applications run, what they are given, and what must be true for a deployment to count as successful. Application source and secrets do not live here.

Each service carries a verification script that is run as part of deploying it. These checks are deliberately suspicious. They read the running container's mount table rather than trusting a configuration file, assert file permissions rather than assuming them, and treat a probe that cannot run as a failure rather than a pass. A gate that converts an unavailable check into a green light is worse than no gate.

The contracts that describe this garden also ship from here, read-only, alongside the service that reads them. A deployment ships contracts; it never rewrites them.
