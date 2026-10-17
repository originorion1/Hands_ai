# Package supply-chain resolution (issue #229)

## Defect and boundary

The draft connector stack used `orion-core==0.1.0`. A clean name-only pip
resolution could select an unrelated public `orion-core` distribution. The
[public PyPI record](https://pypi.org/project/orion-core/) describes a different
multi-agent framework. A package name and version are not ORION provenance.

This candidate renames the five ORION distributions to `originorion-*`. Python
import paths remain `orion.*`. This namespace is a collision reduction, not a
claim of public package ownership or a release trust root. None of these
distributions has been published by this change.

## Release installation contract

Build the five wheels and source archives from one reviewed commit. Place the
wheels and all transitive dependency wheels in a private wheelhouse. Produce a
reviewed `orion.offline-wheelhouse.v1` manifest containing each wheel's exact
filename, distribution name, version, and SHA-256. Record the manifest SHA-256
in the release decision. Use `scripts/verify_orion_wheelhouse.py` with that
reviewed manifest and `--install` in a clean virtual environment. It checks
wheel bytes and embedded metadata before invoking pip with `--no-index`,
`--only-binary=:all:`, and `--require-hashes`, then runs `pip check`.

The script rejects missing or extra files, duplicate names, unexpected
distribution names (including the unrelated public `orion-core`), changed
wheel bytes, embedded name/version mismatch,
symlinks, and a wheel filename inconsistent with embedded identity. A new
manifest made from unreviewed wheels is not authorization. Never install an
ORION release with a name-only public-index command. Private registry or
signed attestations can be added later if public distribution is desired.

## Compatibility and risk

The historical pilot artifact checker now expects the renamed distribution
identities and still verifies installed RECORD hashes. A previously installed
pilot using old names requires a deliberate package migration; this branch
does not modify any live installation. External Python dependencies are frozen
by the release manifest, not by the project metadata's lower bound alone.
The trust root is the human-reviewed source commit, manifest digest, and
artifact digests; a malicious custodian able to replace all three remains a
release-process risk.
