"""Тоглолтын бүх самбарын график карт — Pillow-оор PNG зураг рендерлэнэ.

Самбар бүр: HUD хүрээ, 2 мөр гарчиг, агуулга, footer.
assets/maps/ дотор газрын зураг байвал veto/ready картад фото харагдана.
ТЭМДЭГЛЭЛ: Pillow эможи зурдаггүй тул бүх дүрсийг шугамаар зурна.
"""

import io
import os
import math
from PIL import Image, ImageDraw, ImageFont

# ----- өнгө -----
BG_TOP = (30, 32, 41)
BG_BOT = (9, 9, 13)
PANEL  = (24, 26, 33)
ROW    = (31, 33, 42)
CARD   = (26, 28, 36)
LINE   = (54, 56, 67)
ORANGE = (255, 96, 20)
BLUE   = (54, 140, 240)
WHITE  = (240, 241, 244)
DIM    = (160, 162, 172)
SUB    = (108, 110, 122)
GOLD   = (238, 192, 84)
GREEN  = (60, 200, 120)
RED    = (231, 76, 60)

W = 900
_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_FONTS = os.path.join(_ASSETS, "fonts")

_MAP_FILE = {
    "Ancient": "ancient", "Anubis": "anubis", "Dust II": "dust2",
    "Inferno": "inferno", "Mirage": "mirage", "Nuke": "nuke",
    "Overpass": "overpass", "Train": "train", "Cache": "cache",
    "Vertigo": "vertigo",
}


def _f(name, size):
    """Open a font from bundled assets/fonts/ first, then OS fallback.

    Bundled (e.g. arialbd.ttf) ships with the bot so Cyrillic Ү/Ө
    render correctly on Linux containers that lack Arial Bold.
    """
    bundled = os.path.join(_FONTS, name)
    try:
        return ImageFont.truetype(bundled, size)
    except OSError:
        pass
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def _tw(d, text, font):
    b = d.textbbox((0, 0), text, font=font)
    return b[2] - b[0]


def _ct(d, cx, y, text, font, fill):
    d.text((cx - _tw(d, text, font) / 2, y), text, font=font, fill=fill)


def _star(d, cx, cy, r, fill):
    pts = []
    for i in range(10):
        th = math.radians(-90 + i * 36)
        rad = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rad * math.cos(th), cy + rad * math.sin(th)))
    d.polygon(pts, fill=fill)


def _person(d, cx, cy, h, color):
    hr = h * 0.30
    d.ellipse([cx - hr, cy - h * 0.52, cx + hr, cy - h * 0.52 + 2 * hr],
              fill=color)
    d.pieslice([cx - h * 0.54, cy - h * 0.04, cx + h * 0.54, cy + h * 1.05],
               180, 360, fill=color)


def _chip(d, x, y, s, color):
    d.rounded_rectangle([x, y, x + s, y + s], radius=5, fill=color)


def _check(d, cx, cy, r, color):
    d.line([(cx - r, cy + r * 0.1), (cx - r * 0.25, cy + r * 0.8),
            (cx + r, cy - r * 0.7)], fill=color, width=5, joint="curve")


def _cross(d, cx, cy, r, color):
    d.line([(cx - r, cy - r), (cx + r, cy + r)], fill=color, width=5)
    d.line([(cx - r, cy + r), (cx + r, cy - r)], fill=color, width=5)


def _dot(d, cx, cy, r, color):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def _badge(d, cx, cy, r, accent, label):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=PANEL,
              outline=accent, width=5)
    d.ellipse([cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12],
              outline=accent, width=2)
    f = _f("arialbd.ttf", int(r * 0.92))
    b = d.textbbox((0, 0), label, font=f)
    d.text((cx - (b[2] - b[0]) / 2 - b[0], cy - (b[3] - b[1]) / 2 - b[1]),
           label, font=f, fill=accent)


def _gradient(img):
    w, h = img.size
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t)
                  for i in range(3))
        d.line([(0, y), (w, y)], fill=c)


