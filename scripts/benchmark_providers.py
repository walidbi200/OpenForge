import dataclasses
import time
import json
from pathlib import Path
from openforge.config import Settings
from openforge.services.llm import call_llm

PROMPT = "Write a python function to compute the 100th Fibonacci number. Only output the code."

def run_benchmark(provider: str):
    settings = Settings.load()
    settings = dataclasses.replace(settings, llm_provider=provider, llm_fallback=None)
    
    metrics_captured = {}
    def metric_cb(m):
        metrics_captured.update(m)
        
    start = time.time()
    try:
        call_llm(PROMPT, settings, timeout=30.0, metrics_callback=metric_cb)
    except Exception as e:
        print(f"{provider} failed: {e}")
        
    return metrics_captured

def main():
    print("Benchmarking OpenRouter...")
    or_metrics = run_benchmark("openrouter")
    
    print("Benchmarking Dahl...")
    dahl_metrics = run_benchmark("dahl")
    
    print("\n=== Provider Benchmark ===")
    print(f"{'Metric':<20}\t{'OpenRouter':<25}\tDahl")
    print("-" * 70)
    keys = [
        "provider_used", "fallback_provider", "model", "request_id", 
        "total_tokens", "request_duration", "time_to_first_token", 
        "generation_duration", "completion_rate", "retry_count"
    ]
    for k in keys:
        v_or = or_metrics.get(k, 'N/A')
        v_dahl = dahl_metrics.get(k, 'N/A')
        print(f"{k[:20]:<20}\t{str(v_or)[:25]:<25}\t{str(v_dahl)[:25]}")
        
    Path("provider_benchmark.json").write_text(json.dumps({
        "openrouter": or_metrics,
        "dahl": dahl_metrics
    }, indent=2))

if __name__ == "__main__":
    main()
