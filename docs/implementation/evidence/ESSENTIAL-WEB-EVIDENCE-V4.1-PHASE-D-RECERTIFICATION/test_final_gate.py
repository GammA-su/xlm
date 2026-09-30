"""Additional review-only probes of the real seal path on authored fixtures."""
import json
import shutil

import pytest

from test_astra_probes import fixture, no_network  # noqa: F401
from evidence_v4_support import SyntheticTransport
from evidence_v41_phase_d_support import run_d, tree
from xlm.data.evidence_v4 import phase_d
from xlm.data.evidence_v4 import phase_d_plan as pd


def stopped(result, root):
    assert (result.status, result.run_status) == ('INCOMPLETE', 'STOPPED')
    assert all(result.arms[a]['status'] == 'INCOMPLETE' for a in 'MT')
    for name in (phase_d.RECEIPT, phase_d.MANIFEST):
        path = root / name
        assert not path.exists() or json.loads(path.read_bytes())['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('arm', ['M', 'T'])
@pytest.mark.parametrize('change', ['missing', 'duplicate', 'reordered'])
def test_published_identity_corruption_stops(fixture, tmp_path, monkeypatch, arm, change):
    root = tmp_path / 'd'
    original = phase_d._PhaseDEngine._publish_outputs

    def publish(engine):
        result = original(engine)
        if engine.store.run().status == 'RUNNING':
            path = root / (phase_d.M_OUTPUT if arm == 'M' else phase_d.T_DOCUMENTS)
            lines = path.read_bytes().splitlines(keepends=True)
            altered = {'missing': lines[:-1], 'duplicate': [*lines[:-1], lines[0]], 'reordered': lines[::-1]}[change]
            path.write_bytes(b''.join(altered))
        return result

    monkeypatch.setattr(phase_d._PhaseDEngine, '_publish_outputs', publish)
    result = run_d(root, fixture, SyntheticTransport(fixture.fx))
    stopped(result, root)
    assert 'exactly the frozen' in result.stop_reason


@pytest.mark.parametrize('change', ['missing-export', 'hash-drift', 'receipt-binding', 'decoded-binding'])
def test_export_reconciliation_stops(fixture, tmp_path, monkeypatch, change):
    root = tmp_path / 'd'
    original = phase_d._PhaseDEngine._publish

    def publish(engine, name, raw):
        original(engine, name, raw)
        if name == phase_d.MANIFEST and engine.store.run().status == 'RUNNING':
            if change == 'missing-export':
                (root / phase_d.M_OUTPUT).unlink()
            elif change == 'hash-drift':
                path = root / phase_d.T_DOCUMENTS
                path.write_bytes(path.read_bytes() + b' ')
            elif change == 'receipt-binding':
                path = root / phase_d.RECEIPT
                obj = json.loads(path.read_bytes())
                obj['digest'] = '0' * 64
                path.write_text(json.dumps(obj), encoding='utf-8')
            else:
                with engine.dstore._tx() as db:
                    db.execute('DELETE FROM decoded_outputs WHERE name = ?', (phase_d.M_OUTPUT,))

    monkeypatch.setattr(phase_d._PhaseDEngine, '_publish', publish)
    stopped(run_d(root, fixture, SyntheticTransport(fixture.fx)), root)


@pytest.mark.parametrize('extra', [0, 1])
def test_post_complete_store_measurement(fixture, tmp_path, monkeypatch, extra):
    root = tmp_path / 'd'
    real = phase_d._root_bytes
    occupancy = {'base': 0}
    original = phase_d.PhaseDStore.set_status

    def status(store, value, reason, now):
        original(store, value, reason, now)
        if value == 'COMPLETE':
            occupancy['base'] = pd.ROOT_BYTES_MAX + extra - real(root)

    monkeypatch.setattr(phase_d, '_root_bytes', lambda p: real(p) + occupancy['base'])
    monkeypatch.setattr(phase_d.PhaseDStore, 'set_status', status)
    result = run_d(root, fixture, SyntheticTransport(fixture.fx))
    if extra:
        stopped(result, root)
        assert 'after COMPLETE' in result.stop_reason
    else:
        assert (result.status, result.run_status) == ('COMPLETE', 'COMPLETE')


@pytest.mark.parametrize('change', ['remove', 'same-size'])
def test_every_synthetic_parent_binding_at_last_gate(fixture, tmp_path, monkeypatch, change):
    before = tree(fixture.parent)
    original = phase_d._PhaseDEngine._check_exports
    for index, artifact in enumerate(fixture.plan.parents.artifacts):
        parent = tmp_path / f'parent-{index}'
        root = tmp_path / f'd-{index}'
        shutil.copytree(fixture.parent, parent)

        def check(engine):
            original(engine)
            path = parent / artifact.rel
            if change == 'remove':
                path.unlink()
            else:
                raw = path.read_bytes()
                assert raw
                path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])

        with monkeypatch.context() as local:
            local.setattr(phase_d._PhaseDEngine, '_check_exports', check)
            result = run_d(root, fixture, SyntheticTransport(fixture.fx), parent=parent)
        stopped(result, root)
        assert '(at completion)' in result.stop_reason
    assert tree(fixture.parent) == before
