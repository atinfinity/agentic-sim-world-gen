# Security policy

Please report a vulnerability privately through GitHub's
[Private vulnerability reporting](https://github.com/atinfinity/agentic-sim-world-gen/security/advisories/new)
(Security tab → "Report a vulnerability"). Do not open a public issue for it.

Only the latest release and the `main` branch are supported. This is a personal project: there is no promised
response time, but reports are read.

Things worth knowing when you look at the code:

- The preview server and the live Isaac Sim connection are meant for the local machine. They bind to
  127.0.0.1 by default; binding to another address exposes them to your network.
- Scene folders, floor plans (DXF / IFC / JSON), photos, videos and third-party USD folders are treated as
  untrusted input, but USD files can reference other files and URLs. Only open assets you trust in Isaac Sim.