def _corner(d, x, y, dx, dy, ln, color, w):
    d.line([(x, y), (x + dx * ln, y)], fill=color, width=w)
    d.line([(x, y), (x, y + dy * ln)], fill=color, width=w)


def _base(h, title1, title2, match_time=None):
    """Суурь зураг — градиент, HUD хүрээ, 2 мөр гарчиг, footer."""
    img = Image.new("RGB", (W, h), BG_BOT)
    _gradient(img)
    d = ImageDraw.Draw(img)
    for cx, cy, dx, dy in ((26, 26, 1, 1), (W - 26, 26, -1, 1),
                           (26, h - 26, 1, -1), (W - 26, h - 26, -1, -1)):
        _corner(d, cx, cy, dx, dy, 64, ORANGE, 3)
    d.rectangle([0, 0, W, 5], fill=ORANGE)
    d.rectangle([0, h - 5, W, h], fill=ORANGE)
    _ct(d, W / 2, 44, "C O U N T E R - S T R I K E   2",
        _f("arialbd.ttf", 21), SUB)
    d.text((W - 134, 38), "CS2", font=_f("arialbd.ttf", 34), fill=ORANGE)
    _ct(d, W / 2, 72, title1, _f("arialbd.ttf", 78), WHITE)
    if title2:
        _ct(d, W / 2, 168, title2, _f("arialbd.ttf", 78), ORANGE)
    foot = "CS2 · 5v5 MATCH"
    if match_time is not None:
        foot = (match_time.strftime("%Y-%m-%d   %H:%M")
                + "   ·   УЛААНБААТАР")
    _ct(d, W / 2, h - 48, foot, _f("arialbd.ttf", 19), SUB)
    return img, d


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _section(d, cx, y, text):
    _ct(d, cx, y, text, _f("arialbd.ttf", 33), WHITE)
    d.line([cx - 56, y + 46, cx + 56, y + 46], fill=ORANGE, width=3)


def _load_cover(path, w, h):
    im = Image.open(path).convert("RGB")
    iw, ih = im.size
    sc = max(w / iw, h / ih)
    im = im.resize((max(int(iw * sc), w), max(int(ih * sc), h)))
    iw, ih = im.size
    l, t = (iw - w) // 2, (ih - h) // 2
    return im.crop((l, t, l + w, t + h))


def _map_image(map_name, w, h):
    base = _MAP_FILE.get(map_name)
    if not base:
        return None
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = os.path.join(_ASSETS, "maps", base + ext)
        if os.path.exists(p):
            try:
                return _load_cover(p, w, h)
            except Exception:
                return None
    return None


def _name(p, limit=15):
    return p.name if len(p.name) <= limit else p.name[:limit - 1] + "…"


def _prow(d, x0, x1, y, accent, player, is_cap):
    d.rounded_rectangle([x0, y, x1, y + 58], radius=11, fill=ROW,
                        outline=LINE, width=1)
    if is_cap:
        d.rounded_rectangle([x0, y, x0 + 6, y + 58], radius=2, fill=accent)
    _person(d, x0 + 40, y + 27, 26, accent if is_cap else SUB)
    d.text((x0 + 70, y + 13), _name(player), font=_f("arialbd.ttf", 29),
           fill=WHITE)
    fr = _f("arialbd.ttf", 24)
    col = accent if is_cap else DIM
    rt = f"{player.rating}"
    _star(d, x1 - 28, y + 30, 9, col)
    d.text((x1 - 46 - _tw(d, rt, fr), y + 16), rt, font=fr, fill=col)


