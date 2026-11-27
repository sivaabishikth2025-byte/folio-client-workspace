import {spawnSync,spawn} from 'node:child_process';
import {existsSync,copyFileSync} from 'node:fs';
const win=process.platform==='win32';
function run(cmd,args){const p=spawnSync(cmd,args,{stdio:'inherit',shell:win&&cmd.endsWith('.cmd')});if(p.status!==0)process.exit(p.status||1);}
if(!existsSync('.venv'))run(win?'python':'python3',['-m','venv','.venv']);
const py=win?'.venv/Scripts/python.exe':'.venv/bin/python';
run(py,['-m','pip','install','-q','-r','requirements.txt']);
if(!existsSync('node_modules'))run(win?'npm.cmd':'npm',['ci']);
if(!existsSync('.env'))copyFileSync('.env.example','.env');
run(win?'npm.cmd':'npm',['run','build']);
const p=spawn(py,['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',process.env.PORT||'8000'],{stdio:'inherit'});
process.on('SIGINT',()=>p.kill('SIGINT'));p.on('exit',code=>process.exit(code||0));
