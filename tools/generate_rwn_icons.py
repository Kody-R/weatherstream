from __future__ import annotations
from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFilter

ROOT=Path(__file__).resolve().parents[1]/'app'/'static'/'icons'/'rwn'
SIZES={'hero':224,'standard':128,'compact':72}
COND=[
 'clear_day','clear_night','mostly_clear_day','mostly_clear_night','partly_cloudy_day','partly_cloudy_night',
 'cloudy','overcast','fog','drizzle','light_rain','rain','heavy_rain','showers','thunderstorm','severe_thunderstorm',
 'snow','heavy_snow','sleet','freezing_rain','windy','hot','cold','tropical','tornado','flood','lightning','smoke'
]
METRICS=['temperature','humidity','dew_point','wind','gust','pressure','cloud_cover','rain','uv','visibility','sunrise','sunset']
ALERTS=['tornado','severe_thunderstorm','flash_flood','winter_storm','extreme_heat','wildfire','tropical']
ANIMATED={'partly_cloudy_day','partly_cloudy_night','light_rain','rain','heavy_rain','showers','thunderstorm','severe_thunderstorm','snow','heavy_snow','windy','tropical','lightning'}


def rgba(hexv,a=255):
    hexv=hexv.lstrip('#'); return tuple(int(hexv[i:i+2],16) for i in (0,2,4))+(a,)

def gradient_circle(size, c1, c2):
    im=Image.new('RGBA',(size,size),(0,0,0,0)); p=im.load(); cx=cy=(size-1)/2; r=size/2
    a=rgba(c1); b=rgba(c2)
    for y in range(size):
        for x in range(size):
            d=math.hypot(x-cx,y-cy)/r
            if d<=1:
                t=max(0,min(1,(y/size)*.75+d*.1))
                p[x,y]=tuple(int(a[i]*(1-t)+b[i]*t) for i in range(3))+(255,)
    return im

def shadow_layer(base, blur):
    alpha=base.getchannel('A'); sh=Image.new('RGBA',base.size,(0,0,0,0)); sh.putalpha(alpha.filter(ImageFilter.GaussianBlur(blur))); return sh

def cloud(canvas, box, compact=False, dark=False, offset=(0,0)):
    x,y,w,h=box; x+=offset[0]; y+=offset[1]
    layer=Image.new('RGBA',canvas.size,(0,0,0,0)); d=ImageDraw.Draw(layer)
    top='#b9c7d6' if dark else '#f8fbff'; bottom='#6f8094' if dark else '#b9c9d9'
    # shadow
    sd=Image.new('RGBA',canvas.size,(0,0,0,0)); s=ImageDraw.Draw(sd)
    s.ellipse((x+w*.05,y+h*.30,x+w*.48,y+h*.82),fill=(0,0,0,95)); s.ellipse((x+w*.27,y+h*.02,x+w*.72,y+h*.82),fill=(0,0,0,95)); s.ellipse((x+w*.55,y+h*.22,x+w*.95,y+h*.82),fill=(0,0,0,95)); s.rounded_rectangle((x+w*.08,y+h*.46,x+w*.88,y+h*.88),radius=int(h*.16),fill=(0,0,0,95))
    sd=sd.filter(ImageFilter.GaussianBlur(max(2,int(w*.025)))); canvas.alpha_composite(sd)
    # gradient-ish stacked shapes
    d.ellipse((x+w*.05,y+h*.28,x+w*.48,y+h*.82),fill=rgba(bottom)); d.ellipse((x+w*.27,y+h*.02,x+w*.72,y+h*.82),fill=rgba(top)); d.ellipse((x+w*.55,y+h*.20,x+w*.95,y+h*.82),fill=rgba('#d9e4ef' if not dark else '#8fa0b2')); d.rounded_rectangle((x+w*.08,y+h*.45,x+w*.88,y+h*.88),radius=int(h*.16),fill=rgba('#dce7f1' if not dark else '#91a0b0'))
    if not compact:
        d.arc((x+w*.30,y+h*.12,x+w*.65,y+h*.60),190,320,fill=(255,255,255,150),width=max(1,int(w*.02)))
        d.line((x+w*.17,y+h*.72,x+w*.80,y+h*.72),fill=(103,122,143,120),width=max(1,int(w*.018)))
    canvas.alpha_composite(layer)

