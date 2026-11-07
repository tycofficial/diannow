"""Retain direct and UDP/TCP/TLS relay routes in selected priority order."""
from rtc_agent import desktop_ice_servers
servers=desktop_ice_servers({'iceServers':[
    {'urls':'stun:stun.cloudflare.com:3478'},
    {'urls':['turn:primary.example:3478?transport=tcp','turn:fallback.example:3478?transport=udp','turns:tls.example:443'],'username':'test-user','credential':'test-only'},
]})
assert len(servers)==2 and servers[0].urls==['stun:stun.cloudflare.com:3478']
assert servers[1].urls==['turn:primary.example:3478?transport=tcp','turn:fallback.example:3478?transport=udp','turns:tls.example:443']
assert servers[1].username=='test-user' and servers[1].credential=='test-only'
fallback=desktop_ice_servers({'iceServers':[{'urls':['turn:relay.example:3478?transport=udp','turn:relay.example:3478?transport=tcp','turns:relay.example:5349'],'username':'test','credential':'test'}]},turn_transport='tcp')
assert fallback[0].urls==['turn:relay.example:3478?transport=tcp','turns:relay.example:5349']
assert desktop_ice_servers({})==[]
print('Verified STUN direct candidates and selected UDP/TCP/TLS relay ordering. No network traffic.')
