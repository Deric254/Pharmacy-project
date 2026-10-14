import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

// The app refuses to work on an unlicensed copy, so the GUI tests run it as the
// owner's computer. CI builds this test run with a THROWAWAY owner key (see the
// "throwaway owner key" step in .github/workflows/ci.yml) and passes the matching
// private key file in PHE_E2E_OWNER_KEY_FILE; the real owner key never gets here.
// Without that variable nothing is seeded and the app stays locked, so the
// tests fail loudly rather than pass against a bypass.
const SEED_SCRIPT = `
import sys
sys.path.insert(0, sys.argv[1])
from licensing.licensing import FileStore, Licensing

key_text = open(sys.argv[2], encoding="utf-8").read()
probe = Licensing("Pharmacy ERP", "PHE", "", None)
seed = probe.parse_owner_key(key_text)
from licensing import ed25519
store = FileStore(sys.argv[3], "licence-state.json")
Licensing("Pharmacy ERP", "PHE", ed25519.public_key(seed).hex(), store).claim_owner(key_text)
`

export function seedOwnerLicence(appDataDir: string): void {
  const keyFile = process.env.PHE_E2E_OWNER_KEY_FILE
  if (!keyFile) return
  // desktop_main.py keeps its data (and licence-state.json) in <LOCALAPPDATA>/PharmacyERP.
  const dataDir = path.join(appDataDir, 'PharmacyERP')
  if (existsSync(path.join(dataDir, 'licence-state.json'))) return
  const backendDir = path.join(__dirname, '..', '..', 'backend')
  execFileSync('python', ['-c', SEED_SCRIPT, backendDir, keyFile, dataDir], { stdio: 'inherit' })
}