def sun(canvas,cx,cy,r):
    glow=Image.new('RGBA',canvas.size,(0,0,0,0)); gd=ImageDraw.Draw(glow)
    gd.ellipse((cx-r*1.35,cy-r*1.35,cx+r*1.35,cy+r*1.35),fill=(255,188,42,60)); glow=glow.filter(ImageFilter.GaussianBlur(max(2,int(r*.35)))); canvas.alpha_composite(glow)
    d=ImageDraw.Draw(canvas)
    for a in range(0,360,45):
        rr1=r*1.25; rr2=r*1.60; q=math.radians(a); d.line((cx+math.cos(q)*rr1,cy+math.sin(q)*rr1,cx+math.cos(q)*rr2,cy+math.sin(q)*rr2),fill='#ffc52f',width=max(2,int(r*.10)))
    orb=gradient_circle(int(r*2),'#fff16c','#f59a16'); canvas.alpha_composite(orb,(int(cx-r),int(cy-r)))

def moon(canvas,cx,cy,r):
    d=ImageDraw.Draw(canvas); d.ellipse((cx-r,cy-r,cx+r,cy+r),fill='#f7f0c2'); d.ellipse((cx-r*.35,cy-r*1.02,cx+r*1.20,cy+r*.60),fill=(0,0,0,0))
    # mask crescent using transparent punch via compositing
    cut=Image.new('RGBA',canvas.size,(0,0,0,0)); cd=ImageDraw.Draw(cut); cd.ellipse((cx-r*.25,cy-r*1.10,cx+r*1.35,cy+r*.55),fill=(0,0,0,255));
    alpha=canvas.getchannel('A'); mask=cut.getchannel('A'); alpha=ImageChops.subtract(alpha,mask) if False else alpha
    # overwrite with dark-blue crescent cut matching transparent background using mask on local layer
    md=Image.new('RGBA',canvas.size,(0,0,0,0)); m=ImageDraw.Draw(md); m.ellipse((cx-r,cy-r,cx+r,cy+r),fill='#f7f0c2'); m.ellipse((cx-r*.20,cy-r*1.08,cx+r*1.32,cy+r*.55),fill=(0,0,0,0)); canvas.alpha_composite(md)

def add_moon(canvas,cx,cy,r):
    layer=Image.new('RGBA',canvas.size,(0,0,0,0)); d=ImageDraw.Draw(layer); d.ellipse((cx-r,cy-r,cx+r,cy+r),fill='#f7f0c2')
    mask=Image.new('L',canvas.size,0); md=ImageDraw.Draw(mask); md.ellipse((cx-r,cy-r,cx+r,cy+r),fill=255); md.ellipse((cx-r*.10,cy-r*1.05,cx+r*1.35,cy+r*.55),fill=0); layer.putalpha(mask)
    canvas.alpha_composite(layer); d=ImageDraw.Draw(canvas)
    for sx,sy in ((cx+r*1.25,cy-r*.55),(cx+r*1.55,cy+.05*r)): d.ellipse((sx-2,sy-2,sx+2,sy+2),fill='#dcecff')

def precip(canvas,kind,xs,y0,scale,phase=0):
    d=ImageDraw.Draw(canvas)
    if kind=='rain':
        for i,x in enumerate(xs):
            off=((phase+i)%4)*scale*2
            d.line((x,y0+off,x-scale*4,y0+scale*13+off),fill='#54c8ff',width=max(2,int(scale*2.6)))
    elif kind=='snow':
        for i,x in enumerate(xs):
            y=y0+((phase+i)%3)*scale*2; r=max(2,int(scale*2));
            d.line((x-r*2,y,x+r*2,y),fill='#ffffff',width=max(1,int(scale))); d.line((x,y-r*2,x,y+r*2),fill='#ffffff',width=max(1,int(scale))); d.line((x-r*1.4,y-r*1.4,x+r*1.4,y+r*1.4),fill='#ffffff',width=max(1,int(scale)))

def bolt(canvas,x,y,s):
    d=ImageDraw.Draw(canvas); pts=[(x,y),(x-s*.25,y+s*.58),(x+s*.12,y+s*.52),(x-s*.06,y+s*1.12),(x+s*.55,y+s*.38),(x+s*.18,y+s*.42)]; d.polygon(pts,fill='#ffd447',outline='#fff5a8')

