"""Bounded rolling archive in a release, outside Git history. Uses GH_TOKEN via gh."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import zipfile
from datetime import datetime, timezone
import collector

TAG='world-guide-data-v3'

def gh(*args, binary=False):
    p=subprocess.run(['gh',*args],capture_output=True)
    if p.returncode:
        raise RuntimeError('GitHub data store operation failed; check contents:write and release access')
    return p.stdout if binary else p.stdout.decode()


def release():
    # A missing release is the only permitted cold start; permission/network failures fail closed.
    releases=json.loads(gh('api',f'repos/{os.environ["GH_REPO"]}/releases?per_page=100'))
    found=next((r for r in releases if r['tag_name']==TAG),None)
    if not found:
        gh('release','create',TAG,'--target','main','--title','World Guide V3 rolling data','--notes','7-day rolling snapshots; automated replacement; no Git history growth.')
        return release()
    return found


def restore(root):
    r=release(); assets=[a for a in r['assets'] if a['name'].startswith('snapshot-') and a['name'].endswith('.zip')]
    if not assets:
        print('Cold start: no persisted baseline; deltas unavailable')
        return
    a=max(assets,key=lambda a:a['created_at'])
    if a['size']>20_000_000:
        raise RuntimeError('Archive exceeds 20 MB bound')
    raw=gh('api',f'repos/{os.environ["GH_REPO"]}/releases/assets/{a["id"]}','-H','Accept: application/octet-stream',binary=True)
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist())>200_000_000:
            raise RuntimeError('Expanded archive too large')
        for i in z.infolist():
            p=Path(i.filename)
            if len(p.parts)!=2 or p.parts[0] not in ('history','reports') or '..' in p.parts or p.suffix not in ('.json','.csv','.md'):
                raise RuntimeError('Unexpected archive member')
        for i in z.infolist():
            p=Path(i.filename)
            dest=root/p;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(i))
    print('Restored persistent snapshot archive')


def save(root):
    collector.cleanup(root,datetime.now(timezone.utc),7)
    out=Path('work');out.mkdir(exist_ok=True)
    archive=out/('snapshot-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')+'.zip')
    if sum(p.stat().st_size for folder in ('history','reports') for p in (root/folder).glob('*') if p.is_file())>200_000_000:
        raise RuntimeError('Expanded archive exceeds size bound')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for folder in ('history','reports'):
            for p in sorted((root/folder).glob('*')):
                if p.is_file():z.write(p,p.relative_to(root))
    if archive.stat().st_size>20_000_000:raise RuntimeError('Archive exceeds size bound')
    r=release()
    gh('release','upload',TAG,str(archive))  # Old asset remains intact until successful upload.
    latest=release()
    if not any(a['name']==archive.name and a['size']==archive.stat().st_size for a in latest['assets']):
        raise RuntimeError('Upload verification failed; old snapshot preserved')
    for a in latest['assets']:
        if a['name'].startswith('snapshot-') and a['name']!=archive.name:
            gh('api','--method','DELETE',f'repos/{os.environ["GH_REPO"]}/releases/assets/{a["id"]}')
    print('Saved rolling archive; removed superseded assets')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['restore','save']);args=p.parse_args()
    try:
        (restore if args.mode=='restore' else save)(Path('data'))
    except (RuntimeError,OSError,ValueError,zipfile.BadZipFile):
        raise SystemExit('Persistent store failed; inspect permissions/connectivity/size. No secrets logged.')
