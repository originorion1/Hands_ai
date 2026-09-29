"""Fixed composition of the existing namespace and HTTPS fixture laboratories."""
import json
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from isolated_broker_lab import run_integration

if __name__ == '__main__':
    print(json.dumps(run_integration(transport='https'), sort_keys=True))
