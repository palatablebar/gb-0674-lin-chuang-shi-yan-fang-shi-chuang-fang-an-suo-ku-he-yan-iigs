import io, csv, json, zipfile, hashlib
from app.errors import BusinessError

def serialize(result):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    chain=result.get('chain') or {}
    active=chain.get('active') or {}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核'),
            'chain_id':active.get('chain_id'),'signers':active.get('signers',[]),'freeze':chain.get('freeze')},ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps({'counts':result['counts'],'views':result['views'],'quarantine':result['quarantine'],
            'signatures':chain.get('signatures',[]),'freeze_history':chain.get('freeze_history',[])},ensure_ascii=False,indent=2))
    return buffer.getvalue()
