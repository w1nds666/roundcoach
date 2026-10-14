import json, pathlib
p = pathlib.Path(r'D:\AI-Workspaces\CS Anlysizer\docs\6r21f-e6d-perf.json')
j = json.load(open(p, encoding='utf-8'))
print('engine', j.get('engine'), 'map', j.get('map'), 'demo', j.get('demo'))
for k in sorted(j['views'].keys()):
    v = j['views'][k]
    print(' ', k, v['viewport'], 'min=%s p50=%s p95=%s p99=%s avg=%s jank=%s maxFrame=%s heapB=%s heapD=%s slow=%d' % (
        v['minFps'], v['p50'], v['p95'], v['p99'], v['avgFps'], v['jank'], v['maxFrameMs'], v['heapBeforeMB'], v['heapDuringMB'], len(v['slow'])))
kf = j.get('keyframes', [])
print('keyframes', len(kf))
for k in kf:
    print(' ', k['name'], 't', k['t'], 'ck', k['fieldChecksum'], 'frameHash', k['frameHash'], 'alive', k['alive'], 'diffFromPrev', k['frameDiffFromPrev'], 'exists', pathlib.Path(k['screenshot']).is_file())
