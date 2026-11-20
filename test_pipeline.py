import urllib.request
import json

base = 'http://127.0.0.1:8765'

def get(path):
    r = urllib.request.urlopen(base + path, timeout=10)
    return r.status, json.loads(r.read().decode())

def post(path, data=None):
    body = json.dumps(data or {}).encode('utf-8')
    req = urllib.request.Request(base + path, data=body, headers={'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=10)
    return r.status, json.loads(r.read().decode())

print('=== Hierarchy ===')
status, data = get('/admin/hierarchy')
print('root_admin:', data['root_admin']['name'], 'state:', data['root_admin']['state'])
print('agents:', len(data['agents']))
print('replicas:', len(data['replicas']))
print('tree:', data['tree'])

print()
print('=== Issue directives to pipeline agents ===')
status, data = post('/admin/directives', {'targets': ['freellmapi', 'agent-desktop', 'agentic-inbox', 'Open-LLM-VTuber']})
print('total issued:', data.get('total'))
for item in data.get('issued', []):
    print('  ' + item['agent'] + ': run_id=' + item['run_id'] + ' status=' + item['status'])
    d = item.get('directive', {})
    print('    role=' + str(d.get('role')) + ' purpose=' + d.get('purpose', '')[:60] + '...')

print()
print('=== Issue directives to business agents ===')
status, data = post('/admin/directives', {'targets': ['AutoHedge', 'claude-ads']})
print('total issued:', data.get('total'))
for item in data.get('issued', []):
    print('  ' + item['agent'] + ': run_id=' + item['run_id'] + ' status=' + item['status'])
    d = item.get('directive', {})
    print('    role=' + str(d.get('role')) + ' purpose=' + d.get('purpose', '')[:60] + '...')

print()
print('All tests done!')