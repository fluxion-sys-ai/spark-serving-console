import csv
import errno
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import secrets
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager

import httpx

from .metrics import RateTracker, counters, memory_info, number

CATALOG = [
    dict(id='4b', name='Qwen3.5-4B', quantization='NVFP4', engine='vLLM', version='0.24', kvcached=True, speculative='MTP · 3 draft tokens', min_available_gib=32),
    dict(id='7b', name='Qwen2.5-VL-7B', quantization='NVFP4', engine='vLLM', version='0.24', kvcached=True, speculative='Off', min_available_gib=40),
    dict(id='27b', name='Qwen3.8-27B', quantization='NVFP4', engine='vLLM', version='0.28', kvcached=False, speculative='DSpark · 7 draft tokens', min_available_gib=96),
    dict(id='47b', name='BigQwen2.5-Echo-47B', quantization='Q4_K_M', engine='llama.cpp', version='', kvcached=False, speculative='Off', min_available_gib=72),
]


class Conflict(RuntimeError):
    pass


def json_stream(text):
    values, decoder = [], json.JSONDecoder()
    while text.strip():
        text = text.lstrip()
        value, end = decoder.raw_decode(text)
        values.append(value)
        text = text[end:]
    return values


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default if default is not None else {}


def tail(path, limit=24000):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - limit))
            return stream.read(limit).decode(errors='replace')
    except OSError:
        return ''


def run(argv, timeout=10, env=None):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f'Exit {result.returncode}')[-2000:].strip())
    return result.stdout


def port_available(port):
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
        return True
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise Conflict('Cannot check the target API port: ' + str(exc)) from exc
        return False


