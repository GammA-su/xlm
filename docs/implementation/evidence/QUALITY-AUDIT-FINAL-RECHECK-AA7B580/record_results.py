from pathlib import Path
from collections import Counter
import hashlib
import json
import platform
import subprocess
import xml.etree.ElementTree as ET

repo=Path('F:/Project/xlm-quality-audit')
def git(*args):
    return subprocess.check_output(['git',*args],cwd=repo).decode().strip()
reports={
    'historical_49': 'F:/qa-aa7-astra.xml',
    'preserved_prior_edges': 'F:/qa-aa7-prior.xml',
    'focused_regressions': 'F:/qa-aa7-focused.xml',
    'new_independent': 'F:/qa-aa7-review/independent.xml',
    'envelope_relationships': 'F:/qa-aa7-review/relationships.xml',
}
results={}
for label,path in reports.items():
    root=ET.parse(path).getroot()
    suite=root.find('testsuite')
    failed=[{'name':case.attrib['name'],'failure':case.find('failure').text} for case in suite.findall('testcase') if case.find('failure') is not None]
    results[label]={'junit':path,**suite.attrib,'failed_nodes':failed}
out=Path('F:/qa-aa7-relationships/relationships0/out')
receipt=json.loads((out/'quality-audit-receipt.json').read_bytes())
rows=[json.loads(line) for line in (out/'review-manifest.jsonl').read_text().splitlines()]
counts=Counter((row['component'],row['detector'],row['coarse_bin']) for row in rows)
result={
    'head':git('rev-parse','HEAD'),
    'clean':not git('status','--short'),
    'environment':{'python':platform.python_version(),'platform':platform.platform()},
    'tests':results,
    'totals':{'passed':199,'failed':11,'skipped':0,'focused_deselected':34},
    'prior_harness_note':'One preserved edge probe waits for a monitor sample during receipt write, but this implementation already stopped the monitor. This failure is not counted as evidence of a product shutdown defect.',
    'verdicts':{'I04':'BLOCKED','I08':'PASS','I10':'BLOCKED','I11':'PASS'},
    'blockers':[
        'Receipt-write disk/RSS/deadline deterioration returns exit 0, success JSON and leaves COMPLETE: monitor stopped and no fresh checks after write.',
        'Redigested effective envelopes with 1-byte output, RSS, document limits or review_per_stratum=1 still verify despite contradictory artifacts/execution/binding/policy.',
        'Unchanged original test_child_rss_included fails with BrokenProcessPool rather than the expected RSS C05Error.',
    ],
    'relationship_facts':{'artifact_bytes':sum(a['bytes'] for a in receipt['artifacts'].values()),'peak_rss':receipt['execution']['peak_process_tree_rss_bytes'],'bound_line_ceiling':receipt['binding']['line_ceiling'],'recorded_review_per_stratum':receipt['envelope']['review_per_stratum'],'max_actual_review_rows_per_component_detector_bin':max(counts.values())},
    'static':{'scope':'src/xlm/data/quality + tests/test_quality_audit.py + tests/test_quality_detectors.py + tests/test_quality_hardening.py + tests/test_quality_final_repairs.py + tests/quality_fixtures.py + scripts/quality_audit_benchmark.py','ruff_format_check':0,'ruff_check':0,'mypy_strict':0,'git_diff_check':0,'files':20},
    'frozen_files':json.loads(Path('F:/qa188-review/frozen-hashes.json').read_bytes()),
    'probe_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [repo/'docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py',Path('F:/qa188-review/test_repair_edges.py')]},
}
Path('F:/qa-aa7-review/results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:result[k] for k in ['head','clean','totals','verdicts','relationship_facts']}))
