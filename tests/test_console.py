import copy
import json
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from spark_console.app import create_app
from spark_console.controller import Controller, Conflict, json_stream
from spark_console.metrics import counters, RateTracker, memory_info, number


DRIVER = r'''
import json, sys, time, uuid
from pathlib import Path
root = Path(__file__).parent
ids = ['4b','7b','27b','47b']
action = sys.argv[1]
def save(key, value):
    p = root/key/'state.json'
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(p)
def load(key):
    p = root/key/'state.json'
    return json.loads(p.read_text()) if p.exists() else {'state':'stopped'}
if action == 'status':
    for key in ids:
        state = load(key)
        live = state['state'] in ('starting','ready')
        print(json.dumps(dict(selection=key,running=live,state=state['state'] if live else 'not_running',runtime=str(root/key))))
    print(json.dumps(dict(selection='dual',running=False)))
else:
    key = sys.argv[2]
    if '--dry-run' in sys.argv:
        print(json.dumps({'commands':[['fake-engine','--port',str(24100+ids.index(key)), '--alias',key+'-model']]}))
    else:
        with (root/'calls.txt').open('a') as f: f.write(action+' '+key+'\n')
        if action == 'stop':
            save(key,dict(state='stopped'))
        else:
            owner = uuid.uuid4().hex
            save(key,dict(state='starting',owner=owner,time=time.time()))
            delay = root/'delay'
            time.sleep(float(delay.read_text()) if delay.exists() else .05)
            if (root/'fail').exists():
                save(key,dict(state='failed',error='Simulated startup failure'))
                print('Simulated startup failure',file=sys.stderr)
                sys.exit(1)
            save(key,dict(state='ready',owner=owner,time=time.time()))
'''


def vllm_metrics(running=0, waiting=0):
    return f'''vllm:prompt_tokens_total{{model_name="x"}} 120
vllm:generation_tokens_total{{model_name="x"}} 60
vllm:request_success_total{{finished_reason="stop"}} 2
vllm:request_success_total{{finished_reason="length"}} 1
vllm:num_requests_running {running}
vllm:num_requests_waiting {waiting}
vllm:time_to_first_token_seconds_sum 0.3
vllm:time_to_first_token_seconds_count 3
vllm:e2e_request_latency_seconds_sum 6
vllm:e2e_request_latency_seconds_count 3
vllm:spec_decode_num_draft_tokens_total 10
vllm:spec_decode_num_accepted_tokens_total 7
'''