def _vs_header(d, cy, t1, t2):
    """T/CT badge + TEAM A/B нэр + AVG + VS — нэг хэвтээ толгой band."""
    a1 = round(t1.total_rating / max(len(t1.players), 1), 1)
    a2 = round(t2.total_rating / max(len(t2.players), 1), 1)
    _badge(d, 116, cy, 56, ORANGE, "T")
    _badge(d, W - 116, cy, 56, BLUE, "CT")
    fteam = _f("arialbd.ttf", 46)
    favg = _f("arialbd.ttf", 26)
    d.text((202, cy - 40), "TEAM A", font=fteam, fill=WHITE)
    at1 = f"AVG {a1}"
    d.text((204, cy + 14), at1, font=favg, fill=ORANGE)
    _star(d, 204 + _tw(d, at1, favg) + 13, cy + 26, 9, ORANGE)
    tb = "TEAM B"
    d.text((W - 202 - _tw(d, tb, fteam), cy - 40), tb, font=fteam, fill=WHITE)
    at2 = f"AVG {a2}"
    d.text((W - 204 - _tw(d, at2, favg) - 22, cy + 14), at2, font=favg,
           fill=BLUE)
    _star(d, W - 204 - 9, cy + 26, 9, BLUE)
    _ct(d, W / 2, cy - 32, "VS", _f("arialbd.ttf", 52), GOLD)


def _player_col(d, x0, x1, y0, accent, players, cap_id):
    """Тоглогчдын багана (толгойгүй мөрүүд). Доод y-г буцаана."""
    ry = y0
    for p in sorted(players, key=lambda x: -x.rating):
        _prow(d, x0, x1, ry, accent, p, p.id == cap_id)
        ry += 66
    return ry


# ==================== САМБАР БҮР ====================

def render_registration(session, mt=None):
    players = session.players
    reserves = session.reserves
    rows = max(len(players), 1)
    h = 300 + 70 + rows * 66 + (96 if reserves else 0) + 130
    img, d = _base(h, "BAZAA HAZAA", "БҮРТГЭЛ", mt)
    _section(d, W / 2, 300, f"ТОГЛОГЧИД   {len(players)} / 10")
    y = 372
    if players:
        for i, p in enumerate(players, 1):
            d.rounded_rectangle([140, y, W - 140, y + 58], radius=11,
                                fill=ROW, outline=LINE, width=1)
            d.text((164, y + 14), f"{i:>2}", font=_f("arialbd.ttf", 26),
                   fill=ORANGE)
            _person(d, 232, y + 27, 26, SUB)
            d.text((266, y + 13), _name(p, 22), font=_f("arialbd.ttf", 29),
                   fill=WHITE)
            fr = _f("arialbd.ttf", 24)
            if p.is_rated:
                rt = f"{p.rating}"
                _star(d, W - 164 - 9, y + 30, 9, DIM)
                d.text((W - 164 - 26 - _tw(d, rt, fr), y + 16), rt,
                       font=fr, fill=DIM)
            else:
                d.text((W - 164 - _tw(d, "—", fr), y + 16), "—",
                       font=fr, fill=SUB)
            y += 66
    else:
        d.text((164, y + 12), "Хоосон — НЭГДЭХ товчоор бүртгүүлнэ үү.",
               font=_f("arialbd.ttf", 26), fill=SUB)
        y += 66
    if reserves:
        rv = "   ·   ".join(_name(p, 12) for p in reserves)
        d.text((140, y + 14), "НӨӨЦ:", font=_f("arialbd.ttf", 24),
               fill=ORANGE)
        d.text((250, y + 16), rv, font=_f("arialbd.ttf", 24), fill=DIM)
        y += 96
    y += 16
    if session.is_full:
        msg, col = "10+ ТОГЛОГЧ БЭЛЭН  —  ЭХЛҮҮЛЭХ ТОВЧ ДАРНА", GREEN
    else:
        msg, col = f"ДАХИН  {10 - len(players)}  ТОГЛОГЧ ХЭРЭГТЭЙ", SUB
    _ct(d, W / 2, y, msg, _f("arialbd.ttf", 28), col)
    return _png(img)


