"""单独跑 ASR 转录"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingestion.asr import transcribe
import json, time

mp3 = 'downloads/_ [JVz1B31BmS0].mp3'
print(f'开始转录: {mp3}')
start = time.time()
result = transcribe(mp3, model='tiny', language='zh')
elapsed = time.time() - start
print(f'耗时: {elapsed:.1f}s')
if result.get('ok'):
    with open('/tmp/v2t_text.txt', 'w') as f:
        f.write(result['text'])
    print(f'文本已写入 /tmp/v2t_text.txt ({len(result["text"])} 字)')
else:
    print(f'失败: {result}')
print('DONE')