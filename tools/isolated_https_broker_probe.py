"""Fixed local HTTPS + isolated consumer proof; no activation or profile override."""
from isolated_broker_probe import run

if __name__ == '__main__':
    raise SystemExit(run(https=True))