def render_division(session, mt=None):
    c1, c2 = session.captain1, session.captain2
    img, d = _base(300 + 600, "БАГ", "ХУВААХ", mt)
    _section(d, W / 2, 300, "АХЛАГЧИД")
    by = 396
    _badge(d, 180, by, 56, ORANGE, "T")
    _badge(d, W - 180, by, 56, BLUE, "CT")
    _ct(d, W / 2, by - 26, "VS", _f("arialbd.ttf", 50), GOLD)
    _ct(d, 180, by + 78, "TEAM A", _f("arialbd.ttf", 30), WHITE)
    _ct(d, 180, by + 118, _name(c1, 14), _f("arialbd.ttf", 24), ORANGE)
    _ct(d, W - 180, by + 78, "TEAM B", _f("arialbd.ttf", 30), WHITE)
    _ct(d, W - 180, by + 118, _name(c2, 14), _f("arialbd.ttf", 24), BLUE)
    y = 612
    for name, desc in (("DRAFT", "ахлагч ээлжлэн сонгоно"),
                       ("RANDOM", "үнэлгээгээр тэнцвэртэй санамсаргүй"),
                       ("MANUAL", "ахлагчид гараар хуваана")):
        d.rounded_rectangle([150, y, W - 150, y + 58], radius=11,
                            fill=ROW, outline=LINE, width=1)
        _chip(d, 176, y + 18, 22, ORANGE)
        d.text((216, y + 13), name, font=_f("arialbd.ttf", 28), fill=ORANGE)
        d.text((408, y + 17), desc, font=_f("arialbd.ttf", 20), fill=DIM)
        y += 70
    return _png(img)


def render_draft(session, mt=None):
    dr = session.draft
    img, d = _base(300 + 700, "БАГ ХУВААХ", "DRAFT", mt)
    _vs_header(d, 364, dr.team1, dr.team2)
    yb1 = _player_col(d, 70, 438, 448, ORANGE, dr.team1.players,
                      session.captain1.id)
    yb2 = _player_col(d, 462, W - 70, 448, BLUE, dr.team2.players,
                      session.captain2.id)
    y = max(yb1, yb2) + 16
    if dr.available:
        pool = "   ·   ".join(f"{_name(p, 10)} {p.rating}"
                              for p in sorted(dr.available,
                                              key=lambda x: -x.rating))
        _ct(d, W / 2, y, f"СОНГОГДООГҮЙ  ({len(dr.available)})",
            _f("arialbd.ttf", 24), SUB)
        _ct(d, W / 2, y + 36, pool, _f("arialbd.ttf", 22), DIM)
        y += 84
    if not dr.is_complete:
        cap = (session.captain1 if dr.current_captain == 1
               else session.captain2)
        side = "TEAM A" if dr.current_captain == 1 else "TEAM B"
        _ct(d, W / 2, y, f"СОНГОХ ЭЭЛЖ:  {side}  —  {_name(cap, 16)}",
            _f("arialbd.ttf", 26), WHITE)
    return _png(img)


def render_manual(session, mt=None):
    m = session.manual
    img, d = _base(300 + 700, "БАГ ХУВААХ", "ГАР ХУВААЛТ", mt)
    cap1 = m.team1.players[0].id if m.team1.players else None
    cap2 = m.team2.players[0].id if m.team2.players else None
    _vs_header(d, 364, m.team1, m.team2)
    yb1 = _player_col(d, 70, 438, 448, ORANGE, m.team1.players, cap1)
    yb2 = _player_col(d, 462, W - 70, 448, BLUE, m.team2.players, cap2)
    y = max(yb1, yb2) + 16
    if m.unassigned:
        pool = "   ·   ".join(f"{_name(p, 10)} {p.rating}"
                              for p in sorted(m.unassigned,
                                              key=lambda x: -x.rating))
        _ct(d, W / 2, y, f"ХУВААРИЛААГҮЙ  ({len(m.unassigned)})",
            _f("arialbd.ttf", 24), SUB)
        _ct(d, W / 2, y + 36, pool, _f("arialbd.ttf", 22), DIM)
    else:
        _ct(d, W / 2, y, "БҮХ ТОГЛОГЧ ХУВААРИЛАГДЛАА — БАТАЛГААЖУУЛНА",
            _f("arialbd.ttf", 25), GREEN)
    return _png(img)


