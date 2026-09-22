import json
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest
from test_erpnext_historical_sample import FakeResponse
from test_pilot_read import NOW, grant, launch, row

from orion.discovery.erpnext_pilot import ERPNextPilotReader
from orion.pilot.journal import AttemptJournal, JournalDenied, TransportLimits, grant_digest


def limits(**updates):
    base=TransportLimits(3,4096,1024,3072,1,2,NOW+timedelta(hours=1))
    return replace(base,**updates)


def journal(tmp_path,**kwargs):
    return AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(grant()),
                          limits=limits(**kwargs))


def adapter(j,calls,*,response=None,callback=None):
    def opener(req,timeout):
        calls.append(1)
        if callback:
            callback()
        return response or FakeResponse({'data':[row()]},url=req.full_url)
    return ERPNextPilotReader(source_id=grant().source_id,api_key='TEST_KEY_MUST_NOT_LEAK',
        api_secret='TEST_SECRET_MUST_NOT_LEAK',opener=opener,journal=j)


def test_actual_pilot_transport_accounts_and_persists(tmp_path):
    j=journal(tmp_path)
    calls=[]
    observations=launch(adapter(j,calls))
    assert len(observations)==1 and calls==[1]
    assert j.inspect()['attempts']==1 and not j.inspect()['pending']
    restored=AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(grant()),
                             limits=limits(),expected_head=j.head)
    assert restored.inspect()==j.inspect()
    with pytest.raises(ValueError):
        launch(adapter(restored,calls))  # Rate limit survives restart.
    assert calls==[1]
    launch(adapter(restored,calls),clock=lambda:NOW+timedelta(seconds=2))
    assert calls==[1,1]
    with sqlite3.connect(tmp_path/'attempts.db') as db:
        data=repr(db.execute('SELECT * FROM events').fetchall())
    assert all(s not in data for s in ('TEST_KEY','TEST_SECRET','posting_date','company','token '))


@pytest.mark.parametrize('updates',[{'max_requests':1},{'total_response_bytes':1024}])
def test_request_and_reserved_byte_budgets_never_reset(tmp_path,updates):
    j=journal(tmp_path,**updates)
    calls=[]
    launch(adapter(j,calls))
    with pytest.raises(ValueError):
        launch(adapter(j,calls),clock=lambda:NOW+timedelta(seconds=2))
    assert calls==[1]


def test_scope_and_request_size_reject_before_opener(tmp_path):
    j=journal(tmp_path,request_bytes=10)
    calls=[]
    with pytest.raises(ValueError):
        launch(adapter(j,calls))
    assert calls==[] and j.inspect()['attempts']==0


def test_oversize_and_upstream_failures_open_circuit(tmp_path):
    j=journal(tmp_path,failures=1)
    calls=[]
    with pytest.raises(ValueError):
        launch(adapter(j,calls,response=FakeResponse({'data':'x'*2048})))
    assert j.inspect()['failures']==1
    with pytest.raises(ValueError):
        launch(adapter(j,calls),clock=lambda:NOW+timedelta(seconds=2))
    assert calls==[1]


def test_pending_attempt_after_crash_blocks_restart(tmp_path):
    j=journal(tmp_path)
    j.begin(NOW,1)
    head=j.head
    del j
    j=AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(grant()),
                     limits=limits(),expected_head=head)
    with pytest.raises(JournalDenied):
        j.begin(NOW+timedelta(seconds=2),1)
    assert j.inspect()['attempts']==1 and j.inspect()['pending']
    j.stop()
    with pytest.raises(JournalDenied):
        j.begin(NOW+timedelta(seconds=3),1)


def test_audit_mutation_and_old_tip_rejected(tmp_path):
    j=journal(tmp_path)
    old=j.head
    j.begin(NOW,1)
    j.finish(success=True,received_bytes=1)
    with pytest.raises(JournalDenied):
        AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(grant()),
                       limits=limits(),expected_head=old)
    with sqlite3.connect(tmp_path/'attempts.db') as db:
        db.execute("UPDATE events SET body='{}' WHERE sequence=2")
    with pytest.raises(JournalDenied):
        j.inspect()


def test_competing_writer_and_changed_limits_fail_closed(tmp_path):
    a=journal(tmp_path)
    b=AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(grant()),
                     limits=limits(),expected_head=a.head)
    a.begin(NOW,1)
    with pytest.raises(JournalDenied):
        b.begin(NOW,1)
    with pytest.raises(JournalDenied):
        AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(grant()),
                       limits=limits(max_requests=100),expected_head=a.head)