class MetricsTests(unittest.TestCase):
    def test_vllm_labels_and_averages(self):
        m = counters(vllm_metrics(), 'vLLM')
        self.assertEqual(m['requests'], 3)
        self.assertAlmostEqual(m['ttft_ms'], 100)
        self.assertEqual(m['latency_ms'], 2000)
        self.assertEqual(m['acceptance'], .7)

    def test_llama_and_missing_metrics(self):
        m = counters('llamacpp:tokens_predicted_total 100\nllamacpp:prompt_tokens_total 400\nllamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n', 'llama.cpp')
        self.assertEqual(m['output_tokens'], 100)
        self.assertEqual(m['input_tokens'], 400)
        self.assertIsNone(m['ttft_ms'])
        self.assertIsNone(m['requests'])
        self.assertIsNone(counters('', 'vLLM')['output_tokens'])
        self.assertIsNone(number('[N/A]'))

    def test_rates_reset_and_first_sample(self):
        rates = RateTracker()
        self.assertIsNone(rates.update('4b','a',1,{'output_tokens':100})['output_tps'])
        self.assertEqual(rates.update('4b','a',3,{'output_tokens':200})['output_tps'], 50)
        self.assertIsNone(rates.update('4b','a',5,{'output_tokens':10})['output_tps'])
        self.assertIsNone(rates.update('4b','b',7,{'output_tokens':110})['output_tps'])
        self.assertIsNone(rates.update('4b','b',9,{'output_tokens':None})['output_tps'])

    def test_memory_and_json_stream(self):
        m = memory_info('MemTotal: 2097152 kB\nMemAvailable: 1048576 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n')
        self.assertEqual(m['available_gib'], 1)
        self.assertEqual(m['used_gib'], 1)
        self.assertIsNone(memory_info('')['available_gib'])
        self.assertEqual(len(json_stream('{"x":1}\n {"x":2}\n')), 2)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        # Tests execute real wrapper subprocesses, but GPU and port availability
        # are injected so they cannot touch a real model or require a GPU host.
        self.socket_patch = patch('spark_console.controller.port_available', return_value=True)
        self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        bundle = self.root/'bundle'
        bundle.mkdir()
        bundle.chmod(0o755)
        (bundle/'driver.py').write_text(DRIVER)
        for key in ('4b','7b','27b','47b'):
            (bundle/key).mkdir()
            (bundle/key).chmod(0o775)
        for action in ('start','stop','status','check'):
            (bundle/f'{action}.sh').write_text(f'#!/bin/bash\nexec {shlex.quote(sys.executable)} {shlex.quote(str(bundle / "driver.py"))} {action} "$@"\n')
            (bundle/f'{action}.sh').chmod(0o755)
        self.ctrl = Controller(bundle, self.root/'data')
        self.bundle = bundle
        self.host = dict(hostname='test',user='test',memory=dict(total_gib=121,available_gib=99,used_gib=22,swap_used_gib=0),
                         gpu=dict(name='GB10',utilization=0,temperature=40,power=10),processes=[],foreign_processes=[],gpu_error=None)
        self.ctrl.hardware = lambda: copy.deepcopy(self.host)
        self.requests_running = 0
        self.http_calls = []

        def api(request):
            self.http_calls.append(request)
            key = ['4b','7b','27b','47b'][request.url.port-24100]
            if request.url.path == '/health': return httpx.Response(200,json={'status':'ok'})
            if request.url.path == '/v1/models': return httpx.Response(200,json={'data':[{'id':key+'-model'}]})
            if request.url.path == '/metrics': return httpx.Response(200,text=vllm_metrics(self.requests_running))
            if request.url.path == '/v1/chat/completions': return httpx.Response(200,json={'choices':[{'message':{'content':'A short reply.'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':10,'completion_tokens':8,'total_tokens':18}})
            return httpx.Response(404)
        self.ctrl.http.close()
        self.ctrl.http = httpx.Client(transport=httpx.MockTransport(api))
        self.ctrl.refresh()

    def tearDown(self):
        if self.ctrl.job and self.ctrl.job['state'] == 'running':
            self.ctrl.cancel_job()
            self.wait_job()
        self.ctrl.http.close()
        self.ctrl.operation_lock_path.unlink(missing_ok=True)
        self.ctrl.instance_lock_path.unlink(missing_ok=True)
        self.tmp.cleanup()

    def wait_job(self):
        until = time.monotonic()+8
        while time.monotonic() < until:
            if self.ctrl.job and self.ctrl.job['finished_at'] is not None and not self.ctrl.gate.locked():
                return dict(self.ctrl.job)
            time.sleep(.02)
        self.fail('Job did not finish')

    def deploy(self, key='4b'):
        self.ctrl.submit(key, 'deploy')
        job = self.wait_job()
        self.assertEqual(job['state'],'succeeded',job)

    def calls(self):
        p = self.bundle/'calls.txt'
        return p.read_text().splitlines() if p.exists() else []

    def test_discovery_is_read_only(self):
        self.assertEqual(len(self.ctrl.catalog),4)
        self.assertTrue(all(m['installed'] for m in self.ctrl.catalog))
        self.assertEqual(self.calls(),[])

    def test_access_token_is_private_and_persistent(self):
        token_path = self.root/'data'/'access-token'
        self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)
        token = self.ctrl.access_token
        second = Controller(self.bundle, self.root/'data')
        try:
            self.assertEqual(second.access_token, token)
        finally:
            second.http.close()

    def test_switch_stops_before_starting(self):
        self.deploy()
        self.ctrl.submit('7b','switch')
        job = self.wait_job()
        self.assertEqual(job['state'],'succeeded',job)
        self.assertEqual(self.calls(),['start 4b','stop 4b','start 7b'])
        statuses = self.ctrl.statuses()
        self.assertFalse(statuses['4b']['running'])
        self.assertTrue(statuses['7b']['running'])

    def test_deploy_does_not_silently_replace_existing_model(self):
        self.deploy()
        with self.assertRaisesRegex(Conflict,'Use Switch'):
            self.ctrl.submit('7b','deploy')
        self.assertEqual(self.calls(),['start 4b'])

    def test_foreign_gpu_blocks_without_stopping_anything(self):
        self.host['processes']=[dict(pid=123,user='other-user',memory_mib=7000,managed=False)]
        with self.assertRaisesRegex(Conflict,'other-user'):
            self.ctrl.submit('27b','deploy')
        self.assertEqual(self.calls(),[])

    def test_memory_and_telemetry_fail_closed(self):
        self.host['memory']['available_gib']=31
        with self.assertRaisesRegex(Conflict,'32 GiB'):
            self.ctrl.submit('4b','deploy')
        self.host['memory']['available_gib']=95
        with self.assertRaisesRegex(Conflict,'96 GiB'):
            self.ctrl.submit('27b','deploy')
        self.host['memory']['available_gib']=99
        self.host['memory']['swap_used_gib']=3
        with self.assertRaisesRegex(Conflict,'swap use'):
            self.ctrl.submit('4b','deploy')
        self.host['memory']['swap_used_gib']=0
        self.host['gpu_error']='unavailable'
        with self.assertRaisesRegex(Conflict,'Cannot verify GPU'):
            self.ctrl.submit('4b','deploy')
        self.assertEqual(self.calls(),[])

    def test_foreign_workload_appearing_during_start_stops_only_target(self):
        (self.bundle/'delay').write_text('4')
        self.ctrl.submit('4b','deploy')
        until = time.monotonic()+3
        while 'start 4b' not in self.calls() and time.monotonic()<until: time.sleep(.02)
        self.host['processes']=[dict(pid=123,user='other-user',name='foreign',memory_mib=7000,managed=False)]
        job = self.wait_job()
        self.assertEqual(job['state'],'failed')
        self.assertIn('other workload',job['error'])
        self.assertEqual(self.calls(),['start 4b','stop 4b'])

    def test_switch_waits_for_inflight_requests(self):
        self.deploy()
        self.requests_running=1
        with self.assertRaisesRegex(Conflict,'requests in progress'):
            self.ctrl.submit('7b','switch')
        self.assertEqual(self.calls(),['start 4b'])

    def test_duplicate_operations_and_cancel_loading(self):
        (self.bundle/'delay').write_text('4')
        self.ctrl.submit('4b','deploy')
        with self.assertRaisesRegex(Conflict,'already running'):
            self.ctrl.submit('27b','deploy')
        until = time.monotonic()+3
        while 'start 4b' not in self.calls() and time.monotonic()<until: time.sleep(.02)
        self.ctrl.cancel_job()
        job = self.wait_job()
        self.assertIn('cancelled',job['error'])
        self.assertFalse(self.ctrl.statuses()['4b']['running'])
        self.assertEqual(self.calls(),['start 4b','stop 4b'])

    def test_failed_start_is_reported_without_retry(self):
        (self.bundle/'fail').write_text('1')
        self.ctrl.submit('27b','deploy')
        job = self.wait_job()
        self.assertEqual(job['state'],'failed')
        self.assertIn('Simulated startup failure',job['error'])
        self.assertEqual(self.calls(),['start 27b'])

    def test_request_stats_and_prompt_not_persisted(self):
        self.deploy()
        answer = self.ctrl.try_request('4b','PRIVATE_TEST_PROMPT',64)
        self.assertEqual(answer['usage']['completion_tokens'],8)
        self.assertGreater(answer['request_output_tps'],0)
        self.assertNotIn('PRIVATE_TEST_PROMPT',json.dumps(self.ctrl.events()))

    def test_http_origin_token_and_input_validation(self):
        with TestClient(create_app(self.ctrl)) as client:
            self.assertEqual(client.get('/api/state').status_code,401)
            self.assertEqual(client.post('/api/session',json={'token':'x'*32}).status_code,403)
            self.assertEqual(client.post('/api/session',json={'token':self.ctrl.access_token},
                                         headers={'origin':'http://evil.invalid'}).status_code,403)
            self.assertEqual(client.post('/api/session',json={'token':self.ctrl.access_token}).status_code,200)
            state = client.get('/api/state')
            self.assertEqual(state.status_code,200)
            self.assertNotIn('csrf_token',state.json())
            body = {'model':'4b','action':'deploy'}
            headers={'origin':'http://evil.invalid'}
            self.assertEqual(client.post('/api/operations',json=body,headers=headers).status_code,403)
            headers={'origin':'http://testserver'}
            self.assertEqual(client.post('/api/operations',json={'model':'../../bad','action':'deploy'},headers=headers).status_code,422)
            self.assertEqual(client.post('/api/operations',json=body,headers=headers).status_code,202)
            self.assertEqual(self.wait_job()['state'],'succeeded')
            self.assertEqual(client.post('/api/test',json={'model':'4b','prompt':'hi','max_tokens':99999},headers=headers).status_code,422)
            self.assertEqual(client.post('/api/test',json={'model':'4b','prompt':'hi','max_tokens':32},headers=headers).status_code,200)
            self.assertEqual(client.get('/api/logs/../private').status_code,404)


if __name__ == '__main__':
    unittest.main()
