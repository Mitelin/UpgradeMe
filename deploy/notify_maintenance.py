"""Publish an atomic completion event from systemd ExecStopPost, including failures."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import uuid


def notify(target):
    target = Path(target)
    event = {
        'id': uuid.uuid4().hex,
        'finished': datetime.now().astimezone().isoformat(timespec='seconds'),
        'result': os.environ.get('SERVICE_RESULT', 'unknown'),
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.' + target.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(event, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"UpgradeMe notified: {event['id']} (maintenance result: {event['result']})", flush=True)
    return event


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    notify(parser.parse_args().target)
