# Package artifact custody review

Exact source #235:
`1ae9baa696a14516a555b03ebeb00f8f4feb5ba5`, tree
`e95ca19ba1630d4e42a8875d819819009a52dcac`.
`ARCHITECTURE_235_ARTIFACTS.json` records SHA-256 for five wheels and five
source archives. `uv build --offline` from each package directory produced
the same bytes in two isolated build directories (ten matching artifacts).
The rebuilt Kernel wheel SHA-256 is
`8bc88c21bbe9f2a43c2dd19c697170324c3adcbf3bc9cdfe02ba72be15ee41fb`.
It contains 88 members, zero ERPNext Python implementation members, and
runtime dependencies only on `cryptography>=43.0` and the neutral
`originorion-connector-sdk==0.1.0`. Dev extras are separate. The Kernel source
archive contains 496 members, including ERPNext-named test files, but no
ERPNext implementation modules. Those test files are source-only and do not
create a runtime dependency.

`scripts/verify_orion_wheelhouse.py` installed a private, hash-locked
five-wheel Kernel dependency closure into a fresh venv with `--no-index` and
`--require-hashes`. Kernel imported with no ERPNext or pilot package installed.
A separate fresh eight-wheel closure installed Kernel, SDK, ERPNext, synthetic
and pilot compatibility packages; `pip check` passed. The installed entry
point group `orion.connectors.v1` contained ERPNext and synthetic. Reviewed
metadata registration for each returned `execution_allowed=False`. Removing
ERPNext and pilot compatibility distributions left Kernel import and
`pip check` healthy. Historical pilot imports existed only with their
independent compatibility wheel installed.

The old public name `orion-core==0.1.0` can resolve to an unrelated package.
This stack uses `originorion-*` names and a reviewed offline wheel manifest;
the verifier rejects extra/substituted/tampered wheels and unreviewed package
identities. Fifty-five focused tests included manifest rejection and import
boundaries. Name uniqueness alone is not a trust root. No public package was
published, and a malicious party replacing both manifest and trust record
remains an external custody threat.

The report covers rebuilt #235 artifacts. Later #240/#241 source and any
versioned release candidate require fresh artifact hashes and separate
qualification; hashes from this packet cannot be reused for changed trees.
