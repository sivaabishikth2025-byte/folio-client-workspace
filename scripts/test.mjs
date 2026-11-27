import {spawnSync} from 'node:child_process';
const py=process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python';
const r=spawnSync(py,['-m','pytest','tests','-q'],{stdio:'inherit'});
if(r.status!==0)process.exit(r.status??1);
const js=spawnSync(process.execPath,['--test','tests/evidence.test.mjs'],{stdio:'inherit'});process.exit(js.status??1);
