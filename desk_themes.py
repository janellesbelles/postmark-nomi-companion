"""Local presentation palettes. No credentials, network, or resident state."""
from __future__ import annotations

ROLES = ('NIGHT','PANEL','SIDEBAR','SOFT','LINE','TEXT','MUTED','GOLD',
         'PAPER','PAPER_INK','GREEN')
THEMES = {
    'Lamplit Desk': ('#101d2b','#172838','#142431','#1c3040','#344655','#e7e9e4','#b6c4cb','#e3bc7d','#f3ead9','#302e2a','#aad1b9'),
    'Rosewater': ('#301b2b','#422536','#382030','#583047','#7d5169','#fff0f5','#e4bdcd','#ffb5d2','#fff0f3','#4b2436','#c8dfc7'),
    'Moonflower': ('#211c36','#302944','#27213d','#403454','#625372','#f4eeff','#c9bddd','#d9b8ff','#f2edf9','#362843','#bed6db'),
    'Moss & Mail': ('#19291f','#24382c','#1e3025','#324b38','#506957','#edf3df','#c1ceb5','#d5ce8c','#f3f0df','#2c382a','#b2d8a3'),
    'Tidepool': ('#122b32','#1a3b43','#16333a','#254d56','#466e75','#e5f7f4','#b4d6d5','#98e0d6','#f6efdf','#263e43','#b9dfbc'),
    'Ember & Ink': ('#28211f','#382e29','#302723','#4a3930','#71564a','#fff0df','#ddc2ae','#f0b17f','#faecd9','#493024','#d2d5ac'),
}
DEFAULT = 'Lamplit Desk'


def palette(name):
    return dict(zip(ROLES, THEMES.get(name, THEMES[DEFAULT])))


def load_theme(profiles):
    name = profiles.index.get('ui_theme', DEFAULT)
    return name if name in THEMES else DEFAULT


def save_theme(profiles, name):
    if name not in THEMES:
        raise ValueError('Unknown desk theme.')
    previous = profiles.index.get('ui_theme')
    profiles.index['ui_theme'] = name
    try:
        profiles.save()
    except Exception:
        if previous is None:
            profiles.index.pop('ui_theme', None)
        else:
            profiles.index['ui_theme'] = previous
        raise


def recolor(root, old, new):
    """Update existing widgets in place: never rebuild drafts or live workers."""
    colors = {old[role].lower():new[role] for role in ROLES}
    # Remaining original dialog colors are mapped to presentation roles only.
    for color, role in {'#171526':'NIGHT','#242139':'PANEL','#f2edff':'TEXT',
                        '#bab0d1':'MUTED','#99ffff':'GOLD','#d9b8ff':'GOLD',
                        '#62377d':'SOFT','#49415f':'SOFT','#575072':'SOFT'}.items():
        colors.setdefault(color, new[role])
    def walk(widget):
        options = widget.keys()
        for option in ('background','foreground','activebackground','activeforeground',
                       'insertbackground','selectbackground','selectforeground',
                       'selectcolor','highlightbackground','disabledforeground'):
            if option in options:
                color = str(widget.cget(option)).lower()
                if color in colors:
                    widget.configure(**{option:colors[color]})
        # Canvas art is redrawn from the current palette, not transformed twice.
        if hasattr(widget, 'redraw'):
            widget.redraw()
        for child in widget.winfo_children():
            walk(child)
    walk(root)