def render_teams(session, mt=None, title2="ХУВААГДЛАА"):
    t1, t2 = session.teams
    img, d = _base(300 + 600, "БАГУУД", title2, mt)
    c1 = t1.captain.id if t1.captain else None
    c2 = t2.captain.id if t2.captain else None
    _vs_header(d, 372, t1, t2)
    _player_col(d, 70, 438, 456, ORANGE, t1.players, c1)
    _player_col(d, 462, W - 70, 456, BLUE, t2.players, c2)
    diff = round(abs(t1.total_rating - t2.total_rating), 2)
    _ct(d, W / 2, 300 + 600 - 92, f"Багуудын зөрүү:  {diff} од",
        _f("arialbd.ttf", 24), SUB)
    return _png(img)


def render_veto(session, mt=None):
    from config import MAP_POOL
    v = session.veto
    h = 300 + len(MAP_POOL) * 70 + 180
    img, d = _base(h, "ГАЗРЫН", "VETO", mt)
    roll = session.veto_first_roll
    if roll and roll.get("history"):
        a, b = roll["history"][-1]
        _ct(d, W / 2, 296, f"ШОО:  TEAM A [{a}]    TEAM B [{b}]",
            _f("arialbd.ttf", 24), SUB)
    banned = {m: t for t, m in v.bans}
    picked = {m: t for t, m in v.picks}
    y = 344
    for m in MAP_POOL:
        if m in picked:
            acc, txt = GREEN, f"TEAM {'A' if picked[m] == 1 else 'B'} PICK"
            fill, kind = ROW, "pick"
        elif m in banned:
            acc, txt = RED, f"TEAM {'A' if banned[m] == 1 else 'B'} BAN"
            fill, kind = (20, 18, 20), "ban"
        else:
            acc, txt, fill, kind = SUB, "", ROW, "open"
        d.rounded_rectangle([200, y, W - 200, y + 58], radius=11,
                            fill=fill, outline=acc if txt else LINE, width=2)
        d.rounded_rectangle([200, y, 207, y + 58], radius=2, fill=acc)
        if kind == "pick":
            _check(d, 244, y + 28, 13, acc)
        elif kind == "ban":
            _cross(d, 244, y + 29, 11, acc)
        else:
            _dot(d, 244, y + 29, 6, acc)
        col = SUB if kind == "ban" else WHITE
        d.text((286, y + 13), m.upper(), font=_f("arialbd.ttf", 28), fill=col)
        if txt:
            ft = _f("arialbd.ttf", 22)
            d.text((W - 230 - _tw(d, txt, ft), y + 18), txt, font=ft,
                   fill=acc)
        y += 70
    y += 16
    if not v.is_complete:
        team = v.current_team
        cap = session.captain1 if team == 1 else session.captain2
        act = "PICK (сонгох)" if v.current_action == "pick" else "BAN (хасах)"
        side = "TEAM A" if team == 1 else "TEAM B"
        _ct(d, W / 2, y, f"ЭЭЛЖ:  {side}  —  {act}  —  {_name(cap, 14)}",
            _f("arialbd.ttf", 26), WHITE)
    else:
        _ct(d, W / 2, y, "VETO ДУУСЛАА", _f("arialbd.ttf", 28), GREEN)
    return _png(img)


