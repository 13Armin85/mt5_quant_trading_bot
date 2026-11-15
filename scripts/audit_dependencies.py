"""Check pinned requirements against PyPI's published advisory metadata."""
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import json
from pathlib import Path
import re
import sys

import httpx


def check(line):
    match=re.match(r'([A-Za-z0-9_.-]+)==([^;\s]+)',line)
    if not match:
        return None
    name,version=match.groups()
    try:
        with httpx.Client(trust_env=False,timeout=30) as client:
            response=client.get(f'https://pypi.org/pypi/{name}/{version}/json')
            response.raise_for_status()
            data=response.json()
        advisories=[{'id':item['id'],'aliases':item.get('aliases',[]),
            'fixed_in':item.get('fixed_in',[]),'link':item.get('link')}
            for item in data.get('vulnerabilities',[]) if not item.get('withdrawn')]
        return {'package':name,'version':version,'advisories':advisories}
    except (httpx.HTTPError,ValueError,KeyError):
        return {'package':name,'version':version,'error':'Advisory metadata unavailable'}


def main():
    if '--installed' in sys.argv:
        lines=[f"{item.metadata['Name']}=={item.version}" for item in importlib.metadata.distributions()]
    else:
        lines=Path('requirements.txt').read_text().splitlines()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=[value for value in pool.map(check,lines) if value]
    print(json.dumps({'source':'PyPI advisory metadata','packages':results},indent=2))
    return int(any(value.get('advisories') or value.get('error') for value in results))


if __name__=='__main__':
    sys.exit(main())
