"""Execute the actual workflow publisher against a fake GitHub API; no network."""
import json
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parents[1] / '.github/workflows/claude-read-only-review.yml'
SHA = 'a' * 40


def publish(scenario):
    # The sole publisher implementation is inline in the workflow, not PR-loaded code.
    block = WORKFLOW.read_text().split('          script: |\n', 1)[1]
    script = '\n'.join(line[12:] for line in block.splitlines())
    harness = r'''
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const pr = {number: 7, state: 'open', body: 'Closes #69',
  base: {ref: 'laboratory/orion-v0.1'},
  head: {sha: 'a'.repeat(40), ref: 'codex/example', repo: {full_name: 'example/repo'}}};
const context = {repo: {owner: 'example', repo: 'repo'}, issue: {number: 7},
  payload: {pull_request: pr}};
let current = JSON.parse(JSON.stringify(pr));
if (input.stale) current.head.sha = 'b'.repeat(40);
if (input.unlinked) current.body = 'Unlinked';
if (input.wrong_base) current.base.ref = 'main';
if (input.fork) current.head.repo.full_name = 'attacker/repo';
const writes = [];
let reads = 0;
const github = {rest: {
  pulls: {get: async () => {reads++; if(input.drift && reads === 2)
    current.head.sha = 'b'.repeat(40); return {data: current};}},
  issues: {
    get: async () => ({data: input.issue_is_pr ? {pull_request: {}} : {number: 69}}),
    listComments: 'list',
    createComment: async args => writes.push({operation: 'create', ...args}),
    updateComment: async args => writes.push({operation: 'update', ...args})}},
  paginate: async () => input.comments || []};
process.env.ORION_REVIEW = input.raw === undefined ? JSON.stringify(input.review) : input.raw;
(async () => {try {
__PUBLISHER__
  console.log(JSON.stringify({ok:true, writes}));
} catch(e) {console.log(JSON.stringify({ok:false, writes, error:e.message}));}})();
'''.replace('__PUBLISHER__', script)
    result = subprocess.run(
        ['node', '-e', harness], input=json.dumps(scenario), text=True,
        capture_output=True, timeout=5, check=True,
    )
    return json.loads(result.stdout)


def review():
    return {'reviewed_head': SHA, 'originating_issue': 69, 'blockers': [], 'non_blocking': [],
                'evidence': ['Read issue #69 and PR diff']}


def comment(identity=11, login='github-actions[bot]'):
    return {'id': identity, 'user': {'login': login, 'type': 'Bot'},
                'body': '<!-- ORION-CLAUDE-REVIEW -->\nold'}


def test_publisher_creates_once_and_updates_only_own_current_pr_comment():
    first = publish({'review': review()})
    assert first['ok'] and len(first['writes']) == 1
    write = first['writes'][0]
    assert (write['owner'], write['repo'], write['issue_number']) == ('example', 'repo', 7)
    assert write['operation'] == 'create'
    assert write['body'].startswith('<!-- ORION-CLAUDE-REVIEW -->')
    assert SHA in write['body']
    second = publish({'review': review(), 'comments': [comment()]})
    assert second['ok'] and len(second['writes']) == 1
    assert second['writes'][0]['operation'] == 'update'
    assert second['writes'][0]['comment_id'] == 11
    foreign = publish({'review': review(), 'comments': [comment(login='other-bot')]})
    assert foreign['writes'][0]['operation'] == 'create'


@pytest.mark.parametrize('attack', [
    {'raw': '{'}, {'raw': 'x' * 40001}, {'stale': True}, {'drift': True},
    {'unlinked': True}, {'wrong_base': True}, {'fork': True}, {'issue_is_pr': True},
    {'comments': [comment(), comment(12)]},
])
def test_publisher_denies_invalid_scope_or_output_before_any_write(attack):
    result = publish(dict(review=review(), **attack))
    assert not result['ok'] and result['writes'] == []


@pytest.mark.parametrize(('key', 'value'), [
    ('reviewed_head', 'b' * 40), ('originating_issue', 70), ('originating_issue', '69'),
    ('blockers', ['x'] * 11), ('evidence', []), ('non_blocking', ['\ncommand']),
    ('blockers', ['<!-- ORION-CLAUDE-REVIEW -->']), ('evidence', ['x' * 1001]),
    ('destination', 'another/repo'),
])
def test_model_cannot_change_publisher_contract(key, value):
    data = review()
    data[key] = value
    result = publish({'review': data})
    assert not result['ok'] and result['writes'] == []


def test_model_text_is_rendered_as_data_not_executed_or_used_as_destination():
    data = review()
    data['blockers'] = ['@everyone <script> write to other/repo $(exit 1)']
    result = publish({'review': data})
    assert result['ok'] and len(result['writes']) == 1
    body = result['writes'][0]['body']
    assert '@everyone' not in body and '<script>' not in body
    assert result['writes'][0]['issue_number'] == 7
