"""Additional bounded positive controls; separate from the failing safety probes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from xlm.data.evidence_v3 import executor, journal, synthetic

REPO = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("NO NETWORK")
    for obj, name in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
        monkeypatch.setattr(obj, name, deny)


RUNNER = r'''
import json, os, socket, sys
from pathlib import Path
from xlm.data.evidence_v3 import executor, synthetic
def deny(*args, **kwargs): raise AssertionError("NO NETWORK")
socket.socket.connect = socket.create_connection = socket.getaddrinfo = deny
repo, work, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
ep = synthetic.reload_epoch(repo, work, use_real_sampler=True)
def before(index, req):
    with open(work / 'calls.jsonl', 'a') as f:
        f.write(json.dumps({'pid': os.getpid(), 'range': [req.range_start, req.range_end], 'attempt': req.attempt_id})+'\n')
def rule(index, req, resp):
    ep.clock.advance(1)
    if mode == 'crash' and index == 1:
        resp.chunk_limit = 1
        def die(pos):
            if pos == 1: os._exit(137)
        resp.on_chunk = die
    return resp
ep.transport.before_open, ep.transport.rule = before, rule
print(json.dumps(executor.execute_phase_p(**ep.paths(), harness=ep.harness())))
'''


def test_exact_crash_resume_accounting(tmp_path):
    ep = synthetic.build_epoch(REPO, tmp_path, m_count=1, t_count=1)
    executor.check_authorization(**ep.paths(), harness=ep.harness())
    executor.perform_genesis(**ep.paths(), harness=ep.harness())
    start = (ep.root / 'epoch_start.json').read_bytes()
    def before(index, req):
        with open(tmp_path / 'calls.jsonl', 'a') as f:
            f.write(json.dumps({'pid': os.getpid(), 'range': [req.range_start, req.range_end], 'attempt': req.attempt_id})+'\n')
    def rule(index, req, resp):
        ep.clock.advance(1)
        return resp
    ep.transport.before_open, ep.transport.rule = before, rule
    executor.execute_phase_p(**ep.paths(), harness=ep.harness(), max_operations=1)
    crash = subprocess.run([sys.executable, '-c', RUNNER, str(REPO), str(tmp_path), 'crash'], capture_output=True, text=True, timeout=60)
    assert crash.returncode == 137, crash.stderr
    resume = subprocess.run([sys.executable, '-c', RUNNER, str(REPO), str(tmp_path), 'resume'], capture_output=True, text=True, timeout=60)
    assert resume.returncode == 0, resume.stderr
    state = executor.inspect_epoch(ep.root)
    calls = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    records = [json.loads(line) for line in (ep.root / 'state/journal.jsonl').read_bytes().splitlines()]
    completed = [(r['body']['op_complete']['arm'], r['body']['op_complete']['op_index']) for r in records
                 if r['type'] == 'DISK_RESERVE' and r['body']['op_complete'] is not None]
    assert completed == [('M',0), ('M',1), ('T',0), ('T',1), ('T',2)]
    assert [r['seq'] for r in records] == list(range(len(records)))
    assert (ep.root / 'epoch_start.json').read_bytes() == start
    assert len({c['pid'] for c in calls}) == 3
    assert len(calls) == 10 == sum(a['requests'] for a in state['arms'].values())
    # Ten requests at one synthetic second each, replacing the crashed request
    # by its 30-second hold, plus exactly one second retry backoff.
    elapsed = sum(a['time_ns'] for a in state['arms'].values())
    assert elapsed == 40_000_000_000
    for arm in ('M', 'T'):
        assert state['arms'][arm]['phase'] == 'P_COMPLETE_SEALED'
        attempts = [a for a in state['attempts'] if a['arm'] == arm]
        assert state['arms'][arm]['body_charged'] == sum(a['charged'] for a in attempts)
        assert state['arms'][arm]['body_held'] == 0
    crash_attempts = [a for a in state['attempts'] if a['outcome'] == 'CRASH_RESERVED']
    assert len(crash_attempts) == 1 and crash_attempts[0]['body_total'] == 1
    assert crash_attempts[0]['charged'] == crash_attempts[0]['reservation'] == 65537
    physical = {p.relative_to(ep.root).as_posix(): {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in ep.root.rglob('*') if p.is_file()}
    assert set(physical) == set(state['disk']) - {'state/journal.head.tmp'}
    for name, cell in physical.items():
        if name not in ('state/journal.jsonl', 'state/journal.head'):
            assert cell['bytes'] == state['disk'][name]['size']
    again = executor.execute_phase_p(**ep.paths(), harness=ep.harness())
    assert again['executed_operations'] == 0 and len(ep.transport.calls) == 2
    summary = {'requests': len(calls), 'active_ns': elapsed, 'journal_records_before_sealed_noop': len(records),
               'arms': state['arms'], 'attempts': state['attempts'], 'physical': physical,
               'completed': completed, 'genesis_sha256': hashlib.sha256(start).hexdigest(),
               'subprocess_exit_codes': [crash.returncode, resume.returncode]}
    (OUT / 'exact-recovery.json').write_text(json.dumps(summary, indent=2)+'\n')
    print({k:v for k,v in summary.items() if k not in ('physical','attempts','arms')})


def test_exact_eight_plus_eight_over_ten_disk_peak():
    body = {'epoch_start_digest': '0'*64, 'initial_inventory': {'old.bin': {'size':8, 'sha256':'a'*64, 'category':'scratch',
            'arms':['M'], 'control_allowance':False}}, 'plans':{},
            'disk_caps': {a:{'scratch':10, 'final':10, 'combined':10} for a in ('M','T')}}
    state = journal.new_state(body, 'synthetic')
    state.apply({'seq':0, 'type':'GENESIS', 'body':body})
    state.apply({'seq':1, 'type':'SESSION_OPEN', 'body':{'session':'test'}})
    assert state.occupancy('M') == 8
    for rel, replacement in [('new.bin',False), ('old.bin',True)]:
        with pytest.raises(journal.StateError, match='subcap|combined'):
            state.apply({'seq':2, 'type':'DISK_RESERVE', 'body':{'rel':rel,'temp_rel':rel+'.tmp','size':8,
                         'sha256':'a'*64,'category':'scratch','arms':['M'],'replaces':replacement,'op_complete':None}})
    assert not state.pending
