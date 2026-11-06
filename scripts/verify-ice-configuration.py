"""Retain direct Internet candidates alongside the existing TCP proxy relay."""
from rtc_agent import desktop_ice_servers
servers=desktop_ice_servers({'iceServers':[
    {'urls':'stun:stun.cloudflare.com:3478'},
    {'urls':['turn:primary.example:3478?transport=tcp','turn:fallback.example:3478?transport=udp','turns:tls.example:443'],'username':'test-user','credential':'test-only'},
]})
assert len(servers)==2 and servers[0].urls==['stun:stun.cloudflare.com:3478']
assert servers[1].urls==['turn:primary.example:3478?transport=tcp','turns:tls.example:443']
assert servers[1].username=='test-user' and servers[1].credential=='test-only'
assert desktop_ice_servers({})==[]
print('Verified STUN direct-candidate discovery is retained with TCP/TLS relay configuration. No network traffic.')
