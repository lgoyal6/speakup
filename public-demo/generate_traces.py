"""Export only safe, deterministic outcomes from native components using synthetic bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"services"))
from speakup.domain import RecordingState
from speakup.store import Store, StaleRevision
from speakup.uploads import ResumableUploads, OffsetMismatch, DigestMismatch

def generate():
    payload=bytes(range(256))*4
    scenarios={}
    for name in ["resume","final_ack","bad_digest"]:
        with tempfile.TemporaryDirectory(prefix="speakup-public-fixture-") as directory:
            root=Path(directory);store=Store(str(root/"fixture.db"));steps=[]
            try:
                uploads=ResumableUploads(store,root/"fixture-bytes")
                rid=store.create_recording("fixture-user",recording_id="00000000-0000-4000-8000-000000000001",expected_size=len(payload),expected_sha256=hashlib.sha256(payload).hexdigest())["id"]
                for state in [RecordingState.LOCAL_READY,RecordingState.UPLOADING]:store.transition("fixture-user",rid,state)
                steps.append({"event":"Synthetic upload created","result":{"state":"UPLOADING","total":len(payload)}})
                result=uploads.append("fixture-user",rid,0,len(payload),payload[:512])
                assert result.offset==512 and not result.complete
                steps.append({"event":"First chunk committed; client misses acknowledgement","result":{"durable_offset":result.offset,"complete":result.complete}})
                if name=="resume":
                    try:uploads.append("fixture-user",rid,0,len(payload),payload[:512]);raise AssertionError("duplicate chunk accepted")
                    except OffsetMismatch as error:
                        assert error.offset==512
                        steps.append({"event":"Repeated first chunk refused","result":{"reason":"offset_mismatch","server_offset":error.offset}})
                data=b"x"*512 if name=="bad_digest" else payload[512:]
                try:
                    result=uploads.append("fixture-user",rid,512,len(payload),data)
                    assert name!="bad_digest" and result.complete and result.sha256==hashlib.sha256(payload).hexdigest()
                    steps.append({"event":"Resume from authoritative offset","result":{"offset":result.offset,"complete":result.complete,"sha256":result.sha256}})
                except DigestMismatch:
                    assert name=="bad_digest" and uploads.offset(rid)==0 and store.get_recording("fixture-user",rid)["audio_sha256"] is None
                    steps.append({"event":"Digest mismatch; partial discarded","result":{"offset":uploads.offset(rid),"recorded_digest":None}})
                if name=="final_ack":
                    again=uploads.append("fixture-user",rid,512,len(payload),payload[512:])
                    assert again==result
                    steps.append({"event":"Repeated final chunk acknowledged without appending","result":{"offset":again.offset,"complete":again.complete,"sha256":again.sha256}})
            finally:store.db.close()
            scenarios[name]={"steps":steps}
    store=Store();steps=[]
    try:
        rid=store.create_recording("fixture-user",recording_id="00000000-0000-4000-8000-000000000002")["id"]
        first=store.save_transcript("fixture-user",rid,"Sample note","fixture",None)
        steps.append({"event":"Synthetic sample transcript saved","result":{"revision":first["revision"],"text":first["text"]}})
        newer=store.save_transcript("fixture-user",rid,"Reviewed sample note","user_edit",None,base_revision=1)
        steps.append({"event":"First editor saves revision 2","result":{"revision":newer["revision"],"text":newer["text"]}})
        try:store.save_transcript("fixture-user",rid,"Stale offline text","user_edit",None,base_revision=1);raise AssertionError("stale edit accepted")
        except StaleRevision as error:
            assert error.current_revision==2
            current=store.get_transcript("fixture-user",rid)
            assert current["text"]=="Reviewed sample note"
            steps.append({"event":"Stale edit refused; newer text preserved","result":{"reason":"stale_revision","current_revision":error.current_revision,"text":current["text"]}})
    finally:store.db.close()
    scenarios["edit_conflict"]={"steps":steps}
    return {"mode":"saved synthetic native-component replay","fixture_bytes":1024,
        "code":[{"path":name,"sha256":hashlib.sha256((ROOT/name).read_bytes()).hexdigest()}for name in ["services/speakup/domain.py","services/speakup/uploads.py","services/speakup/store.py"]],"scenarios":scenarios}

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--check",action="store_true");args=parser.parse_args()
    path=Path(__file__).with_name("traces.json");value=generate()
    if args.check:assert json.loads(path.read_text())==value;print("PASS: four native-component traces reconstructed exactly")
    else:path.write_text(json.dumps(value,indent=2)+"\n");print("Generated four safe synthetic traces")