def _map_cards(d, img, session, y0):
    cw, gap = 260, 20
    x = (W - 3 * cw - 2 * gap) / 2
    cy1 = y0 + 300
    photo_h = 132
    for idx, m in enumerate(session.maps):
        x0 = x + idx * (cw + gap)
        x1 = x0 + cw
        picker = session.veto.map_picker(m)
        if picker == 1:
            acc, origin = ORANGE, "TEAM A PICK"
        elif picker == 2:
            acc, origin = BLUE, "TEAM B PICK"
        else:
            acc, origin = GOLD, "DECIDER MAP"
        d.rounded_rectangle([x0, y0, x1, cy1], radius=14, fill=CARD,
                            outline=acc, width=2)
        photo = _map_image(m, cw - 8, photo_h)
        if photo is not None:
            mask = Image.new("L", photo.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, photo.size[0], photo.size[1]], radius=11, fill=255)
            img.paste(photo, (int(x0 + 4), int(y0 + 4)), mask)
        else:
            d.rounded_rectangle([x0 + 4, y0 + 4, x1 - 4, y0 + 4 + photo_h],
                                radius=11, fill=(36, 38, 47))
            _ct(d, (x0 + x1) / 2, y0 + 4 + photo_h / 2 - 18, m.upper(),
                _f("arialbd.ttf", 34), (70, 72, 84))
        d.rounded_rectangle([x0 + 12, y0 + 12, x0 + 52, y0 + 52],
                            radius=8, fill=acc)
        _ct(d, x0 + 32, y0 + 15, str(idx + 1), _f("arialbd.ttf", 28), BG_BOT)
        _ct(d, (x0 + x1) / 2, y0 + photo_h + 20, m.upper(),
            _f("arialbd.ttf", 34), WHITE)
        _ct(d, (x0 + x1) / 2, y0 + photo_h + 62, origin,
            _f("arialbd.ttf", 19), acc)
        t_team = session.sides.get(m, 1)
        ct_team = 2 if t_team == 1 else 1
        mx = (x0 + x1) / 2
        d.line([x0 + 28, cy1 - 76, x1 - 28, cy1 - 76], fill=LINE, width=2)
        d.line([mx, cy1 - 68, mx, cy1 - 16], fill=LINE, width=2)
        _ct(d, (x0 + mx) / 2, cy1 - 64, "T", _f("arialbd.ttf", 20), ORANGE)
        _ct(d, (x0 + mx) / 2, cy1 - 38,
            "TEAM A" if t_team == 1 else "TEAM B", _f("arialbd.ttf", 19), DIM)
        _ct(d, (mx + x1) / 2, cy1 - 64, "CT", _f("arialbd.ttf", 20), BLUE)
        _ct(d, (mx + x1) / 2, cy1 - 38,
            "TEAM A" if ct_team == 1 else "TEAM B", _f("arialbd.ttf", 19),
            DIM)


def render_ready(session, mt=None):
    t1, t2 = session.teams
    img, d = _base(300 + 600 + 470, "BAZAA HAZAA", "BELEN BOLLOO!", mt)
    c1 = t1.captain.id if t1.captain else None
    c2 = t2.captain.id if t2.captain else None
    _vs_header(d, 372, t1, t2)
    _player_col(d, 70, 438, 456, ORANGE, t1.players, c1)
    _player_col(d, 462, W - 70, 456, BLUE, t2.players, c2)
    _section(d, W / 2, 838, "ТОГЛОХ  3  ГАЗАР")
    _map_cards(d, img, session, 910)
    return _png(img)