class Controller:
    def __init__(self, bundle, data):
        self.bundle, self.data = Path(bundle).resolve(), Path(data).resolve()
        self._validate_owned_directory(self.bundle, 'model launcher directory')
        self.scripts = {}
        for action in ('start', 'stop', 'status', 'check'):
            path = self.bundle / f'{action}.sh'
            self._validate_owned_file(path, f'{action} launcher')
            self.scripts[action] = path
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data, 0o700)
        self._validate_owned_directory(self.data, 'console data directory', private=True)
        runtime_root = os.environ.get('XDG_RUNTIME_DIR')
        runtime_base = Path(runtime_root) if runtime_root else None
        if runtime_base is None or not runtime_base.is_dir() or runtime_base.stat().st_uid != os.getuid():
            control_base = Path(tempfile.gettempdir()) / f'spark-serving-console-{os.getuid()}'
        else:
            control_base = runtime_base / 'spark-serving-console'
        control_base.mkdir(mode=0o700, exist_ok=True)
        os.chmod(control_base, 0o700)
        self._validate_owned_directory(control_base, 'console control directory', private=True)
        key = hashlib.sha256(str(self.bundle).encode()).hexdigest()[:16]
        self.instance_lock_path = control_base / f'{key}.instance.lock'
        self.operation_lock_path = control_base / f'{key}.operation.lock'
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.gate = threading.Lock()
        self.cancel = threading.Event()
        self.access_token = self._load_access_token()
        self.http = httpx.Client(timeout=3, trust_env=False)
        self.rates = RateTracker()
        self.catalog = []
        self.models = []
        self.host = {}
        self.history = deque(maxlen=200)
        self.job = None
        self.ts = 0
        self.error = None
        self.db_path = self.data / 'events.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, level TEXT, message TEXT)')
        self.event('info', 'Console connected. Existing model services are preserved.')
        self.discover()

    @staticmethod
    def _validate_owned_directory(path, label, private=False, allow_group_write=False):
        try:
            stat = Path(path).stat()
        except OSError as exc:
            raise RuntimeError(f'{label.capitalize()} is unavailable: {path}') from exc
        if not Path(path).is_dir() or stat.st_uid != os.getuid():
            raise RuntimeError(f'The console must run as the owner of the {label}: {path}')
        forbidden = 0o077 if private else (0o002 if allow_group_write else 0o022)
        if stat.st_mode & forbidden:
            raise RuntimeError(f'Unsafe permissions on {label}: {path}')

    @staticmethod
    def _validate_owned_file(path, label):
        path = Path(path)
        try:
            stat = path.lstat()
        except OSError as exc:
            raise RuntimeError(f'{label.capitalize()} not found: {path}') from exc
        if path.is_symlink() or not path.is_file() or stat.st_uid != os.getuid():
            raise RuntimeError(f'{label.capitalize()} must be a regular file owned by this account: {path}')
        if stat.st_mode & 0o022:
            raise RuntimeError(f'Unsafe permissions on {label}: {path}')

    def _load_access_token(self):
        path = self.data / 'access-token'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._validate_owned_file(path, 'console access token')
            if path.stat().st_mode & 0o077:
                raise RuntimeError(f'Unsafe permissions on console access token: {path}')
            token = path.read_text().strip()
            if len(token) < 32:
                raise RuntimeError(f'Console access token is invalid: {path}')
            return token
        token = secrets.token_urlsafe(32)
        with os.fdopen(fd, 'w') as stream:
            stream.write(token + '\n')
        return token

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def event(self, level, message):
        with self.db() as db:
            db.execute('INSERT INTO events(ts,level,message) VALUES(?,?,?)', (time.time(), level, message))
            db.execute('DELETE FROM events WHERE id < (SELECT MAX(id)-500 FROM events)')

    def events(self):
        with self.db() as db:
            return [dict(id=r[0], ts=r[1], level=r[2], message=r[3]) for r in
                    db.execute('SELECT id,ts,level,message FROM events ORDER BY id DESC LIMIT 30')]

    def script(self, action, model=None, extra=()):
        if action not in ('start', 'stop', 'status', 'check') or (model and model not in ('4b', '7b', '27b', '47b')):
            raise ValueError('Unsupported action or model')
        return ['bash', str(self.scripts[action]), *([model] if model else []), *extra]

    def statuses(self):
        return {v['selection']: v for v in json_stream(run(self.script('status'), timeout=10))}

    def discover(self):
        status = self.statuses()
        for item in CATALOG:
            item = dict(item)
            item.update(installed=False, port=None, alias=None, runtime=None)
            try:
                runtime = status.get(item['id'], {}).get('runtime')
                if not runtime:
                    raise RuntimeError('Launcher status did not report a runtime directory.')
                runtime = Path(runtime).resolve()
                self._validate_owned_directory(runtime, f"{item['id']} runtime directory", allow_group_write=True)
                item['runtime'] = str(runtime)
                preview = json.loads(run(self.script('start', item['id'], ('--dry-run',)), timeout=10))
                command = preview['commands'][0]
                item['port'] = int(command[command.index('--port') + 1])
                alias_flag = '--served-model-name' if '--served-model-name' in command else '--alias'
                item['alias'] = command[command.index(alias_flag) + 1]
                item['installed'] = True
            except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.TimeoutExpired) as exc:
                item['installation_error'] = str(exc)
            self.catalog.append(item)

    def model(self, model_id):
        return next(m for m in self.catalog if m['id'] == model_id)

    def hardware(self):
        mem = memory_info(Path('/proc/meminfo').read_text())
        values = dict(hostname=socket.gethostname(), user=pwd.getpwuid(os.getuid()).pw_name, memory=mem,
                      gpu=None, processes=[], gpu_error=None, load_average=list(os.getloadavg()))
        try:
            text = run(['nvidia-smi', '--query-gpu=name,utilization.gpu,temperature.gpu,power.draw',
                        '--format=csv,noheader,nounits'], timeout=6)
            rows = list(csv.reader(io.StringIO(text)))
            if not rows or len(rows[0]) < 4:
                raise RuntimeError('GPU telemetry returned no device')
            r = [v.strip() for v in rows[0]]
            values['gpu'] = dict(name=r[0], utilization=number(r[1]), temperature=number(r[2]), power=number(r[3]))
            text = run(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory',
                        '--format=csv,noheader,nounits'], timeout=6)
            for r in csv.reader(io.StringIO(text)):
                if len(r) < 3:
                    continue
                pid = int(r[0])
                try:
                    user = pwd.getpwuid(Path(f'/proc/{pid}').stat().st_uid).pw_name
                except (OSError, KeyError):
                    user = 'unknown'
                values['processes'].append(dict(pid=pid, name=r[1].strip(), user=user,
                                                memory_mib=number(r[2]), managed=False))
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            values['gpu_error'] = str(exc)
        return values

    @staticmethod
    def marker_owned(pid, state):
        """Match a process to the current account and a launcher's random owner marker."""
        try:
            p = Path(f'/proc/{pid}')
            if p.stat().st_uid != os.getuid() or not state.get('owner'):
                return False
            marker = ('SPARK_PROFILE_OWNER=' + state['owner']).encode()
            return marker in (p / 'environ').read_bytes().split(b'\0')
        except (OSError, ValueError, KeyError):
            return False

    @classmethod
    def belongs(cls, pid, state):
        """Attribute only a launcher's exact ownership marker and process group."""
        try:
            server = state.get('server') or {}
            if server.get('boot') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
                return False
            p = Path(f'/proc/{pid}')
            group = int((p / 'stat').read_text().rsplit(')', 1)[1].split()[2])
            return cls.marker_owned(pid, state) and group == server.get('pid')
        except (OSError, ValueError, KeyError):
            return False

    def inspect(self, status, host, scrape=True):
        models = []
        for item in self.catalog:
            m = dict(item)
            s = status.get(m['id'], {})
            state = read_json(Path(m['runtime']) / 'state.json') if m['runtime'] else {}
            m.update(running=bool(s.get('running')), status=s.get('state', 'not_running'), ready=False,
                     metrics=None, metrics_error=None, error=state.get('error'),
                     started_at=state.get('time') if s.get('running') else None,
                     session=state.get('owner'), gpu_memory_mib=None)
            if m['running']:
                for process in host['processes']:
                    if self.belongs(process['pid'], state):
                        process.update(managed=True, model=m['id'])
                        if process['memory_mib'] is not None:
                            m['gpu_memory_mib'] = (m['gpu_memory_mib'] or 0) + process['memory_mib']
                if scrape and m['status'] == 'ready':
                    try:
                        base = f"http://127.0.0.1:{m['port']}"
                        health = self.http.get(base + '/health')
                        health.raise_for_status()
                        response = self.http.get(base + '/v1/models')
                        response.raise_for_status()
                        if m['alias'] not in {v['id'] for v in response.json()['data']}:
                            raise ValueError('Unexpected model at the configured port')
                        m['ready'] = True
                        response = self.http.get(base + '/metrics')
                        response.raise_for_status()
                        m['metrics'] = self.rates.update(m['id'], m['session'], time.monotonic(),
                                                        counters(response.text, m['engine']))
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        m['metrics_error'] = str(exc)
            elif state.get('state') == 'failed':
                m['status'] = 'failed'
            models.append(m)
        host['foreign_processes'] = [p for p in host['processes'] if not p['managed']]
        return models

    def refresh(self):
        # Startup jobs, the sampling loop and test requests can all request a
        # refresh. Never let an older sample overwrite a newer rate baseline.
        with self.refresh_lock:
            self._refresh()

    def _refresh(self):
        try:
            status, host = self.statuses(), self.hardware()
            models = self.inspect(status, host)
            now = time.time()
            with self.lock:
                self.models, self.host, self.ts, self.error = models, host, now, None
                self.history.append(dict(ts=now, gpu_util=(host['gpu'] or {}).get('utilization'),
                    ram_used=host['memory']['used_gib'], models={m['id']: dict(m['metrics'], session=m['session'])
                    for m in models if m['metrics'] is not None}))
        except Exception as exc:
            with self.lock:
                self.error = str(exc)

    def snapshot(self):
        with self.lock:
            return dict(ts=self.ts, error=self.error, host=self.host, models=self.models,
                        job=dict(self.job) if self.job else None, history=list(self.history),
                        events=self.events())

    def resource_guard(self, model_id, host, strict=True):
        required = self.model(model_id)['min_available_gib'] if strict else 12
        available = host['memory'].get('available_gib')
        if available is None or available < required:
            raise Conflict(f'{required} GiB of available unified memory is required before loading '
                           f"{self.model(model_id)['name']}; currently {available if available is not None else 'unknown'} GiB.")
        swap = host['memory'].get('swap_used_gib')
        if swap is None or swap > 2:
            raise Conflict('System swap use is above the 2 GiB startup limit. Stop memory-heavy work and let memory recover before loading a model.')

    def preflight(self, model_id, action):
        if model_id not in {m['id'] for m in self.catalog}:
            raise ValueError('Unknown model')
        if action not in ('deploy', 'switch', 'stop'):
            raise ValueError('Unknown action')
        status, host = self.statuses(), self.hardware()
        models = self.inspect(status, host, scrape=False)
        active = [m for m in models if m['running']]
        target = self.model(model_id)
        if action == 'stop':
            return active
        if not target['installed']:
            raise Conflict('This profile is not installed. Complete its existing setup script first.')
        if status.get('dual', {}).get('running'):
            raise Conflict('The existing dual-model service is running. Stop it with its original launcher before switching.')
        if host['gpu_error']:
            raise Conflict('Cannot verify GPU availability: ' + host['gpu_error'])
        if host['foreign_processes']:
            owners = ', '.join(f"{p['user']} (PID {p['pid']})" for p in host['foreign_processes'])
            raise Conflict('GPU occupied by another workload: ' + owners + '. That workload will not be stopped.')
        releasing_other = action == 'switch' and any(m['id'] != model_id for m in active)
        self.resource_guard(model_id, host, strict=not releasing_other)
        if action == 'deploy' and any(m['id'] != model_id for m in active):
            raise Conflict('Another model is running. Use Switch to stop it before loading this model.')
        # Refuse to interrupt existing inference. An explicitly chosen stop still
        # remains available if the operator wants to interrupt an in-flight request.
        for m in active:
            if m['id'] == model_id:
                continue
            try:
                response = self.http.get(f"http://127.0.0.1:{m['port']}/metrics")
                response.raise_for_status()
                stats = counters(response.text, m['engine'])
                if stats['running'] is None or stats['waiting'] is None:
                    raise Conflict('Cannot confirm that the active model is idle. Stop it explicitly before switching.')
                if stats['running'] > 0 or stats['waiting'] > 0:
                    raise Conflict('The active model has requests in progress. Wait for them to finish or stop it explicitly.')
            except httpx.HTTPError:
                raise Conflict('Cannot confirm that the active model is idle. Stop it explicitly before switching.')
        return active

    def submit(self, model_id, action):
        if not self.gate.acquire(blocking=False):
            raise Conflict('A model operation is already running.')
        try:
            self.preflight(model_id, action)
            self.cancel.clear()
            job = dict(id=secrets.token_hex(8), model=model_id, action=action, state='running',
                       stage='Preparing', started_at=time.time(), finished_at=None, error=None)
            with self.lock:
                self.job = job
            threading.Thread(target=self.worker, args=(job,), daemon=True).start()
            return dict(job)
        except BaseException:
            self.gate.release()
            raise

    def set_stage(self, stage):
        with self.lock:
            self.job['stage'] = stage

    def execute(self, action, model_id, log):
        self.set_stage(('Stopping ' if action == 'stop' else 'Loading ') + self.model(model_id)['name'])
        with log.open('ab') as output:
            proc = subprocess.Popen(self.script(action, model_id), stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + (1700 if action == 'start' else 120)
            next_gpu_check = 0
            try:
                while proc.poll() is None:
                    if action == 'start' and self.cancel.is_set():
                        run(self.script('stop', model_id), timeout=120)
                        raise Conflict('Model loading cancelled. Owned model processes stopped.')
                    if time.monotonic() > deadline:
                        if action == 'start':
                            run(self.script('stop', model_id), timeout=120)
                        raise RuntimeError('Model operation timed out; check the engine log.')
                    if action == 'start' and time.monotonic() >= next_gpu_check:
                        next_gpu_check = time.monotonic() + 2
                        hw = self.hardware()
                        if hw['gpu_error']:
                            run(self.script('stop', model_id), timeout=120)
                            raise Conflict('GPU monitoring failed during startup. This account\'s model start was stopped.')
                        state = read_json(Path(self.model(model_id)['runtime']) / 'state.json')
                        foreign = [p for p in hw['processes'] if not self.marker_owned(p['pid'], state)]
                        if foreign:
                            run(self.script('stop', model_id), timeout=120)
                            owners = ', '.join(f"{p['user']} (PID {p['pid']})" for p in foreign)
                            raise Conflict('Another GPU workload appeared during startup: ' + owners +
                                           ". This account's model start was stopped; the other workload was left untouched.")
                    time.sleep(.3)
                if proc.returncode:
                    raise RuntimeError(tail(log, 1800).strip() or f'Launcher exited with {proc.returncode}')
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()

    def wait_released(self, model_id):
        """Give child cleanup and the launcher's plain-bind TIME_WAIT check time."""
        port = self.model(model_id)['port']
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            hw = self.hardware()
            if hw['gpu_error']:
                raise Conflict('GPU availability check failed: ' + hw['gpu_error'])
            port_free = port_available(port) if port else True
            if not hw['processes'] and port_free:
                self.resource_guard(model_id, hw, strict=True)
                return
            if self.cancel.is_set():
                raise Conflict('Operation cancelled.')
            time.sleep(1)
        raise Conflict('GPU processes or the target port are still occupied; the next model was not started.')

    def worker(self, job):
        log = self.data / f"operation-{job['id']}.log"
        try:
            with self.operation_lock_path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                active = self.preflight(job['model'], job['action'])
                self.event('info', f"{job['action'].capitalize()}: {self.model(job['model'])['name']}")
                if job['action'] == 'stop':
                    self.execute('stop', job['model'], log)
                elif not any(m['id'] == job['model'] and m['status'] == 'ready' for m in active):
                    if job['action'] == 'switch':
                        for m in active:
                            self.execute('stop', m['id'], log)
                    if self.cancel.is_set():
                        raise Conflict('Operation cancelled before loading.')
                    self.set_stage('Waiting for GPU and port release')
                    self.wait_released(job['model'])
                    self.execute('start', job['model'], log)
                self.refresh()
                status = self.statuses().get(job['model'], {})
                if job['action'] == 'stop' and status.get('running'):
                    raise RuntimeError('Model processes are still running; check the engine log.')
                if job['action'] != 'stop':
                    model = next(m for m in self.models if m['id'] == job['model'])
                    if not model['ready']:
                        raise RuntimeError('Launcher finished but the expected model API is not healthy.')
                with self.lock:
                    job.update(state='succeeded', stage='Model stopped' if job['action'] == 'stop' else 'API ready')
                self.event('success', f"{self.model(job['model'])['name']}: {job['stage']}")
        except Exception as exc:
            with self.lock:
                job.update(state='failed', stage='Operation stopped', error=str(exc))
            self.event('error', str(exc)[:2000])
        finally:
            with self.lock:
                job['finished_at'] = time.time()
            self.gate.release()
            self.refresh()
            # Keep bounded operation logs, independent of protected engine logs.
            logs = sorted(self.data.glob('operation-*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in logs[30:]:
                old.unlink(missing_ok=True)

    def cancel_job(self):
        with self.lock:
            if not self.job or self.job['state'] != 'running':
                raise Conflict('No operation is running.')
            self.cancel.set()
            return {'message': 'Cancellation requested. Waiting for owned processes to stop.'}

    def logs(self, model_id):
        model = self.model(model_id)
        root = Path(model['runtime']) if model.get('runtime') else None
        result = {'engine': tail(root / 'logs/server.log') if root else '',
                  'supervisor': tail(root / 'logs/supervisor.log', 6000) if root else '', 'operation': ''}
        with self.lock:
            if self.job and self.job['model'] == model_id:
                result['operation'] = tail(self.data / f"operation-{self.job['id']}.log", 10000)
        return result

    def try_request(self, model_id, prompt, max_tokens):
        if not self.gate.acquire(blocking=False):
            raise Conflict('A model operation or test request is already running.')
        try:
            self.refresh()
            model = next(m for m in self.models if m['id'] == model_id)
            if not model['ready']:
                raise Conflict('Deploy this model and wait for API ready before sending a request.')
            started = time.monotonic()
            payload = dict(model=model['alias'], messages=[{'role': 'user', 'content': prompt}],
                           max_tokens=max_tokens, temperature=0, stream=False,
                           chat_template_kwargs={'enable_thinking': False})
            r = self.http.post(f"http://127.0.0.1:{model['port']}/v1/chat/completions", json=payload, timeout=300)
            r.raise_for_status()
            body = r.json()
            usage = body.get('usage', {})
            elapsed = time.monotonic() - started
            content = body['choices'][0]['message'].get('content') or ''
            self.event('info', f"Test request: {model['name']}, {usage.get('completion_tokens', '?')} output tokens, {elapsed:.1f}s")
            return dict(content=content, usage=usage, elapsed_s=elapsed,
                        request_output_tps=usage['completion_tokens']/elapsed if 'completion_tokens' in usage else None,
                        finish_reason=body['choices'][0].get('finish_reason'))
        finally:
            self.gate.release()
