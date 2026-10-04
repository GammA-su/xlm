"""Independent I10 cross-field/recorded-output consistency checks."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, 'F:/Project/xlm-quality-audit/tests')
from quality_fixtures import build_corpus, document
from test_quality_audit import audit
from xlm.data.evidence_v2 import canonical
from xlm.data.quality.runner import verify_report
from xlm.data.quality.scan import RECEIPT_FILE, QualityError


@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    root=tmp_path_factory.mktemp('relationships')
    corpus=build_corpus(root/'c', {'a/b/c':[document(str(i),'Authored prose for review sampling.',i+1) for i in range(8)]})
    output=root/'out'
    audit(corpus,output)
    raw=(output/RECEIPT_FILE).read_bytes()
    assert verify_report(corpus,output)['verified']
    return corpus,output,raw


@pytest.mark.parametrize('field', ['max_output_bytes','max_rss_bytes','max_document_bytes','review_per_stratum'])
def test_impossible_effective_envelope_refuses(completed,field):
    corpus,output,raw=completed
    body=json.loads(raw)
    before=body['envelope'][field]
    assert before>1
    if field=='max_output_bytes':
        assert sum(entry['bytes'] for entry in body['artifacts'].values())>1
    if field=='max_rss_bytes':
        assert body['execution']['peak_process_tree_rss_bytes']>1
    if field=='max_document_bytes':
        assert body['binding']['line_ceiling']>1
    body['envelope'][field]=1
    body['digest']=canonical.self_digest(body)
    path=output/RECEIPT_FILE
    path.write_bytes(canonical.canonical_bytes(body))
    try:
        with pytest.raises(QualityError):
            result=verify_report(corpus,output)
            print({'field':field,'claimed_limit':1,'original_limit':before,'result':result})
    finally:
        path.write_bytes(raw)
