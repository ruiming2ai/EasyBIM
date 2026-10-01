"""Apply only the exact locally tested patch during isolated candidate CI."""
import base64
import gzip
import hashlib
import pathlib
import subprocess

path = pathlib.Path('Development Space/tests/auto-update-candidate.patch.gz.b64')
# Repair a transport transcription, then require the exact original digest.
encoded = path.read_bytes().replace(b'Is/SWZZJah', b'Is/SWZJah')
patch = gzip.decompress(base64.b64decode(encoded))
assert hashlib.sha256(patch).hexdigest() == '50ab8b7bad99c8e4b402dcd52274a3e8a49d561ee048db86317f51f58f82deaa'
subprocess.run(['git', 'apply', '--whitespace=error', '-'], input=patch, check=True)