def draw_condition(name,size,phase=0):
    im=Image.new('RGBA',(size,size),(0,0,0,0)); comp=size<=80; s=size/224
    if name in {'clear_day','mostly_clear_day','partly_cloudy_day','hot'}: sun(im,size*.46,size*.43,size*.25)
    if name in {'clear_night','mostly_clear_night','partly_cloudy_night'}: add_moon(im,size*.46,size*.43,size*.25)
    if name in {'cloudy','overcast','fog','drizzle','light_rain','rain','heavy_rain','showers','thunderstorm','severe_thunderstorm','snow','heavy_snow','sleet','freezing_rain','partly_cloudy_day','partly_cloudy_night','mostly_clear_day','mostly_clear_night'}:
        ox=(phase-1.5)*2*s if name.startswith('partly') else 0
        cloud(im,(size*.15,size*.30,size*.73,size*.43),compact=comp,dark=name in {'overcast','thunderstorm','severe_thunderstorm'},offset=(ox,0))
    if name=='overcast': cloud(im,(size*.08,size*.18,size*.63,size*.36),compact=comp,dark=True,offset=(0,0))
    if name=='fog':
        d=ImageDraw.Draw(im)
        for i in range(3): d.rounded_rectangle((size*.18,size*(.68+i*.075),size*.84,size*(.72+i*.075)),radius=max(1,int(size*.015)),fill=(216,231,240,220))
    if name in {'drizzle','light_rain','rain','heavy_rain','showers','freezing_rain'}:
        cnt=3 if name in {'drizzle','light_rain'} else 4 if name in {'rain','showers','freezing_rain'} else 5
        xs=[size*(.30+i*(.42/max(1,cnt-1))) for i in range(cnt)]; precip(im,'rain',xs,size*.69,1.0*s,phase)
    if name in {'snow','heavy_snow','sleet'}:
        cnt=3 if name=='snow' else 5; xs=[size*(.28+i*(.46/max(1,cnt-1))) for i in range(cnt)]; precip(im,'snow',xs,size*.73,1.3*s,phase)
    if name=='sleet':
        d=ImageDraw.Draw(im); d.line((size*.38,size*.70,size*.34,size*.80),fill='#61c9ff',width=max(2,int(size*.018))); d.line((size*.62,size*.70,size*.58,size*.80),fill='#61c9ff',width=max(2,int(size*.018)))
    if name=='freezing_rain':
        d=ImageDraw.Draw(im); d.polygon([(size*.78,size*.74),(size*.83,size*.83),(size*.73,size*.83)],fill='#bdf2ff')
    if name in {'thunderstorm','severe_thunderstorm','lightning'}:
        if name=='lightning': cloud(im,(size*.15,size*.18,size*.72,size*.42),compact=comp,dark=True)
        if phase!=1 or name=='severe_thunderstorm': bolt(im,size*.48,size*.58,size*.18)
    if name=='severe_thunderstorm':
        d=ImageDraw.Draw(im)
        for x in (size*.31,size*.70): d.ellipse((x-size*.035,size*.78,x+size*.035,size*.85),fill='#e9f7ff',outline='#85d5ff')
    if name=='windy':
        d=ImageDraw.Draw(im)
        for j,(yy,ln) in enumerate(((.32,.60),(.50,.73),(.68,.54))):
            shift=math.sin((phase+j)*math.pi/2)*size*.025
            d.arc((size*.12+shift,size*(yy-.13),size*(.12+ln)+shift,size*(yy+.13)),190,350,fill='#d8efff',width=max(3,int(size*.045)))
    if name=='cold':
        d=ImageDraw.Draw(im); cx=cy=size*.5; r=size*.24
        for a in range(0,180,30):
            q=math.radians(a); dx=math.cos(q)*r; dy=math.sin(q)*r; d.line((cx-dx,cy-dy,cx+dx,cy+dy),fill='#b9eeff',width=max(2,int(size*.028)))
    if name=='tropical':
        d=ImageDraw.Draw(im); cx=cy=size*.5
        for k in range(2):
            start=phase*.12+k*math.pi; pts=[]
            for i in range(45):
                a=start+i*.18; r=size*(.05+i*.0045); pts.append((cx+math.cos(a)*r,cy+math.sin(a)*r))
            d.line(pts,fill='#e8f8ff',width=max(3,int(size*.04)))
        d.ellipse((cx-size*.05,cy-size*.05,cx+size*.05,cy+size*.05),fill='#0f4f79')
    if name=='tornado':
        d=ImageDraw.Draw(im); pts=[(size*.26,size*.20),(size*.78,size*.20),(size*.68,size*.34),(size*.74,size*.40),(size*.57,size*.54),(size*.62,size*.60),(size*.47,size*.76),(size*.50,size*.88),(size*.40,size*.88),(size*.43,size*.72),(size*.32,size*.63),(size*.48,size*.49),(size*.34,size*.41),(size*.58,size*.31)]; d.polygon(pts,fill='#c7d1dc',outline='#ffffff')
    if name=='flood':
        d=ImageDraw.Draw(im)
        for row in range(3):
            y=size*(.38+row*.15); pts=[]
            for i in range(9): pts.append((size*(.12+i*.10), y+math.sin(i*math.pi/2)*size*.035)); d.line(pts,fill='#55c5ff',width=max(3,int(size*.045)))
    if name=='smoke':
        cloud(im,(size*.16,size*.28,size*.68,size*.38),compact=comp,dark=True)
        d=ImageDraw.Draw(im)
        for yy in (.64,.73,.82): d.line((size*.16,size*yy,size*.83,size*yy),fill=(176,184,191,190),width=max(2,int(size*.025)))
    return im

