"""Focused independent boundary probes for repaired I04/I08/I10/I11 only."""
from pathlib import Path
import hashlib
import json
import sys

import pytest

REPO = Path('F:/Project/xlm-quality-audit')
sys.path.insert(0, str(REPO/'tests'))
from quality_fixtures import build_corpus, document
from test_quality_audit import audit, limits
from test_quality_hardening import resign
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import runner
from xlm.data.quality.runner import run_audit, verify_report
from xlm.data.quality.scan import RECEIPT_FILE, QualityError
from xlm.data.quality.detectors import analyze
from xlm.data.quality.policy import FLAGS
from xlm.data.quality.overlay import OverlayError


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path/'c', {'a/b/c':[document('a', 'A small authored example document.', 1)]})


@pytest.mark.parametrize('resource', ['disk','rss'])
def test_i04_publication_checks_current_resource_state(corpus, tmp_path, monkeypatch, resource):
    from xlm.data.exclusion import supervisor
    out = tmp_path/'out'
    captured = []
    original_init = runner.Guard.__init__
    def remember(self, **kwargs):
        original_init(self, **kwargs)
        captured.append(self)
    monkeypatch.setattr(runner.Guard, '__init__', remember)
    original_write = canonical.write_atomic
    original_disk = supervisor.shutil.disk_usage
    original_rss = supervisor.tree_rss
    violated = False
    def disk(path):
        value = original_disk(path)
        return value._replace(free=0) if violated and resource=='disk' else value
    def rss(*args, **kwargs):
        return 16*1024**3 if violated and resource=='rss' else original_rss(*args, **kwargs)
    monkeypatch.setattr(supervisor.shutil, 'disk_usage', disk)
    monkeypatch.setattr(supervisor, 'tree_rss', rss)
    def write(path, payload):
        nonlocal violated
        if path.name == RECEIPT_FILE:
            # A resource violation occurs as final publication begins; no actual
            # RAM/disk exhaustion. Event records also show whether it was sampled.
            violated = True
        return original_write(path, payload)
    monkeypatch.setattr(canonical, 'write_atomic', write)
    refused = False
    try:
        run_audit(corpus,out,limits=limits(free_reserve_bytes=1),progress_interval=None)
    except QualityError:
        refused = True
    print({'resource':resource,'violation':violated,'recorded_failure':captured[0].supervisor.failure,
           'complete_receipt':(out/RECEIPT_FILE).exists()})
    assert refused and not (out/RECEIPT_FILE).exists()


@pytest.mark.parametrize('field,value', [('workers',0),('max_rss_bytes',0),('deadline_seconds',0),('queue_tasks',999)])
def test_i10_impossible_effective_envelope_refuses(corpus,tmp_path,field,value):
    out=tmp_path/'out'
    audit(corpus,out)
    path=out/RECEIPT_FILE
    body=json.loads(path.read_bytes())
    body['envelope'][field]=value
    body['digest']=canonical.self_digest(body)
    path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError):
        verify_report(corpus,out)


@pytest.mark.parametrize('text',[
    'Example HTML source:\n\n    <!DOCTYPE html>\n    <html><body>example</body></html>\n',
    'The literal `<html>` starts an HTML document; this sentence is documentation.',
])
def test_i08_code_examples_are_ambiguous(text):
    result=analyze(text,len(text.encode()))
    assert 'markup_full_html' not in {FLAGS[i] for i in result.flags}


def test_i11_changed_content_digest_suffix_refuses(tmp_path,monkeypatch):
    root=Path('F:/qa-tmp-96f38f3/quality-c050/root')
    fixture={'proof':root/'proof.json','manifest':root/'manifest.json',
             'key':'authored-pilot-only-not-a-protected-trust-root'}
    monkeypatch.setenv('XLM_AUTHORED_C05_KEY',fixture['key'])
    def change(rows):
        value=rows[0]['content']
        rows[0]['content']=value[:-1]+('0' if value[-1]!='0' else '1')
    proof=resign(fixture,tmp_path,change)
    with pytest.raises((QualityError,OverlayError)):
        audit(fixture['manifest'],tmp_path/'out',proof=proof,allow_authored_proof=True)


def test_i04_failure_reported_by_inflight_sample_during_shutdown(corpus,tmp_path,monkeypatch):
    import threading
    from xlm.data.exclusion.supervisor import Supervisor, DISK_REASON
    armed=threading.Event()
    sampling=threading.Event()
    captured=[]
    real_sample=Supervisor.sample
    def delayed_sample(self):
        if armed.is_set():
            captured.append(self)
            sampling.set()
            assert self.stopped.wait(3), 'test monitor did not reach shutdown'
            self.fail(DISK_REASON)
        else:
            real_sample(self)
    monkeypatch.setattr(Supervisor,'sample',delayed_sample)
    real_write=canonical.write_atomic
    def write(path,payload):
        if path.name==RECEIPT_FILE:
            armed.set()
            assert sampling.wait(3), 'test monitor did not begin sampling'
        return real_write(path,payload)
    monkeypatch.setattr(canonical,'write_atomic',write)
    out=tmp_path/'out'
    refused=False
    try:
        audit(corpus,out)
    except QualityError:
        refused=True
    assert captured and captured[0].failure==DISK_REASON
    print({'recorded_failure':captured[0].failure,'receipt_exists':(out/RECEIPT_FILE).exists(),'refused':refused})
    assert refused and not (out/RECEIPT_FILE).exists()
