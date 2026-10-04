"""Independent narrow checks of I04/I08/I10/I11; authored inputs only."""
from pathlib import Path
import json
import sys
import time

import pytest

REPO=Path('F:/Project/xlm-quality-audit')
sys.path.insert(0,str(REPO/'tests'))
from quality_fixtures import build_corpus,document
from test_quality_audit import audit
from test_quality_hardening import resign
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import supervisor as sup
from xlm.data.quality import runner
from xlm.data.quality.cli import main
from xlm.data.quality.scan import RECEIPT_FILE,QualityError
from xlm.data.quality.detectors import analyze
from xlm.data.quality.policy import FLAGS
from xlm.data.quality.overlay import OverlayError


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path/'c',{'a/b/c':[document('id','This is authored prose.',1)]})


@pytest.mark.parametrize('stage',['shutdown_disk','last_worker_rss','finalization_deadline','prepublication_failure','receipt_recorded_failure','receipt_disk','receipt_rss','receipt_deadline'])
def test_i04_no_complete_or_success(corpus,tmp_path,monkeypatch,capsys,stage):
    guards=[]
    init=runner.Guard.__init__
    def recording(self,**kw):
        init(self,**kw)
        guards.append(self)
    monkeypatch.setattr(runner.Guard,'__init__',recording)
    if stage=='shutdown_disk':
        original=sup.Supervisor.__exit__
        def stop(self,*args):
            original(self,*args)
            self.fail(sup.DISK_REASON)
        monkeypatch.setattr(sup.Supervisor,'__exit__',stop)
    if stage=='last_worker_rss':
        original=runner.process_chunk
        def process(task):
            result=original(task)
            if task.last: guards[0].supervisor.fail(sup.RSS_REASON)
            return result
        monkeypatch.setattr(runner,'process_chunk',process)
    if stage=='finalization_deadline':
        original=runner.build_artifacts
        def aggregate(*args,**kwargs):
            result=original(*args,**kwargs)
            guards[0].supervisor.deadline.at=time.monotonic()-1
            return result
        monkeypatch.setattr(runner,'build_artifacts',aggregate)
    if stage=='prepublication_failure':
        original=runner.Guard.final
        def final(self,*args,**kw):
            assert self.stopped and not self.supervisor.thread.is_alive()
            self.supervisor.fail(sup.DISK_REASON)
            return original(self,*args,**kw)
        monkeypatch.setattr(runner.Guard,'final',final)
    if stage.startswith('receipt_'):
        original=canonical.write_atomic
        def write(path,payload):
            if path.name==RECEIPT_FILE:
                assert guards[0].stopped and not guards[0].supervisor.thread.is_alive()
                if stage=='receipt_recorded_failure':
                    guards[0].supervisor.fail(sup.RSS_REASON)
                elif stage=='receipt_disk':
                    monkeypatch.setattr(runner,'_free_bytes',lambda path:0)
                elif stage=='receipt_rss':
                    monkeypatch.setattr(runner,'_tree_rss',lambda:1<<60)
                else:
                    # Advance past the actual deadline during the receipt write.
                    guards[0].supervisor.deadline.at=time.monotonic()+0.03
                    time.sleep(0.06)
            return original(path,payload)
        monkeypatch.setattr(canonical,'write_atomic',write)
    out=tmp_path/'out'
    rc=main(['audit','--manifest',str(corpus),'--output',str(out),'--workers','1','--free-reserve-gib','0.000001','--no-progress'])
    value=json.loads(capsys.readouterr().out)
    print({'case':stage,'exit':rc,'response':value,'receipt':(out/RECEIPT_FILE).exists()})
    assert rc!=0 and value.get('refused') is True and 'complete' not in value
    assert not (out/RECEIPT_FILE).exists()


@pytest.mark.parametrize('text,expected',[
    ('Use `<html>` to start a page.',False),
    ('The `<body>` element contains text.',False),
    ('Example:\n\n    <html>\n    <body>Example</body>\n    </html>\n',False),
    ('```html\n<html><body>Example</body></html>\n```',False),
    ('```xml\n<?xml version="1.0"?><note>Example</note>\n```',False),
    ('The <html> tag is described in this prose sentence.',False),
    ('<!DOCTYPE html><html><head><title>Page</title></head><body>Real page</body></html>',True),
])
def test_i08_independent_context(text,expected):
    result=analyze(text,len(text.encode()))
    assert ('markup_full_html' in {FLAGS[n] for n in result.flags})==expected


@pytest.fixture(scope='module')
def valid_receipt(tmp_path_factory):
    root=tmp_path_factory.mktemp('valid')
    corpus=build_corpus(root/'c',{'a/b/c':[document('id','This is authored prose.',1)]})
    out=root/'out'
    audit(corpus,out)
    path=out/RECEIPT_FILE
    return corpus,out,path.read_bytes()


@pytest.mark.parametrize('change',[
    {'workers':0},{'workers':-1},{'workers':32},
    {'max_rss_bytes':0},{'max_rss_bytes':-1},
    {'deadline_seconds':0.0},{'deadline_seconds':-1.0},
    {'queue_tasks':0},{'queue_tasks':3},
    {'workers':True},{'workers':'1'},{'workers':1.0},
    {'deadline_seconds':600},{'max_output_bytes':False},
    {'publication_margin_seconds':600.0},{'review_per_stratum':0},
    {'max_pending_commits':0},{'verify_threads':0},
    {'unknown':1},{'DELETE':'workers'},
    {'deadline_seconds':float('nan')},{'deadline_seconds':float('inf')},
    {'deadline_seconds':float('-inf')},
])
def test_i10_reconstructed_invalid_receipt(valid_receipt,change):
    corpus,out,raw=valid_receipt
    body=json.loads(raw)
    if 'DELETE' in change: del body['envelope'][change['DELETE']]
    else: body['envelope'].update(change)
    try: body['digest']=canonical.self_digest(body)
    except ValueError: pass # nonfinite input has no canonical digest; verification must reject
    (out/RECEIPT_FILE).write_text(json.dumps(body),encoding='utf-8')
    try:
        with pytest.raises(QualityError): runner.verify_report(corpus,out)
    finally:
        (out/RECEIPT_FILE).write_bytes(raw)


def test_i10_valid_receipt(valid_receipt):
    corpus,out,_=valid_receipt
    assert runner.verify_report(corpus,out)['verified']


@pytest.mark.parametrize('prefix',[1,8,16,31,0])
def test_i11_signed_digest_mismatch(tmp_path,monkeypatch,prefix):
    root=Path('F:/qa-tmp-96f38f3/quality-c050/root')
    fixture={'proof':root/'proof.json','manifest':root/'manifest.json','key':'authored-pilot-only-not-a-protected-trust-root'}
    monkeypatch.setenv('XLM_AUTHORED_C05_KEY',fixture['key'])
    def change(rows):
        raw=bytearray.fromhex(rows[0]['content'])
        if prefix==31: raw[-1]^=1
        else:
            for i in range(prefix,32): raw[i]^=255
        rows[0]['content']=raw.hex()
    proof=resign(fixture,tmp_path,change)
    with pytest.raises((QualityError,OverlayError),match='content differs'):
        audit(fixture['manifest'],tmp_path/'out',proof=proof,allow_authored_proof=True)
