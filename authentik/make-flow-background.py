#!/usr/bin/env python3
"""make-flow-background.py — regenerate the HeardOS sign-in background (2560x1440 JPEG) from the Hearth Crest package.
Charcoal #26272b -> ink #1f2023 vignette, orange #f2a35e hearth glow lower right, faint mono shield watermark, 96 px slate grid."""
from PIL import Image, ImageDraw, ImageChops
import pathlib, math, sys
src=pathlib.Path.home()/"Pictures/logos/heardos-logo"; W,H=2560,1440; out=sys.argv[1] if len(sys.argv)>1 else "flow-background.jpg"
def field(fn, w=320, h=180):
    im=Image.new("L",(w,h)); px=im.load()
    for y in range(h):
        for x in range(w): px[x,y]=max(0,min(255,int(fn(x/(w-1),y/(h-1))*255)))
    return im.resize((W,H), Image.BICUBIC)
vig=field(lambda u,v: 1-min(1,((u-0.5)**2/0.45+(v-0.5)**2/0.55))**1.1)
glow=field(lambda u,v: 0.34*max(0,1-math.sqrt(((u-0.78)/0.55)**2+((v-0.86)/0.6)**2))**2.2)
base=Image.composite(Image.new("RGB",(W,H),(0x26,0x27,0x2b)), Image.new("RGB",(W,H),(0x1f,0x20,0x23)), vig)
base=Image.composite(Image.new("RGB",(W,H),(0xf2,0xa3,0x5e)), base, glow)
mark=Image.open(src/"png/mark/heardos-mark-on-dark-1024.png").convert("RGBA"); s=int(H*1.05); mark=mark.resize((s,s), Image.LANCZOS)
alpha=ImageChops.multiply(mark.convert("L"), mark.split()[3]).point(lambda v:int(v*0.075))
base.paste(Image.new("RGB",mark.size,(0xec,0xec,0xec)),(int(W*0.60),int(H*0.5-s*0.47)),alpha)
grid=Image.new("L",(W,H),0); g=ImageDraw.Draw(grid)
for x in range(0,W,96): g.line([(x,0),(x,H)],fill=255)
for y in range(0,H,96): g.line([(0,y),(W,y)],fill=255)
base=Image.composite(Image.new("RGB",(W,H),(0x4e,0x4e,0x54)), base, grid.point(lambda v:int(v*0.035)))
base.save(out, quality=90, optimize=True, subsampling=0); print(out, base.size)
