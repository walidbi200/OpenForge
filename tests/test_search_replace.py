from openforge.agents.coder import CodingAgent
from openforge.config import Settings
import pytest
import json

def test_search_replace():
    agent = CodingAgent(Settings(agentrouter_api_key="test"))
    
    current_files = {
        "file1.py": "def foo():\n    print('old')\n    return False\n"
    }
    
    # Test 1: Successful replace
    raw = json.dumps({
        "files": [
            {
                "path": "file1.py",
                "action": "modify",
                "search": "def foo():\n    print('old')\n",
                "replace": "def foo():\n    print('new')\n"
            }
        ]
    })
    edits, _ = agent._parse_response(raw, ["file1.py"], current_files)
    assert len(edits) == 1
    assert "print('new')" in edits[0].new_content
    
    # Test 2: Multiple replacements on the same file
    current_files2 = {
        "file1.py": "def foo():\n    print('old')\n    return False\n"
    }
    raw2 = json.dumps({
        "files": [
            {
                "path": "file1.py",
                "action": "modify",
                "search": "print('old')\n",
                "replace": "print('new')\n"
            },
            {
                "path": "file1.py",
                "action": "modify",
                "search": "return False\n",
                "replace": "return True\n"
            }
        ]
    })
    edits, _ = agent._parse_response(raw2, ["file1.py"], current_files2)
    assert len(edits) == 1
    assert "print('new')" in edits[0].new_content
    assert "return True" in edits[0].new_content

    # Test 3: Missing search block (action: modify without search)
    raw3 = json.dumps({
        "files": [
            {
                "path": "file1.py",
                "action": "modify",
                "replace": "something"
            }
        ]
    })
    edits, _ = agent._parse_response(raw3, ["file1.py"], {"file1.py": "test"})
    assert len(edits) == 1
    assert edits[0].new_content == ""
        
    # Test 4: Invalid replacement (search block not found)
    raw4 = json.dumps({
        "files": [
            {
                "path": "file1.py",
                "action": "modify",
                "search": "def bar():\n",
                "replace": "def baz():\n"
            }
        ]
    })
    edits, diagnostics = agent._parse_response(raw4, ["file1.py"], {"file1.py": "def foo():\n    print('old')\n    return False\n"})
    assert len(edits) == 0
    assert len(diagnostics["rejected_edits"]) == 1