def test_expiry_revocation_and_stop_reject_before_admission(tmp_path):
    j=journal(tmp_path)
    calls=[]
    current={'grant':grant()}
    with pytest.raises(ValueError):
        launch(adapter(j,calls,callback=lambda:current.update(grant=None)),
               grants=lambda k:current['grant'])
    assert calls==[1] and j.inspect()['failures']==1
    j.stop()
    with pytest.raises(ValueError):
        launch(adapter(j,calls),clock=lambda:NOW+timedelta(seconds=2))
    assert calls==[1]


def test_denied_authorization_and_wrong_journal_do_not_consume_io(tmp_path):
    j=journal(tmp_path)
    calls=[]
    with pytest.raises(ValueError):
        launch(adapter(j,calls),grants=lambda k:None)
    assert calls==[] and j.inspect()['attempts']==0
    altered=replace(grant(),authorization_id='different')
    j=AttemptJournal(tmp_path/'other.db',key=b'x'*32,binding=grant_digest(altered),limits=limits())
    with pytest.raises(ValueError):
        launch(adapter(j,calls))
    assert calls==[]


def test_metadata_requests_share_one_durable_budget(tmp_path):
    from test_pilot_metadata import grant as mg
    from test_pilot_metadata import launch as ml
    from test_pilot_metadata import reader as mr
    j=AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(mg()),
                     limits=limits(max_requests=1))
    calls=[]
    reader=mr(calls)
    reader._journal=j
    tick=[0]
    def clock():
        tick[0]+=1
        return NOW+timedelta(seconds=tick[0])
    with pytest.raises(ValueError):
        ml(reader,clock=clock)
    assert len(calls)==1 and j.inspect()['attempts']==1


def test_readiness_rejects_secrets_unknown_flags_and_activation(tmp_path,capsys):
    from orion.pilot.readiness import main, release_report
    cfg={'tenant_id':'synthetic','company':'synthetic','source_id':'https://example.test',
         'key_reference':'PILOT_KEY','secret_reference':'PILOT_SECRET','mode':'read_only'}
    path=tmp_path/'pilot.json'
    path.write_text(json.dumps(cfg))
    assert main(['--config',str(path),'--start'])==2
    report=json.loads(capsys.readouterr().out)
    assert report['live_ready'] is False and report['status']=='startup_denied'
    assert len(report['gates'])==19
    assert all(g['status'] in {'PASS','FAIL','BLOCKED','NOT_APPLICABLE'} for g in report['gates'])
    for extra in ({'force':True},{'secret':'DO_NOT_PRINT_ME'},{'mode':'write'},
                  {'key_reference':'DO_NOT_PRINT_ME!'}):
        path.write_text(json.dumps(cfg|extra))
        assert main(['--config',str(path),'--start'])==2
        output=capsys.readouterr().out
        assert 'DO_NOT_PRINT_ME' not in output
    assert release_report()['execution_allowed'] is False


def test_journal_boundaries_are_read_only_and_missing_tip_denies(tmp_path):
    j=journal(tmp_path)
    with pytest.raises(AttributeError):
        j.limits=limits(max_requests=100)
    with pytest.raises(AttributeError):
        j.binding='0'*64
    with pytest.raises(JournalDenied):
        AttemptJournal(tmp_path/'attempts.db',key=b'x'*32,binding=grant_digest(grant()),limits=limits())


def _supervised_references(journal, **updates):
    references = {'caller':'a'*64, 'scope':journal.binding, 'version':'b'*64}
    references.update(updates)
    return references


def _provisioned_journal(tmp_path, *, max_requests=48):
    settings = limits(max_requests=max_requests, response_bytes=1,
                      total_response_bytes=max_requests, failures=10)
    provision = {'transition_reference':'c'*64, 'metadata_checkpoint':2,
                 'metadata_evidence_head':'d'*64, 'predecessor_generation':0}
    journal = AttemptJournal(tmp_path/'attempts.db', key=b'x'*32,
        binding=grant_digest(grant()), limits=settings, provision=provision)
    return journal, settings, provision


def _complete_supervised_attempt(journal, index):
    now = NOW+timedelta(seconds=index*2)
    request = format(index, '064x')
    journal.lifecycle('broker_request', at=now,
                      references=_supervised_references(journal, request=request))
    journal.begin(now, 1)
    journal.finish(success=True, received_bytes=1)
    journal.lifecycle('broker_admitted', at=now,
                      references=_supervised_references(
                          journal, request=request, observations='e'*64))


