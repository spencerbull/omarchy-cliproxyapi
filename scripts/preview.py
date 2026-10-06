#!/usr/bin/env python3
"""Preview the real plugin with synthetic accounts in an isolated shell."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from demo_server import create_server

omarchy = next((path for path in [Path(os.environ.get('OMARCHY_PATH', '/usr/share/omarchy')),
                                 Path.home() / '.local/share/omarchy', Path.home() / 'omarchy']
                if (path / 'shell/Commons').is_dir()), None)
if omarchy is None:
    raise SystemExit('Omarchy shell modules not found. Set OMARCHY_PATH to an Omarchy checkout.')
server = create_server()
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with tempfile.TemporaryDirectory(prefix='cliproxyapi-preview-') as directory:
        target = Path(directory)
        (target / 'Commons').symlink_to(omarchy / 'shell/Commons')
        (target / 'Ui').symlink_to(omarchy / 'shell/Ui')
        source = (ROOT / 'tests/preview.qml').read_text().replace('PLUGIN_URL', ROOT.as_uri())
        (target / 'shell.qml').write_text(source)
        env = dict(os.environ, QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1", XDG_CONFIG_HOME=str(target / 'config'), CPA_DEMO_PORT=str(server.server_port))
        print(f'Native preview: {target}', flush=True)
        print('Synthetic URL: http://127.0.0.1:' + str(server.server_port) + '  Key: demo-only', flush=True)
        try:
            subprocess.run(['qs', '-p', str(target), '--no-color'], env=env, check=False)
        except KeyboardInterrupt:
            pass
finally:
    server.shutdown()
    server.server_close()
