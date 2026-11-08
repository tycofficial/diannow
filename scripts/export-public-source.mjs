import {readFile,readdir} from 'node:fs/promises';
// Export only the reviewed runtime sources and direct verification scripts.
// Credentials, local service configuration, logs, outputs and environments
// stay outside this allowlist; .env.example contains placeholders only.
const runtimePaths=['index.html','.nojekyll','README.md','THIRD_PARTY.md','requirements.txt','package.json','.gitignore','.env.example','.openai/hosting.json','worker/index.js','web/viewer.html','web/gestures.mjs','docs/index.html','docs/.nojekyll','scripts/build.mjs','scripts/validate-artifact.mjs','scripts/export-public-source.mjs','scripts/rtc_agent.py','scripts/remote_input.py','scripts/desktop_audio.py','scripts/audio_routing.py','scripts/touch_injection.py','scripts/display_target.py','scripts/install-signed-drivers.ps1','scripts/configure-desktop-network.ps1','scripts/low_latency_rtp.py','scripts/rtp_scheduling.py','scripts/ice_consent.py','scripts/turn_flow_control.py','scripts/turn_proxy.py'];
runtimePaths.push('scripts/control_transport.py');
runtimePaths.push('version.json','docs/version.json');
runtimePaths.push('scripts/bitrate_stability.py');
runtimePaths.push('scripts/stream_profile.py');
runtimePaths.push('scripts/realtime_clock.py');
runtimePaths.push('scripts/encoded_pipeline.py');
runtimePaths.push('scripts/video_retransmission.py');
runtimePaths.push('scripts/turn_route.py');
runtimePaths.push('scripts/turn_fast_path.py');
runtimePaths.push('scripts/capture_recovery.py');
runtimePaths.push('scripts/audio_buffer.py');
runtimePaths.push('scripts/media_encoder.py');
runtimePaths.push('scripts/delivery_acceptance.py');
const checks=(await readdir('scripts',{withFileTypes:true})).filter(entry=>entry.isFile()&&/^verify-[a-z0-9-]+\.(?:py|mjs)$/.test(entry.name)).map(entry=>'scripts/'+entry.name).sort();
const paths=[...runtimePaths,'scripts/desktop_host.py','scripts/build-desktop.ps1','scripts/install-desktop.ps1',...checks];
const files=await Promise.all(paths.map(async path=>({path,mode:'100644',type:'blob',content:await readFile(path,'utf8')})));
process.stdout.write(JSON.stringify(files));
