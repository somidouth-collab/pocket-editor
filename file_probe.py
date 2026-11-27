"""Pure-filesystem probe, isolated from the Tk process."""
from pathlib import Path
import base64
import hashlib
import json
import sys

AUTO_LIMIT=8*1024*1024


def fingerprint(path):
    value=Path(path).stat()
    return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns


def inspect_file(record):
    key,serial,path,known,candidate,observed,verify,force=record
    known=tuple(known) if known is not None else None
    candidate=tuple(candidate) if candidate is not None else None
    try:
        signature=fingerprint(path)
        if not force and signature==known and not verify:return key,serial,'unchanged',signature,None,None
        if not force and signature!=candidate:return key,serial,'settling',signature,None,None
        if signature[2]>AUTO_LIMIT and not force:
            if signature==known:return key,serial,'unchanged',signature,None,None
            hasher=hashlib.sha256()
            with Path(path).open('rb') as stream:
                for chunk in iter(lambda:stream.read(65536),b''):hasher.update(chunk)
            if signature!=fingerprint(path):return key,serial,'settling',None,None,None
            digest=hasher.hexdigest()
            return key,serial,'same' if digest==observed else 'large',signature,digest,None
        data=Path(path).read_bytes()
        if signature!=fingerprint(path):return key,serial,'settling',None,None,None
        digest=hashlib.sha256(data).hexdigest()
        if digest==observed and not force:return key,serial,'same',signature,digest,None
        return key,serial,'content',signature,digest,data
    except FileNotFoundError:return key,serial,'missing',None,None,None
    except OSError as error:return key,serial,'error',None,None,str(error)


if __name__=='__main__':
    records=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    results=[]
    for record in records:
        value=list(inspect_file(record))
        if isinstance(value[-1],bytes):value[-1]=base64.b64encode(value[-1]).decode('ascii')
        results.append(value)
    Path(sys.argv[2]).write_text(json.dumps(results),encoding='utf-8')