def render_betting(rnd, banks, mt=None):
    """Бооцооны самбар — баг тус бүрийг 2 тал болгож, харалдаа хослуулна."""
    from betting import UNPAID, SETTLED, DEBT
    rows = len(rnd.bets)
    has_win = rnd.winner_team is not None
    top = 432 if has_win else 374
    h = top + rows * 116 + 130
    img, d = _base(h, "БООЦОО", "BO3", mt)
    if has_win:
        wn = "TEAM A" if rnd.winner_team == 1 else "TEAM B"
        wc = ORANGE if rnd.winner_team == 1 else BLUE
        _ct(d, W / 2, 296, f"WINNER  —  {wn}", _f("arialbd.ttf", 40), wc)
        hy = 356
    else:
        hy = 300
    # багийн толгой — 2 тал
    fh = _f("arialbd.ttf", 40)
    d.text((110, hy), "TEAM A", font=fh, fill=ORANGE)
    d.text((W - 110 - _tw(d, "TEAM B", fh), hy), "TEAM B", font=fh,
           fill=BLUE)
    d.line([112, hy + 54, 440, hy + 54], fill=ORANGE, width=3)
    d.line([W - 440, hy + 54, W - 112, hy + 54], fill=BLUE, width=3)
    fn = _f("arialbd.ttf", 31)
    fa = _f("arialbd.ttf", 33)
    fs = _f("arialbd.ttf", 21)
    y = top
    for bet in rnd.bets:
        a, b = bet.player_a, bet.player_b
        a_win = rnd.winner_team == 1
        b_win = rnd.winner_team == 2
        a_col = SUB if (has_win and not a_win) else WHITE
        b_col = SUB if (has_win and not b_win) else WHITE
        d.rounded_rectangle([80, y, W - 80, y + 104], radius=13, fill=ROW,
                            outline=LINE, width=1)
        _person(d, 128, y + 52, 32, ORANGE if a_col == WHITE else SUB)
        if a_win:
            _star(d, 170, y + 42, 12, GOLD)
        d.text((194, y + 34), _name(a, 14), font=fn, fill=a_col)
        _person(d, W - 128, y + 52, 32, BLUE if b_col == WHITE else SUB)
        if b_win:
            _star(d, W - 170, y + 42, 12, GOLD)
        bn = _name(b, 14)
        d.text((W - 194 - _tw(d, bn, fn), y + 34), bn, font=fn, fill=b_col)
        amt = f"{bet.amount:,}₮"
        _ct(d, W / 2, y + 22, amt, fa, GOLD)
        if has_win:
            acc = {UNPAID: GOLD, SETTLED: GREEN,
                   DEBT: RED}.get(bet.status, SUB)
            word = {UNPAID: "тооцоо хүлээж буй", SETTLED: "төлөгдсөн",
                    DEBT: "өр болсон"}.get(bet.status, "")
            _ct(d, W / 2, y + 62, word, fs, acc)
        else:
            _ct(d, W / 2, y + 62, "vs", fs, SUB)
        y += 116
    y += 8
    if not has_win:
        msg, col = "admin / ахлагч хожсон багийг тэмдэглэнэ", SUB
    elif not rnd.is_complete:
        msg, col = ("хожсон тоглогч бооцоогоо хаана: авсан / өр болгох",
                    DIM)
    else:
        nd = len(rnd.debts_to_register())
        msg, col = f"БҮХ БООЦОО ХААГДЛАА  —  өр болсон: {nd}", GREEN
    _ct(d, W / 2, y, msg, _f("arialbd.ttf", 25), col)
    return _png(img)


def render_debts(ledger, banks):
    opens = ledger.open_debts
    shown = opens[:12]
    extra = len(opens) - len(shown)
    h = 300 + max(len(shown), 1) * 106 + (46 if extra else 0) + 174
    img, d = _base(h, "ӨРИЙН", "ДЭВТЭР")
    if not opens:
        _ct(d, W / 2, 360, "Нээлттэй өр алга — бүгд цэвэрхэн!",
            _f("arialbd.ttf", 30), GREEN)
        return _png(img)
    _section(d, W / 2, 290, f"НЭЭЛТТЭЙ ӨР   —   {len(opens)}")
    _ct(d, W / 2, 348,
        "улаан = өр төлөгч        ногоон = төлбөр хүлээн авагч",
        _f("arialbd.ttf", 20), SUB)
    y = 392
    fn = _f("arialbd.ttf", 27)
    far = _f("arialbd.ttf", 26)
    for i, dt in enumerate(shown, 1):
        d.rounded_rectangle([140, y, W - 140, y + 92], radius=12,
                            fill=ROW, outline=LINE, width=1)
        d.rounded_rectangle([140, y, 148, y + 92], radius=2, fill=RED)
        d.text((176, y + 32), f"{i}", font=_f("arialbd.ttf", 26), fill=SUB)
        dn = _name(dt.debtor, 10)
        cn = _name(dt.creditor, 10)
        d.text((216, y + 14), dn, font=fn, fill=RED)
        d.text((408, y + 16), "→", font=far, fill=SUB)
        d.text((448, y + 14), cn, font=fn, fill=GREEN)
        amt = f"{dt.amount:,}₮"
        d.text((W - 160 - _tw(d, amt, fn), y + 14), amt, font=fn, fill=GOLD)
        date = dt.created or "огноо тэмдэглээгүй"
        d.text((228, y + 56),
               f"{dt.debtor.name} нь {dt.creditor.name}-д өртэй   ·   "
               f"үүссэн: {date}", font=_f("arialbd.ttf", 19), fill=SUB)
        y += 106
    if extra:
        _ct(d, W / 2, y + 6, f"…болон бусад {extra} нээлттэй өр",
            _f("arialbd.ttf", 21), SUB)
    return _png(img)
