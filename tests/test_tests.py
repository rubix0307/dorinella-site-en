"""The Bristol test: scoring, the advice per type, the page, the form and the JSON API."""
import pytest
from fastapi.testclient import TestClient

from app.learn import BRISTOL
from app.main import app

client = TestClient(app, follow_redirects=False)


def _evaluate(answers):
    response = client.post('/api/v1/tests/bristol/evaluate', json={'answers': answers})
    assert response.status_code == 200, response.text
    return response.json()


def _result_html(page: str) -> str:
    return page.split('data-result')[1].split('Take it again', maxsplit=1)[0]


def test_the_test_has_seven_types_and_three_questions():
    assert [b.key for b in reversed(BRISTOL.bands)] == [f'type-{n}' for n in range(1, 8)]
    assert len(BRISTOL.questions) == 3 and len(BRISTOL.questions[0].options) == 7
    assert BRISTOL.validate([0, 0, 0]) == [] and BRISTOL.validate([7, 0, 0]) and BRISTOL.validate([0])


@pytest.mark.parametrize('chosen', range(7))
def test_every_type_gets_its_own_reading_and_a_doctor_note(chosen):
    result = _evaluate([chosen, 1, 0])
    assert result['score'] == chosen + 1 and result['band']['key'] == f'type-{chosen + 1}'
    assert result['band']['title'].startswith(f'Type {chosen + 1}:')
    assert result['band']['text'] and result['band']['tips'] and result['band']['doctor']
    assert result['alert'] is False


def test_massage_and_paid_links_follow_the_stool_type():
    firm = _evaluate([1, 1, 0])
    assert firm['massage'] and firm['paid_guide_url'] == '/products/gut-massage-guide/' and firm['course_url'] == '/products/massage-course/'
    assert firm['guide_url'] == '/guides/gut-basics/'
    loose = _evaluate([6, 1, 0])
    assert not loose['massage'] and loose['paid_guide_url'] is None and loose['course_url'] is None
    assert loose['guide_url'] == '/guides/gut-basics/'


@pytest.mark.parametrize('flag', [1, 2])
def test_a_warning_sign_puts_the_doctor_first_and_drops_the_sales_links(flag):
    result = _evaluate([1, 1, flag])
    assert result['alert'] and not result['massage'] and result['paid_guide_url'] is None
    assert any(n['warning'] for n in result['notes'])


def test_frequency_notes_are_added_when_they_apply():
    assert not _evaluate([3, 1, 0])['notes']
    assert 'constipation' in _evaluate([3, 3, 0])['notes'][0]['text']


def test_the_unknown_test_and_bad_answers_are_refused():
    assert client.get('/api/v1/tests/nope').status_code == 404
    assert client.post('/api/v1/tests/nope/evaluate', json={'answers': [0, 0, 0]}).status_code == 404
    assert client.post('/api/v1/tests/bristol/evaluate', json={'answers': [0]}).status_code == 422
    assert [q['text'] for q in client.get('/api/v1/tests/bristol').json()['questions']] == [q.text for q in BRISTOL.questions]


def test_the_page_renders_the_form():
    response = client.get('/tests/bristol/')
    assert response.status_code == 200 and 'data-quiz' in response.text and '<h1' in response.text
    assert client.get('/tests/other/').status_code == 404


def test_the_form_fallback_renders_the_result_and_the_missing_answer_is_refused():
    full = client.post('/tests/bristol/', data={'q0': 3, 'q1': 1, 'q2': 0})
    assert full.status_code == 200 and 'Type 4: smooth, soft sausage' in full.text
    assert 'When to see a doctor' in full.text
    assert 'for education only' in full.text
    assert 'href="/guides/gut-basics/"' in full.text and 'href="/products/gut-massage-guide/"' in full.text
    missing = client.post('/tests/bristol/', data={'q0': 3})
    assert missing.status_code == 422 and 'Please answer every question.' in missing.text


def test_a_warning_result_shows_no_tips_and_no_sales_links():
    page = client.post('/tests/bristol/', data={'q0': 0, 'q1': 1, 'q2': 1}).text
    assert 'role="alert"' in page and 'Gentle things that often help' not in page
    assert '/products/' not in _result_html(page)


def test_a_loose_result_says_massage_is_not_for_diarrhea():
    page = client.post('/tests/bristol/', data={'q0': 6, 'q1': 1, 'q2': 0}).text
    assert 'not meant for diarrhea' in page and '/products/' not in _result_html(page)
