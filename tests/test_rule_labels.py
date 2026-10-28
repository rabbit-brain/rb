"""Human outcome labels name the declared criterion without changing the machine outcome."""
import json
import re

import pytest
from rbtest import as_the_user, main
from rabbit_brain import tui


@pytest.mark.parametrize('comparison,headline,green', [
    ('0.9[0.8,1.2]', 'estimate meets criterion · loss', False),
    ('0.2[0.1,0.3]', 'criterion met · loss', True),
    ('1.3[1.2,1.4]', 'criterion not met · loss', False),
])
def test_rule_labels_read_the_declared_threshold_not_the_direction(workdir, monkeypatch, capsys, comparison, headline, green):
    as_the_user(monkeypatch)

    def cli(*args):
        assert main([*args, '--json']) == 0
        return json.loads(capsys.readouterr().out)['data']

    cli('init', 'Threshold wording', '--no-agents')
    cli('metric', 'add', 'loss', '--minimize')
    cli('experiment', 'add', 'Comparison', '--id', 'e', '--baseline', 'a', '--candidate', 'b')
    cli('criterion', 'add', 'At most one unit higher', '-e', 'e', '--metric', 'change.loss', '--at-most', '1', '--id', 'c')
    cli('analysis', 'add', 'e', '--compare', f'b/a:loss={comparison}', '--method', 'synthetic interval', '--unit', 'seed', '--units', '3')
    finding = cli('show', 'c')['finding']
    assert finding['says']['headline'] == headline
    assert finding['rule_outcome'] == ('misses' if comparison.startswith('1.3') else 'meets')
    assert finding['result'] == 'loss'  # all intervals exceed zero; only some meet the separately declared threshold
    assert tui.standing_class(headline.split(' · ')[0]) == ('good' if green else 'bad' if finding['rule_outcome'] == 'misses' else 'dim')
    page = workdir / 'record.html'
    cli('page', '--out', str(page))
    # the card says its verdict once, under its title, led by the rule's state word (FIX2); a rule met on its estimate
    # says its interval does not show it yet; the card is green only when the rule is met soundly
    lead = headline.split(' · ')[0]
    text = page.read_text()
    stand = text.split('id="c-c"', 1)[1].split('<p class="str">', 1)[1].split('</p>', 1)[0]
    assert stand.startswith('Not met: ' if lead == 'criterion not met' else 'Met: '), stand
    assert ('its interval does not show it yet' in stand) == (lead == 'estimate meets criterion'), stand
    assert ('<div class="card crit is-good" id="c-c"' in text) == green
    assert '≤' in page.read_text()


@pytest.fixture
def linked(workdir, monkeypatch, capsys):
    """q0 above q1; three criteria linked to q1 (two met, one not met) and a planned experiment filed under q1."""
    as_the_user(monkeypatch)
    monkeypatch.setenv('RB_ACTOR', 'human:ana')

    def cli(*args, text=False):
        if text:
            main(list(args))
            return capsys.readouterr().out
        assert main([*args, '--json']) == 0, args
        return json.loads(capsys.readouterr().out)['data']

    cli('init', 'Labels on a question', '--no-agents')
    cli('metric', 'add', 'loss', '--minimize')
    cli('question', 'add', 'Can the new model ship?', '--id', 'q0')
    cli('question', 'add', 'Does it cost accuracy?', '--id', 'q1', '--parent', 'q0')
    cli('experiment', 'add', 'Comparison', '--id', 'e', '-q', 'q1', '--baseline', 'a', '--candidate', 'b')
    cli('experiment', 'add', 'Later', '--id', 'later', '-q', 'q1', '--hypothesis', 'it holds at scale')
    for cid, at_most in (('c1', '0.05'), ('c2', '0.5'), ('c3', '0')):
        cli('criterion', 'add', cid, '-e', 'e', '--metric', 'change.loss', '--at-most', at_most, '--id', cid, '-q', 'q1')
    for b in ('1.02', '1.04', '1.03'):
        cli('run', 'add', 'e', 'a.loss=1', f'b.loss={b}')
    return cli


def test_a_questions_counts_say_rbs_words_and_keep_linked_criteria_apart_from_what_is_below_it(linked):
    shown = linked('show', 'q1', text=True)
    assert '  linked here, by rule: 2 criteria met · 1 criterion not met · by result: 3 loss\n' in shown
    status = linked('status', text=True)
    assert '  below it: 1 sub-question, 1 open · 1 planned\n' in status
    assert '    linked here: 2 criteria met · 1 criterion not met; below it: 1 planned\n' in status
    assert not re.search(r'(?<!\w)\d+ (meets|misses|undetermined|untested)\b', shown + status)
    rollup = next(q for q in linked('status')['questions'] if q['id'] == 'q1')['rollup']
    assert rollup['rules'] == {'meets': 2, 'misses': 1} and rollup['results'] == {'loss': 3}


def test_a_call_says_the_rule_it_was_made_on_as_the_finding_words_it(linked):
    said = linked('decide', 'q1', 'continue', '--why', 'keep going', text=True)
    assert 'made on c3: +0.03 [+0.005159, +0.05484], n 3 (rb): loss, criterion not met' in said
    assert 'made on c1: +0.03 [+0.005159, +0.05484], n 3 (rb): loss, estimate meets criterion' in said
    said = linked('decide', 'c3', 'accept', '--why', 'close enough', text=True)
    assert 'This goes against the rule (criterion not met).' in said and 'misses' not in said
    # an --each rule met by its recorded values, with no interval yet
    linked('experiment', 'add', 'One run', '--id', 'once', '-q', 'q1', '--baseline', 'a', '--candidate', 'b')
    linked('criterion', 'add', 'each', '-e', 'once', '--metric', 'change.loss', '--at-most', '0.05', '--each', '--id', 'c4', '-q', 'q1')
    linked('run', 'add', 'once', 'a.loss=1', 'b.loss=1.01')
    assert linked('show', 'c4')['finding']['says']['headline'] == 'recorded values meet criterion · point only'
    said = linked('decide', 'c4', 'reject', '--why', 'one run is not enough', text=True)
    assert 'made on c4: +0.01, n 1 (rb): point only, recorded values meet criterion' in said
    assert 'This goes against the rule (recorded values meet criterion).' in said
    # the counts read the rule outcome, so c4 counts as met beside c1 and c2, never as a label of its own
    assert '  linked here, by rule: 3 criteria met · 1 criterion not met · by result: 3 loss · 1 point only\n' in linked('show', 'q1', text=True)
