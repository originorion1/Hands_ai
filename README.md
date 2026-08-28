# ORION Core

ORION is a small, vendor-neutral laboratory kernel for governed enterprise
system learning and shadow-only decision support. The v0.1 vertical slice
observes authorized system data through replaceable adapters, retains evidence
provenance and tenant scope, projects observed facts into a system graph, and
can propose a non-executing human-review action from validated customer
knowledge.

The project deliberately has no production ERP credentials, write adapters, or
customer data. ERPNext is represented only by a read-only adapter; future
systems can implement the same discovery port.

## Local demonstration

With Python 3.12 and the development dependencies installed, run:

```powershell
python -m pytest -q
python -m orion.demo
```

The demo uses in-memory ERPNext-shaped fixture data and always reports
`"execution_allowed": false`.
