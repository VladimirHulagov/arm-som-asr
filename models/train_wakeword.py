#!/usr/bin/env python3
"""Train wake word model from slushay samples."""
import sys
sys.path.insert(0, '/opt/voice-client')

from pathlib import Path
from openwakeword.utils import train_new_word

samples_dir = Path('/opt/voice-client/slushay-samples')
output_path = Path('/opt/voice-client/wakeword.onnx')

# Collect both sample_*.wav and live_*.wav
sample_files = sorted(samples_dir.glob('sample_*.wav'))
live_files = sorted(samples_dir.glob('live_*.wav'))
all_files = sample_files + live_files

print(f'Found {len(sample_files)} sample files and {len(live_files)} live files')
print(f'Total: {len(all_files)} files')

if len(all_files) < 5:
    print('Need at least 5 samples!')
    sys.exit(1)

print('Training model...')
print('This may take a while without GPU...')

try:
    train_new_word(
        output_path=str(output_path),
        sample_files=[str(f) for f in all_files],
        target_lang='ru',
        negative_samples_method='vad'
    )
    print(f'Done! Model saved to {output_path}')
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