def draw_metric(name,size=48):
    im=Image.new('RGBA',(size,size),(0,0,0,0)); d=ImageDraw.Draw(im); w=max(2,size//12); a='#7bd6ff'; b='#ffd447'; white='#f4fbff'; cx=size/2
    if name=='temperature': d.rounded_rectangle((size*.40,size*.12,size*.60,size*.72),radius=w,outline=white,width=w); d.ellipse((size*.30,size*.60,size*.70,size*.98),fill='#ff765f',outline=white,width=max(1,w//2)); d.rectangle((size*.47,size*.30,size*.54,size*.70),fill='#ff765f')
    elif name in {'humidity','dew_point','rain'}: d.polygon([(cx,size*.08),(size*.22,size*.58),(size*.22,size*.74),(size*.31,size*.91),(size*.50,size*.98),(size*.69,size*.91),(size*.78,size*.74),(size*.78,size*.58)],fill=a if name!='dew_point' else '#86edc8',outline=white); d.ellipse((size*.39,size*.62,size*.55,size*.78),fill=(255,255,255,150))
    elif name in {'wind','gust'}:
        for yy,ln in ((.30,.62),(.52,.78),(.72,.54)): d.arc((size*.08,size*(yy-.13),size*(.08+ln),size*(yy+.13)),190,350,fill=white if name=='wind' else b,width=w)
    elif name=='pressure': d.ellipse((size*.12,size*.12,size*.88,size*.88),outline=white,width=w); d.line((cx,size*.50,size*.73,size*.34),fill=b,width=w); d.ellipse((cx-w, size*.50-w,cx+w,size*.50+w),fill=white)
    elif name=='cloud_cover': cloud(im,(size*.06,size*.18,size*.88,size*.58),compact=True,dark=False)
    elif name=='uv': sun(im,cx,cx,size*.22)
    elif name=='visibility': d.ellipse((size*.08,size*.24,size*.92,size*.76),outline=white,width=w); d.ellipse((size*.36,size*.36,size*.64,size*.64),fill=a,outline=white,width=max(1,w//2))
    elif name in {'sunrise','sunset'}: sun(im,cx,size*.48,size*.16); d.line((size*.12,size*.70,size*.88,size*.70),fill=white,width=w); d.line((cx,size*.72,cx,size*.92 if name=='sunset' else size*.52),fill=b,width=w)
    return im

def draw_alert(name,size=128):
    mapping={'tornado':'tornado','severe_thunderstorm':'severe_thunderstorm','flash_flood':'flood','winter_storm':'heavy_snow','extreme_heat':'hot','wildfire':'smoke','tropical':'tropical'}
    im=draw_condition(mapping[name],size,0)
    # badge ring to separate alert icon from normal weather iconography
    d=ImageDraw.Draw(im); d.rounded_rectangle((2,2,size-3,size-3),radius=max(8,size//8),outline=(255,255,255,170),width=max(2,size//32))
    return im

def main():
    for kind,size in SIZES.items():
        out=ROOT/'conditions'/kind; out.mkdir(parents=True,exist_ok=True)
        for name in COND:
            draw_condition(name,size,0).save(out/f'{name}.png')
    for kind in ('hero','standard'):
        size=SIZES[kind]; out=ROOT/'animated'/kind; out.mkdir(parents=True,exist_ok=True)
        for name in sorted(ANIMATED):
            for phase in range(4): draw_condition(name,size,phase).save(out/f'{name}-{phase}.png')
    out=ROOT/'metrics'; out.mkdir(parents=True,exist_ok=True)
    for name in METRICS: draw_metric(name,48).save(out/f'{name}.png')
    out=ROOT/'alerts'; out.mkdir(parents=True,exist_ok=True)
    for name in ALERTS: draw_alert(name,128).save(out/f'{name}.png')
    print('generated',sum(1 for _ in ROOT.rglob('*.png')),'icons under',ROOT)

if __name__=='__main__': main()
