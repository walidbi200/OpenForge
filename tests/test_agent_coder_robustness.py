import pytest
from openforge.utils import repair_json, extract_json

def test_repair_json_trailing_commas():
    # Fix trailing comma in object
    assert repair_json('{"a": 1, }') == '{"a": 1}'
    # Fix trailing comma in array
    assert repair_json('{"a": [1, 2, ]}') == '{"a": [1, 2]}'

def test_repair_json_truncated_object():
    # Missing closing brace
    assert repair_json('{"a": 1') == '{"a": 1}'

def test_repair_json_truncated_array():
    # Missing closing bracket and brace
    assert repair_json('{"a": [1, 2, 3') == '{"a": [1, 2, 3]}'

def test_repair_json_truncated_string():
    # Missing closing quote, bracket, and brace
    assert repair_json('{"a": ["b') == '{"a": ["b"]}'

def test_repair_json_trailing_comma_truncated():
    # Truncated right after a comma
    # It might not produce perfect json depending on repair logic, but let's test our logic
    repaired = repair_json('{"a": [1, 2, ')
    # Our regex will turn [1, 2, ] into [1, 2], so the stack will append ']' giving [1, 2, ] which then gets fixed to [1, 2]
    assert repaired == '{"a": [1, 2]}'

def test_repair_json_complex_truncation():
    raw = """{
      "files": [
        {
          "path": "test.py",
          "new_content": "def foo():\\n    return"
    """
    repaired = repair_json(raw)
    assert repaired == """{
      "files": [
        {
          "path": "test.py",
          "new_content": "def foo():\\n    return"
    }]}"""
