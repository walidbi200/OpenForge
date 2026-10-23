import pytest
import json
import httpx
from openforge.services.llm import RetryManager, LLMProviderConfig
from openforge.config import Settings

class MockResponse:
    def __init__(self, lines, status_code=200):
        self.lines = lines
        self.status_code = status_code
        self.request = httpx.Request("POST", "http://fake")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=self)

    def iter_lines(self):
        for line in self.lines:
            if isinstance(line, Exception):
                raise line
            yield line
            
    def read(self):
        pass

class MockClient:
    def __init__(self, response):
        self.response = response
        
    def stream(self, *args, **kwargs):
        class ContextManager:
            def __init__(self, resp):
                self.resp = resp
            def __enter__(self):
                return self.resp
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return ContextManager(self.response)
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

def test_streaming_partial_chunks(monkeypatch):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        "",
        "data: " + json.dumps({"choices": [{"delta": {"content": " world!"}}]}),
        "data: [DONE]"
    ]
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: MockClient(MockResponse(lines)))
    
    settings = Settings(llm_provider="dahl", dahl_api_key="fake")
    manager = RetryManager(settings, timeout=30.0)
    
    result = manager.execute([{"role": "user", "content": "test"}], {}, None)
    assert result == "Hello world!"

def test_streaming_malformed_sse(monkeypatch):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "Valid"}}]}),
        "data: {broken_json",
        "data: " + json.dumps({"choices": [{"delta": {"content": "Still valid"}}]}),
        "data: [DONE]"
    ]
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: MockClient(MockResponse(lines)))
    
    settings = Settings(llm_provider="dahl", dahl_api_key="fake")
    manager = RetryManager(settings, timeout=30.0)
    
    result = manager.execute([{"role": "user", "content": "test"}], {}, None)
    assert result == "ValidStill valid"

def test_streaming_connection_reset(monkeypatch):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "First try"}}]}),
        httpx.ReadError("Connection reset by peer")
    ]
    
    lines_retry = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "Second try success"}}]}),
        "data: [DONE]"
    ]
    
    call_count = 0
    def mock_client_factory(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockClient(MockResponse(lines))
        return MockClient(MockResponse(lines_retry))
        
    monkeypatch.setattr(httpx, "Client", mock_client_factory)
    
    settings = Settings(llm_provider="dahl", dahl_api_key="fake")
    manager = RetryManager(settings, timeout=30.0)
    
    # We should retry and get the second result
    result = manager.execute([{"role": "user", "content": "test"}], {}, None)
    assert result == "Second try success"
    assert call_count == 2