def test_provisioned_supervised_budget_is_fully_representable_and_reopens(tmp_path):
    journal, settings, provision = _provisioned_journal(tmp_path)
    journal.lifecycle('broker_start', at=NOW,
                      references=_supervised_references(journal))
    journal.lifecycle('broker_arm', at=NOW,
                      references=_supervised_references(journal))
    for index in range(settings.max_requests):
        _complete_supervised_attempt(journal, index + 1)

    denied_at = NOW+timedelta(seconds=2*(settings.max_requests+1))
    journal.lifecycle('broker_request', at=denied_at,
                      references=_supervised_references(journal, request='f'*64))
    with pytest.raises(JournalDenied, match='exhausted budget'):
        journal.begin(denied_at, 1)
    journal.lifecycle('broker_denied', at=denied_at,
                      references=_supervised_references(journal, reason='1'*64))
    journal.stop()
    journal.lifecycle('broker_stop', at=denied_at,
                      references=_supervised_references(journal))

    restored = AttemptJournal(tmp_path/'attempts.db', key=b'x'*32,
        binding=grant_digest(grant()), limits=settings, expected_head=journal.head,
        provision=provision)
    assert restored.inspect()['attempts'] == settings.max_requests
    assert restored.inspect()['stopped'] and not restored.inspect()['pending']


def test_direct_journal_retains_maximum_legacy_request_budget(tmp_path):
    settings = limits(max_requests=100, response_bytes=1,
                      total_response_bytes=100, failures=10)
    journal = AttemptJournal(tmp_path/'attempts.db', key=b'x'*32,
        binding=grant_digest(grant()), limits=settings)
    for index in range(settings.max_requests):
        now = NOW+timedelta(seconds=index*2)
        journal.begin(now, 1)
        journal.finish(success=True, received_bytes=1)
    journal.stop()
    restored = AttemptJournal(tmp_path/'attempts.db', key=b'x'*32,
        binding=grant_digest(grant()), limits=settings, expected_head=journal.head)
    assert restored.inspect()['attempts'] == settings.max_requests
    assert restored.inspect()['stopped'] and not restored.inspect()['pending']


def test_interrupted_final_supervised_attempt_stays_consumed_and_blocks_replay(tmp_path):
    journal, settings, provision = _provisioned_journal(tmp_path)
    journal.lifecycle('broker_start', at=NOW,
                      references=_supervised_references(journal))
    journal.lifecycle('broker_arm', at=NOW,
                      references=_supervised_references(journal))
    for index in range(settings.max_requests - 1):
        _complete_supervised_attempt(journal, index + 1)
    interrupted_at = NOW+timedelta(seconds=settings.max_requests*2)
    journal.lifecycle('broker_request', at=interrupted_at,
                      references=_supervised_references(journal, request='f'*64))
    journal.begin(interrupted_at, 1)

    restored = AttemptJournal(tmp_path/'attempts.db', key=b'x'*32,
        binding=grant_digest(grant()), limits=settings, expected_head=journal.head,
        provision=provision)
    assert restored.inspect()['attempts'] == settings.max_requests
    assert restored.inspect()['pending'] and not restored.inspect()['stopped']
    with pytest.raises(JournalDenied, match='pending'):
        restored.begin(interrupted_at+timedelta(seconds=2), 1)
    with pytest.raises(JournalDenied, match='pending'):
        restored.lifecycle('broker_denied', at=interrupted_at,
                           references=_supervised_references(restored, reason='1'*64))
    restored.stop()
    assert restored.inspect()['attempts'] == settings.max_requests
    assert restored.inspect()['stopped'] and restored.inspect()['pending']
    with pytest.raises(JournalDenied, match='stopped'):
        restored.begin(interrupted_at+timedelta(seconds=4), 1)
    stopped = AttemptJournal(tmp_path/'attempts.db', key=b'x'*32,
        binding=grant_digest(grant()), limits=settings, expected_head=restored.head,
        provision=provision)
    assert stopped.inspect()['stopped'] and stopped.inspect()['pending']
    with pytest.raises(JournalDenied, match='stopped'):
        stopped.begin(interrupted_at+timedelta(seconds=6), 1)


def test_upstream_exception_and_response_text_never_enter_journal(tmp_path):
    j=journal(tmp_path)
    calls=[]
    def failure():
        raise RuntimeError('TEST_SECRET_MUST_NOT_LEAK')
    with pytest.raises(ValueError) as exc:
        launch(adapter(j,calls,callback=failure))
    assert 'TEST_SECRET' not in str(exc.value)
    with sqlite3.connect(tmp_path/'attempts.db') as db:
        text=repr(db.execute('SELECT * FROM events').fetchall())
    assert 'TEST_SECRET' not in text and j.inspect()['failures']==1
