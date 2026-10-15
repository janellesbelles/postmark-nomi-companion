"""Eight original, local Canvas header scenes. No images, identity, or network."""
from __future__ import annotations

CAPTIONS = {
    'Lamplit Desk': 'Letters find their way by lamplight.',
    'Rosewater': 'A little rose-colored correspondence.',
    'Moonflower': 'For thoughts that bloom after dark.',
    'Moss & Mail': 'Good words take the woodland path.',
    'Tidepool': 'A small letter, a wide-open sea.',
    'Ember & Ink': 'Keep a little warmth between the lines.',
    'Sprocket Pocket Sunshine': 'Tiny robots. Pocket-sized sunshine.',
    'Evermoon': 'Two moons. A thousand little stories.',
}


class Scene:
    """Draw in a 1000x100 space, responsive to both mail/live header sizes."""
    def __init__(self, canvas, width, height, colors):
        self.c, self.sx, self.sy, self.p = canvas, width/1000, height/100, colors

    def points(self, values):
        return [v*(self.sx if i%2==0 else self.sy) for i,v in enumerate(values)]

    def rect(self, *xy, fill, outline='', **kw):
        return self.c.create_rectangle(*self.points(xy),fill=fill,outline=outline,**kw)

    def oval(self, *xy, fill, outline='', **kw):
        return self.c.create_oval(*self.points(xy),fill=fill,outline=outline,**kw)

    def disc(self, x, y, radius, fill, **kw):
        # Keep celestial bodies round even in a narrow mailbox or preview tile.
        cx,cy,r=x*self.sx,y*self.sy,radius*self.sy
        return self.c.create_oval(cx-r,cy-r,cx+r,cy+r,fill=fill,outline='',**kw)

    def poly(self, *xy, fill, outline='', **kw):
        return self.c.create_polygon(*self.points(xy),fill=fill,outline=outline,**kw)

    def line(self, *xy, fill, **kw):
        return self.c.create_line(*self.points(xy),fill=fill,**kw)

    def stars(self, color):
        for x,y in ((60,16),(170,29),(286,12),(438,23),(571,13),(691,25),(826,10),(964,32)):
            self.oval(x-1,y-1,x+1,y+1,fill=color)
        for x,y in ((341,20),(771,18)):
            self.line(x-3,y,x+3,y,fill=color)
            self.line(x,y-3,x,y+3,fill=color)

    def flower(self, x, y, color, radius=6):
        for dx,dy in ((-radius,0),(radius,0),(0,-radius),(0,radius)):
            self.oval(x+dx-radius,y+dy-radius,x+dx+radius,y+dy+radius,fill=color)
        self.oval(x-3,y-3,x+3,y+3,fill=self.p['PAPER'])

    def envelope(self, x, y, size=40):
        self.rect(x,y,x+size,y+size*.55,fill=self.p['PAPER'],outline=self.p['GOLD'])
        self.line(x,y,x+size/2,y+size*.3,x+size,y,fill=self.p['LINE'])

    def lamplit(self):
        p=self.p
        self.stars(p['GOLD'])
        self.disc(754,23.5,12.5,p['PAPER'])
        self.poly(0,50,270,67,540,100,0,100,fill=p['NIGHT'])
        self.poly(400,100,735,42,1000,34,1000,100,fill=p['SOFT'])
        self.poly(717,70,605,100,710,100,756,70,fill=p['LINE'])
        for x,y in ((491,68),(520,65),(550,71)):
            self.poly(x,y-18,x-10,y+9,x+10,y+9,fill=p['SIDEBAR'])
        self.rect(852,51,900,85,fill=p['NIGHT'],outline=p['LINE'])
        self.poly(845,51,876,25,907,51,fill=p['NIGHT'],outline=p['LINE'])
        for x in (862,882): self.rect(x,61,x+7,71,fill=p['GOLD'])
        self.line(675,54,675,94,fill=p['GOLD'],width=2)
        self.rect(668,43,682,61,fill=p['GOLD'],outline=p['PAPER'])

    def rosewater(self):
        p=self.p
        # Scalloped stationery and a sealed envelope, framed with climbing roses.
        self.poly(520,25,885,12,916,89,544,98,fill=p['SOFT'],outline=p['LINE'])
        self.poly(560,18,897,28,880,94,548,82,fill=p['PAPER'])
        for x in range(564,884,24): self.oval(x,16,x+24,30,fill=p['PAPER'])
        for y in (40,51,62): self.line(595,y,780,y+5,fill=p['LINE'])
        self.envelope(815,47,64)
        self.oval(841,61,854,74,fill=p['GOLD'],outline=p['LINE'])
        for x in (42,460,957):
            self.line(x-12,91,x+8,55,x-5,19,fill=p['GREEN'],width=2,smooth=True)
            for dx,dy in ((-7,73),(8,47)):
                self.oval(x+dx-11,dy-4,x+dx+3,dy+3,fill=p['GREEN'])
            self.flower(x,24,p['GOLD'],7)
            self.flower(x-8,67,p['LINE'],5)

    def moonflower(self):
        p=self.p
        self.stars(p['GOLD'])
        self.disc(737,29.5,22.5,p['GOLD'])
        self.disc(750,22,22,p['PANEL'])
        self.poly(0,92,390,74,680,88,1000,68,1000,100,0,100,fill=p['SOFT'])
        for x,y in ((527,68),(623,55),(835,59),(916,71)):
            self.line(x,y+24,x-4,y,x+7,y-8,fill=p['GREEN'],width=2,smooth=True)
            self.poly(x-15,y,x-5,y-6,x,y-16,x+5,y-6,x+15,y,x+6,y+4,x,y+12,x-6,y+4,
                      fill=p['GOLD'],outline=p['PAPER'])
            self.oval(x-2,y-2,x+2,y+2,fill=p['PAPER'])

    def moss(self):
        p=self.p
        for x,scale in ((45,1),(480,.8),(948,1.2)):
            self.line(x,88,x+10,24,fill=p['GREEN'],width=2)
            for y in (36,48,60,72):
                self.poly(x+8,y,x-22*scale,y-10,x-6,y+7,fill=p['GREEN'])
                self.poly(x+8,y,x+31*scale,y-13,x+15,y+7,fill=p['LINE'])
        self.poly(385,100,605,69,1000,82,1000,100,fill=p['SOFT'])
        self.rect(741,43,750,93,fill=p['GOLD'])
        self.rect(716,26,787,61,fill=p['LINE'],outline=p['GREEN'])
        self.poly(708,26,751,10,795,26,fill=p['GREEN'])
        self.envelope(731,34,30)
        self.line(781,30,781,12,803,12,fill=p['GOLD'],width=2)
        for x,y,s in ((581,82,1),(863,89,.8),(631,94,.6)):
            self.rect(x-4*s,y-12*s,x+4*s,y+4*s,fill=p['PAPER'])
            self.oval(x-17*s,y-28*s,x+17*s,y-6*s,fill=p['GOLD'])
            self.rect(x-18*s,y-15*s,x+18*s,y-5*s,fill=p['SOFT'])
            for dx in (-7,6): self.oval(x+dx-2,y-21*s,x+dx+2,y-17*s,fill=p['PAPER'])

    def tidepool(self):
        p=self.p
        self.disc(682,23.5,13.5,p['PAPER'])
        for y,color in ((58,p['SOFT']),(74,p['LINE']),(91,p['GREEN'])):
            self.line(0,y,170,y-10,360,y+6,550,y-9,760,y+5,1000,y-8,fill=color,width=3,smooth=True)
        self.poly(837,99,847,43,872,43,885,99,fill=p['PAPER'],outline=p['GOLD'])
        self.rect(845,31,875,44,fill=p['GOLD'])
        self.poly(841,31,860,18,879,31,fill=p['LINE'])
        self.line(850,63,875,63,fill=p['LINE'],width=4)
        self.poly(841,34,729,11,741,51,fill=p['SOFT'])
        for x,y in ((535,80),(962,76)):
            self.poly(x-23,y,x-18,y-17,x,y-28,x+18,y-17,x+23,y,x,y+7,fill=p['GOLD'],outline=p['PAPER'])
            for dx in (-14,0,14): self.line(x,y+5,x+dx,y-18,fill=p['LINE'])

    def ember(self):
        p=self.p
        self.rect(0,85,1000,100,fill=p['SOFT'])
        # Candle, paper, fountain pen, and inkpot rather than another landscape.
        self.oval(542,6,648,90,fill=p['SOFT'])
        self.rect(580,43,610,88,fill=p['PAPER'],outline=p['GOLD'])
        self.line(595,34,595,44,fill=p['LINE'],width=2)
        self.poly(595,8,584,27,595,36,605,26,fill=p['GOLD'])
        self.poly(595,18,591,28,595,32,600,27,fill=p['PAPER'])
        self.oval(566,84,624,92,fill=p['GOLD'])
        self.poly(667,48,831,54,808,91,643,83,fill=p['PAPER'])
        for y in (61,70,79): self.line(680,y,785,y+5,fill=p['LINE'])
        self.rect(859,63,901,89,fill=p['LINE'],outline=p['GOLD'])
        self.rect(868,57,892,65,fill=p['NIGHT'],outline=p['GOLD'])
        self.line(882,64,929,18,fill=p['GOLD'],width=4)
        self.poly(917,28,937,10,930,31,fill=p['PAPER'])
        for x,y in ((485,21),(651,18),(955,50),(724,24)):
            self.oval(x-2,y-3,x+2,y+3,fill=p['GOLD'])

    def robot(self, x, y, body, trim):
        dark='#31576b'
        self.line(x,y-17,x,y-24,fill=dark,width=2)
        self.oval(x-3,y-28,x+3,y-22,fill=trim)
        self.rect(x-12,y-17,x+12,y+1,fill=body,outline=dark)
        for dx in (-5,5): self.oval(x+dx-2,y-11,x+dx+2,y-7,fill=dark)
        self.line(x-3,y-3,x+3,y-3,fill=dark)
        self.rect(x-9,y+3,x+9,y+19,fill=body,outline=dark)
        self.oval(x-3,y+7,x+3,y+13,fill=trim)
        for dx in (-6,6): self.line(x+dx,y+19,x+dx,y+25,fill=dark,width=2)
        self.line(x-9,y+6,x-17,y+13,fill=dark,width=2)
        self.line(x+9,y+6,x+17,y+1,fill=dark,width=2)

    def sunshine(self):
        # Intentional decorative pastels; text uses contrast-tested role colors.
        sky,blue,peach,butter,mint='#dceef6','#91b8cb','#f1b5a5','#f8d98c','#a7cdb6'
        self.rect(0,0,1000,100,fill=sky)
        self.disc(458.5,31,21,butter)
        for x,y in ((521,18),(765,13)):
            self.oval(x-22,y-7,x+22,y+8,fill='#fffdf3')
            self.oval(x-8,y-14,x+12,y+8,fill='#fffdf3')
        self.poly(0,90,400,80,700,89,1000,76,1000,100,0,100,fill=mint)
        for x,roof in ((572,peach),(731,blue),(898,butter)):
            self.rect(x-24,48,x+24,92,fill='#fff5da',outline=blue)
            self.poly(x-31,48,x,27,x+31,48,fill=roof)
            self.rect(x-6,72,x+7,92,fill=roof)
            self.oval(x-8,53,x+8,66,fill=blue,outline='#fffdf3')
        self.robot(646,62,blue,peach)
        self.robot(831,64,peach,butter)
        self.robot(954,50,mint,butter)
        self.envelope(664,64,25)
        self.envelope(800,70,24)

    def evermoon(self):
        p=self.p
        self.stars(p['GOLD'])
        self.disc(679,26.5,20.5,p['GOLD'],tags='moon')
        self.disc(738,23.5,11.5,p['PAPER'],tags='moon')
        self.poly(355,72,454,41,493,57,552,29,625,63,696,48,774,72,876,32,1000,61,1000,100,355,100,fill=p['LINE'])
        self.poly(355,83,492,62,559,76,650,62,765,84,864,57,1000,76,1000,100,355,100,fill=p['SOFT'])
        self.poly(444,100,598,78,812,78,1000,98,1000,100,fill=p['NIGHT'])
        for x,y,width in ((680,82,23),(681,88,34),(680,94,44),(738,82,10),(740,89,18)):
            self.line(x-width/2,y,x+width/2,y,fill=p['GOLD'])
        self.line(793,20,817,14,838,25,871,19,fill=p['LINE'])
        for x,y in ((793,20),(817,14),(838,25),(871,19)):
            self.oval(x-1,y-1,x+1,y+1,fill=p['PAPER'])


SCENES = {
    'Lamplit Desk':'lamplit', 'Rosewater':'rosewater', 'Moonflower':'moonflower',
    'Moss & Mail':'moss', 'Tidepool':'tidepool', 'Ember & Ink':'ember',
    'Sprocket Pocket Sunshine':'sunshine', 'Evermoon':'evermoon',
}


def draw(canvas, name, width, height, colors, caption=None):
    scene=Scene(canvas,width,height,colors)
    scene.rect(0,0,1000,100,fill=colors['PANEL'])
    getattr(scene,SCENES.get(name,'lamplit'))()
    # Opaque caption tab guarantees contrast against every scene, including the
    # light robot village. Decorations cannot obscure the resident or controls.
    tab_width=min(width-10,max(200,width*.43))
    canvas.create_rectangle(12,height-46,tab_width,height-6,
                            fill=colors['NIGHT'],outline='')
    canvas.create_text(22,height-26,text=caption or CAPTIONS.get(name,CAPTIONS['Lamplit Desk']),
                       anchor='w',fill=colors['TEXT'],font=('Georgia',11,'italic'),
                       width=max(80,tab_width-32))
