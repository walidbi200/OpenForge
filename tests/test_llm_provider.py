import json
from unittest.mock import MagicMock, patch
import httpx
import pytest

from openforge.config import Settings
from openforge.services.llm import LLMProviderConfig, call_llm, RetryManager

def _settings(**kwargs) -> Settings:
    defaults = {
        "agentrouter_api_key": None,
        "agentrouter_model": "gpt-5.6-sol",
        "openrouter_api_key": None,
        "dahl_api_key": None,
        "llm_provider": "openrouter",
        "llm_fallback": None,
        "api_key": None,
        "api_base": None,
        "model": None,
        "openrouter_model": "anthropic/claude-sonnet-4.6",
        "dahl_model": "moonshotai/Kimi-K2.6",
    }
    defaults.update(kwargs)
    return Settings(**defaults)

class TestLLMProviderConfig:
    def test_agentrouter_defaults(self):
        s = _settings(
            llm_provider="agentrouter", agentrouter_api_key="agentrouter-test"
        )
        cfg = LLMProviderConfig.from_settings(s)
        assert cfg.provider == "agentrouter"
        assert cfg.base_url == "https://agentrouter.org/v1"
        assert cfg.api_key == "agentrouter-test"
        assert cfg.model == "gpt-5.6-sol"

    def test_dahl_defaults(self):
        s = _settings(llm_provider="dahl", dahl_api_key="dahl-test")
        cfg = LLMProviderConfig.from_settings(s)
        assert cfg.provider == "dahl"
        assert cfg.api_key == "dahl-test"
        assert cfg.model == "moonshotai/Kimi-K2.6"

class MockResponse:
    def __init__(self, content, status=200):
        self.status_code = status
        self.content = content
        self.request = MagicMock()
    
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=self.request, response=self)
            
    def read(self):
        pass
        
    def iter_lines(self):
        yield f'data: {{"choices": [{{"delta": {{"content": "{self.content}"}}}}]}}'
        yield 'data: [DONE]'
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

class MockClient:
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
        self.last_url = None
        self.last_kwargs = None
        
    def stream(self, method, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

class TestCallLLM:
    def test_agentrouter_uses_opencode_transport(self):
        s = _settings(
            llm_provider="agentrouter", agentrouter_api_key="agentrouter-test"
        )
        output = "\n".join(
            [
                json.dumps({"type": "text", "part": {"text": "Hello from AgentRouter!"}}),
                json.dumps(
                    {
                        "type": "step_finish",
                        "part": {
                            "tokens": {"input": 10, "output": 4, "total": 14}
                        },
                    }
                ),
            ]
        )
        completed = MagicMock(returncode=0, stdout=output, stderr="")
        with patch("openforge.services.llm.subprocess.run", return_value=completed) as run:
            result = call_llm("say hello", s, timeout=10.0)
        assert result == "Hello from AgentRouter!"
        args = run.call_args.args[0]
        assert args[:3] == ["opencode", "run", "--pure"]
        assert "agentrouter/gpt-5.6-sol" in args
        assert run.call_args.kwargs["input"] == "say hello"
        env = run.call_args.kwargs["env"]
        assert env["AGENTROUTER_API_KEY"] == "agentrouter-test"
        assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true"
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        options = config["provider"]["agentrouter"]["options"]
        assert options["apiKey"] == "{env:AGENTROUTER_API_KEY}"
        assert options["headers"]["x-app-name"] == "claude-code"

    def test_dahl_success(self):
        s = _settings(llm_provider="dahl", dahl_api_key="dahl-test")
        client = MockClient([MockResponse("Hello from Dahl!")])
        with patch("openforge.services.llm.httpx.Client", return_value=client):
            result = call_llm("say hello", s, timeout=10.0)
        assert result == "Hello from Dahl!"

    def test_failover_trigger(self):
        s = _settings(llm_provider="openrouter", openrouter_api_key="sk-or-test", llm_fallback="dahl", dahl_api_key="test")
        client = MockClient([
            MockResponse("", 403), # OpenRouter quota fails, triggers failover
            MockResponse("Fallback worked!")
        ])
        with patch("openforge.services.llm.httpx.Client", return_value=client):
            result = call_llm("say hello", s, timeout=10.0)
        assert result == "Fallback worked!"
