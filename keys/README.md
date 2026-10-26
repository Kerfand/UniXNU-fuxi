# Owner trust anchor

The verified-root path authenticates every 4 KiB block of the `OS` and `SystemOS` windows
against a signed manifest. The signature is checked with a fixed RSA3072 anchor that is
embedded in the kernel as `verified_root_key` (395 bytes, `VR_KEY_BYTES`).

| File | Content |
| --- | --- |
| `owner-public.pem` | Owner public key (RSA3072, PKCS#8 public PEM). Public. |
| `trusted-key.bin` | The 395-byte anchor built from the public key: `"VROOTKEY"` + 384-byte modulus + `0x010001`. This is exactly what `verified_root_key` holds. |

sha256 of `trusted-key.bin`:
`74ace0ad282db2890d594dbc2a57698874256fe0beba35e11b5481a8b0a9499e`

The **private** key is not in this repository. It is stored as the Actions secret
`FUXI_OWNER_KEY` (PKCS#1 PEM, from `openssl genrsa 3072`) and is only read inside the
workflow that signs the manifests.

## Regenerating

A new key invalidates every manifest and every kernel built against the old one, so only do
this deliberately.

```sh
openssl genrsa -out owner.pem 3072
openssl rsa -in owner.pem -pubout -out owner-public.pem
# anchor: manifest.py public_anchor(), or
python3 - <<'PY'
from pathlib import Path
import subprocess
line = subprocess.check_output(['openssl', 'rsa', '-pubin', '-in', 'owner-public.pem',
                                '-modulus', '-noout'], text=True)
modulus = bytes.fromhex(line.strip().split('=', 1)[1])
Path('trusted-key.bin').write_bytes(b'VROOTKEY' + modulus + b'\x01\x00\x01')
PY
```

Then update the secret:

```sh
gh secret set FUXI_OWNER_KEY -R Kerfand/UniXNU-fuxi < owner.pem
```

The anchor is an independent owner key, not an Apple key: it authorises the blocks this
board's own manifests describe. It says nothing about Apple silicon or fuses.
