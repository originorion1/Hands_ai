# Kernel isolation laboratory

Issue #145 adds an executable prerequisite experiment, not a production deployment.
Run from the repository root with Python 3.12+:

```
python3 tools/pilot_isolation_probe.py
```

The fixed launcher runs `tests/isolation_lab.py` in a fresh interpreter with a
minimal environment and closed descriptors. No pytest installation is needed.
No supplied URL, code/module selector, source credential or customer configuration
is accepted. The fixture creates private random canaries and audit bytes, a TCP
listener bound only to 127.0.0.1 and a Unix listener in its temporary directory.
It first verifies that the unsandboxed test process can reach these resources.
These negative controls prevent an absent listener from masquerading as isolation.
No external network destination is used. All temporary resources are discarded.

When prerequisites work, bubblewrap creates user, PID, mount and network namespaces,
drops capabilities, selects UID/GID 65534, clears the environment and exposes only
read-only system/runtime trees, a fixed test script, private /tmp, /proc and /dev.
The child tries to read the synthetic secret, append to the synthetic audit file,
read the host fixture's process environment and connect to the host loopback/Unix
listeners. It also checks namespace identities, capabilities, UID and descriptors.
The parent independently verifies that audit bytes did not change.

PASS means only this specific local profile was exercised with all assertions true.
It never means that the ORION application or broker has been deployed inside it.
Missing/duplicate/nonboolean assertions, skipped negative controls, altered audit
bytes and any successful escape cannot pass. Launcher failure, unavailable utility,
unsupported runtime, local socket denial or timeout yields BLOCKED and exit 2.
Parser tests using constructed results are not OS containment evidence.

The profile exposes system libraries and the running interpreter's base prefix
read-only. Its adequacy for an actual deployment must be reviewed separately.
Bubblewrap and the kernel, test runner, interpreter and repository are trusted.
The test does not prove cross-UID service management, broker-only secret delivery,
protected durable audit custody, permitted broker HTTPS egress, asynchronous stop,
crash supervision or production filesystem minimization. It does not change either
existing release gate, issue authority, start a broker or activate customer access.

In the current execution environment, synthetic local socket setup is denied,
so the complete experiment is BLOCKED before negative controls can be established.
A separate earlier bubblewrap probe also failed its network-namespace setup.
The next required environment capability is permitted loopback/Unix test sockets
and unprivileged user/PID/mount/network namespaces (or an equivalently reviewed
isolated test host). Do not relax ORION release gates to compensate for their absence.
