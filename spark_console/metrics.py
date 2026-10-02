"""Read-only telemetry, adapted from workstation-serving-stack/controller/metrics.py.

Missing counters remain null. GB10 reports N/A for dedicated VRAM, so the console
uses system MemAvailable and labels per-process GPU memory separately.
"""
import math
import re
from prometheus_client.parser import text_string_to_metric_families


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def parse_prom(text):
    result = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            value = number(sample.value)
            if value is not None:
                result[sample.name] = result.get(sample.name, 0) + value
    return result


def pick(raw, *names):
    for name in names:
        if name in raw:
            return raw[name]
    return None


def counters(text, engine):
    raw = parse_prom(text)
    if engine == 'llama.cpp':
        return {
            'input_tokens': pick(raw, 'llamacpp:prompt_tokens_total', 'llamacpp:tokens_evaluated_total'),
            'output_tokens': pick(raw, 'llamacpp:tokens_predicted_total'),
            'requests': pick(raw, 'llamacpp:requests_success_total', 'llamacpp:requests_completed_total'),
            'running': pick(raw, 'llamacpp:requests_processing'),
            'waiting': pick(raw, 'llamacpp:requests_deferred'),
            'ttft_ms': None, 'latency_ms': None, 'acceptance': None,
        }
    count = raw.get('vllm:time_to_first_token_seconds_count', 0)
    req_count = raw.get('vllm:e2e_request_latency_seconds_count', 0)
    proposed = raw.get('vllm:spec_decode_num_draft_tokens_total', 0)
    return {
        'input_tokens': pick(raw, 'vllm:prompt_tokens_total'),
        'output_tokens': pick(raw, 'vllm:generation_tokens_total'),
        'requests': pick(raw, 'vllm:request_success_total', 'vllm:e2e_request_latency_seconds_count'),
        'running': pick(raw, 'vllm:num_requests_running'),
        'waiting': pick(raw, 'vllm:num_requests_waiting'),
        'ttft_ms': raw.get('vllm:time_to_first_token_seconds_sum', 0) / count * 1000 if count else None,
        'latency_ms': raw.get('vllm:e2e_request_latency_seconds_sum', 0) / req_count * 1000 if req_count else None,
        'acceptance': raw.get('vllm:spec_decode_num_accepted_tokens_total', 0) / proposed if proposed else None,
    }


class RateTracker:
    def __init__(self):
        self.previous = {}

    def update(self, model, session, now, values):
        previous = self.previous.get(model)
        self.previous[model] = (session, now, values.copy())
        result = dict(values)
        for key in ('input_tokens', 'output_tokens'):
            rate = None
            if previous and previous[0] == session and now > previous[1]:
                old, new = previous[2].get(key), values.get(key)
                if old is not None and new is not None and new >= old:
                    rate = (new - old) / (now - previous[1])
            result[key.replace('_tokens', '_tps')] = rate
        return result


def memory_info(text):
    values = {m[0]: int(m[1]) * 1024 for m in re.findall(r'^(\w+):\s+(\d+)\s+kB', text, re.M)}
    total, available = values.get('MemTotal'), values.get('MemAvailable')
    return {
        'total_gib': total / 1024**3 if total is not None else None,
        'available_gib': available / 1024**3 if available is not None else None,
        'used_gib': (total - available) / 1024**3 if total is not None and available is not None else None,
        'swap_used_gib': (values.get('SwapTotal', 0) - values.get('SwapFree', 0)) / 1024**3,
    }
