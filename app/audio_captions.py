"""Caption overlay in the existing Visual System 2.0 safe content area."""
from PIL import ImageDraw


def draw_caption(image, text):
    if not text: return image
    from app.renderer import font
    draw=ImageDraw.Draw(image); w,h=image.size
    scale=w/1280; f=font(max(14,int(26*scale)),bold=True)
    lines=[]; line=''
    for word in text.split():
        candidate=(line+' '+word).strip()
        if draw.textlength(candidate,font=f)>w*.84 and line:
            lines.append(line); line=word
        else: line=candidate
    if line: lines.append(line)
    lines=lines[:2]; line_height=max(20,int(34*scale))
    bottom=h-int(102*h/720); top=bottom-line_height*len(lines)-int(20*scale)
    draw.rounded_rectangle((int(w*.06),top,int(w*.94),bottom),radius=max(4,int(10*scale)),fill='#061523',outline='#ffd447',width=2)
    for i,line in enumerate(lines):
        draw.text((w//2,top+int(10*scale)+i*line_height),line,font=f,fill='#ffffff',anchor='mt')
    return image
