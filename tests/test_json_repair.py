from openforge.utils import extract_json, repair_json

def test_json_repair():
    # 1. Markdown fences
    raw = "```json\n{\"foo\": \"bar\"}\n```"
    assert extract_json(raw) == '{"foo": "bar"}'

    # 2. Trailing commas
    bad_comma = '{"foo": "bar",}'
    repaired = repair_json(bad_comma)
    assert repaired == '{"foo": "bar"}'

    # 3. Unterminated strings
    unterminated = '{"foo": "bar'
    repaired = repair_json(unterminated)
    assert repaired == '{"foo": "bar"}'

    # 4. Truncated JSON
    truncated = '{"files": [{"path": "a.py", "action": "modify"'
    repaired = repair_json(truncated)
    assert repaired == '{"files": [{"path": "a.py", "action": "modify"}]}'

    # 5. Conversational text before/after
    prose = "Here is the code:\n```json\n{\"test\": 123}\n```\nHope it helps!"
    assert extract_json(prose) == '{"test": 123}'
