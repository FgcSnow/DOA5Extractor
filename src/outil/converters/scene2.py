import sys

import convert_model,convert_scene
from convert_structures import children,u,put
from convert_scene import scattering
from params import simple
from arealight import area_light
_orig=convert_model.textures
def textures(g,external):
    split=u(g,40);h=g[48:split]
    if children(h):return _orig(g,external)
    l=bytearray(g[split:]);nh=bytearray(h[:0x40]+h[0x50:]);nh[:4]=b'TTDH';put(nh,32,0x40);put(nh,16,len(nh))
    blob=bytearray(external[:16]);blob.extend(bytes(48));l[:4]=b'TTDL'
    put(blob,4,len(blob));put(l,68,len(blob))
    out=bytearray(g[:48]);out[:4]=b'TTDM';put(out,40,48+len(nh));out.extend(nh);out.extend(l);put(out,16,len(out))
    return out,blob
convert_model.textures=textures;convert_scene.textures=textures
AREA_LIGHT_FALLBACK=[]
def convert_scene2(scene,col10):
    out=bytearray(scene[:children(scene)[0]]);converted_col=None
    for i,o in enumerate(children(scene)):
        size=u(scene,u(scene,36)+4*i);x=scene[o:o+size];name=x[:8].rstrip(b'\0').decode()
        if i==0:
            off=children(x)[1];tex,converted_col=textures(x[off:],col10)
            x=bytearray(x[:off])+tex;put(x,16,len(x))
        elif name=='LIGHT' and x[0xb0:0xb5]==b'area_':x=area_light(x)
        elif name in('LIGHT','FOG'):x=simple(x,name)
        elif name=='LSCAT':x=scattering(x)
        put(out,u(out,32)+4*i,len(out));put(out,u(out,36)+4*i,len(x));out.extend(x)
    put(out,16,len(out));return out,converted_col
