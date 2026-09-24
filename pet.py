"""claude-cutie — Clawd, a tiny pixel-art desktop pet that mirrors what Claude Code is doing.

Drag with left mouse, click to make it jump, right click for menu.
State comes from ~/.claude/pet_state.json (written by hook.py).

Scenes are true pixel art: every frame is composed on a small LW x LH grid
from hand-drawn sprites and scaled up with nearest-neighbour.
"""
import json, math, random, socket, sys, time, tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from PIL import Image, ImageTk

STATE_FILE = Path.home() / ".claude" / "pet_state.json"
POS_FILE = Path.home() / ".claude" / "pet_pos.json"
LOCK_PORT = 47321              # single-instance lock (hook.py checks it too)
LW, LH, S = 56, 26, 5          # logical canvas and pixel scale
GROUND = LH - 1
CARD_H = 62
W, H = 300, CARD_H + LH * S + 6
KEY = "#010203"                # transparent color key

CARD, CARD_EDGE, TEXT, MUTED = "#262624", "#3A3935", "#F5F4ED", "#A6A39A"
BODY_HEX, GOLD_HEX = "#D97757", "#F0C66E"


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


PAL = {k: rgb(v) for k, v in {
    "O": BODY_HEX, "o": "#B5603F", "h": "#E8906F", "K": "#1F1E1C",
    "L": "#4A4944", "S": "#1B1A18", "B": "#5E7A96", "D": "#8A8780", "d": "#5A5852", "k": "#3A3935",
    "P": "#F2EEE3", "p": "#D9D3C4", "i": "#9C9585", "b": "#B8B1A0", "W": "#FFFFFF",
    "C": "#4E6A8E", "c": "#3B5270",
    "Y": GOLD_HEX, "y": "#C99A3E", "e": "#E58FA0", "w": "#E8C9A0",
    "G": GOLD_HEX, "g": "#8A877F", "T": TEXT, "M": MUTED,
}.items()}
CODE = [rgb(c) for c in ("#F0C66E", "#8FB8DE", "#B5CEA8", "#D97757", "#C9A0DC")]

# ---- sprites ('.' = transparent) --------------------------------------
CORE = [                       # crab body without arms/legs/eyes, 14 wide
    "..hhhhhhhhhh..",
    "..OOOOOOOOOO..",
    "..OOOOOOOOOO..",
    "..OOOOOOOOOO..",
    "..OOOOOOOOOO..",
    "..OOOOOOOOOO..",
    "..oooooooooo..",
]
LEGS = (3, 5, 8, 10)
FRONT_EYES, SIDE_EYES = (4, 9), (9, 11)

BOOK = [                       # small open book, 3/4 view
    "PPPPbpppp",
    "PiiPbpiip",
    "PPPPbpppp",
    "PiiPbpipp",
    "PPPPbpppp",
    "CCCCcCCCC",
]
PENCIL = [                     # tip at bottom-right (5, 5), eraser top-left
    "ee....",
    "eYY...",
    ".yYY..",
    "..yYY.",
    "...yYw",
    ".....k",
]
CLOUD = [
    "..TTT.TTT..",
    ".TTTTTTTTT.",
    "TTTTTTTTTTT",
    "TTTTTTTTTTT",
    ".TTTTTTTTT.",
    "..TTTTTTT..",
]
ZZ = [["TTTTT", "...T.", "..T..", ".T...", "TTTTT"], ["TTTT", "..T.", ".T..", "TTTT"]]

STATUS = {
    "thinking": "Thinking", "coding": "Coding", "writing": "Writing",
    "reading": "Reading", "running": "Running", "waiting": "Waiting for you", "done": "Done",
}
ACTIVE = {"thinking", "coding", "writing", "reading", "running"}
SLEEP_AFTER, DONE_FOR = 180, 5


class Pet:
    def __init__(self):
        r = self.root = tk.Tk()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=KEY)
        try:
            r.wm_attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        x, y = sw - W - 30, sh - H - 60
        try:
            pos = json.loads(POS_FILE.read_text())
            x = min(max(0, int(pos["x"])), sw - W)
            y = min(max(0, int(pos["y"])), sh - H)
        except (OSError, ValueError, KeyError):
            pass
        r.geometry(f"{W}x{H}+{x}+{y}")
        self.c = tk.Canvas(r, width=W, height=H, bg=KEY, highlightthickness=0)
        self.c.pack()
        self.title_font = tkfont.Font(family="Georgia", size=11)
        self.sub_font = tkfont.Font(family="Segoe UI", size=9)

        self.state, self.state_t, self.mtime = "idle", time.time(), 0
        self.prompt = self.detail = ""
        self.frame, self.walk_x, self.dir = 0, (LW - 14) // 2, 1
        self.jump_until, self.blink_at = 0, time.time() + 3
        self.look, self.look_until = 0, 0
        self.dust = []
        self.drag = (0, 0, False)
        self.photo = None

        self.c.bind("<ButtonPress-1>", self.on_press)
        self.c.bind("<B1-Motion>", self.on_drag)
        self.c.bind("<ButtonRelease-1>", self.on_release)
        menu = tk.Menu(r, tearoff=0)
        for s in ["idle", *STATUS, "sleeping"]:
            menu.add_command(label=f"Show: {s}", command=lambda s=s: self.set_state(s))
        menu.add_separator()
        menu.add_command(label="Quit", command=r.destroy)
        self.c.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        self.tick()

    # ---- input / state -------------------------------------------------
    def on_press(self, e):
        self.drag = (e.x, e.y, False)

    def on_drag(self, e):
        dx, dy, _ = self.drag
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")
        self.drag = (dx, dy, True)

    def on_release(self, e):
        if self.drag[2]:
            try:
                POS_FILE.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}))
            except OSError:
                pass
        else:
            self.jump_until = time.time() + 0.5

    def set_state(self, s):
        self.state, self.state_t = s, time.time()

    def poll(self):
        try:
            m = STATE_FILE.stat().st_mtime
            if m != self.mtime:
                self.mtime = m
                d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.prompt, self.detail = d.get("prompt", ""), d.get("detail", "")
                self.set_state(d.get("state", "idle"))
        except (OSError, ValueError):
            pass
        age = time.time() - self.state_t
        if self.state == "done" and age > DONE_FOR:
            self.set_state("idle")
        elif self.state == "idle" and age > SLEEP_AFTER:
            self.set_state("sleeping")

    # ---- pixel primitives ---------------------------------------------
    def px(self, x, y, col):
        x, y = int(x), int(y)
        if 0 <= x < LW and 0 <= y < LH:
            self.img.putpixel((x, y), PAL[col] if isinstance(col, str) else col)

    def rect(self, x, y, w, h, col):
        for yy in range(int(y), int(y) + h):
            for xx in range(int(x), int(x) + w):
                self.px(xx, yy, col)

    def blit(self, sprite, x, y, flip=False):
        for r, row in enumerate(sprite):
            for c, ch in enumerate(row[::-1] if flip else row):
                if ch != ".":
                    self.px(x + c, y + r, ch)

    def line(self, x0, y0, x1, y1, col, thick=1):
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            x = round(x0 + (x1 - x0) * i / n)
            y = round(y0 + (y1 - y0) * i / n)
            self.rect(x, y, thick, thick, col)

    def plus(self, x, y, size, col):
        self.px(x, y, col)
        for d in range(1, size + 1):
            for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d)):
                self.px(x + dx, y + dy, col)

    # ---- the crab -----------------------------------------------------
    def blinking(self, now):
        return self.blink_at < now < self.blink_at + 0.15

    def crab(self, x, top, *, side=False, flip=False, eyes="open", look=0,
             ground=GROUND, lifts=(0, 0, 0, 0), arms=(True, True), sit=False):
        """Draw Clawd. `top` is the first body row; legs reach down to `ground`."""
        now = time.time()
        mx = (lambda c: 13 - c) if flip else (lambda c: c)
        leg_end = top + 8 if ground is None else ground
        if sit:
            leg_end = top + 7
        for lx, lift in zip(LEGS, lifts):
            for y in range(top + 7, leg_end + 1 - lift):
                self.px(x + mx(lx), y, "o")
        self.blit(CORE, x, top, flip)
        back_arm, front_arm = arms
        if side:  # back arm darker, front arm forward
            if back_arm:
                self.rect(x + mx(1), top + 3, 1, 2, "o")
            if front_arm:
                self.rect(x + min(mx(12), mx(13)), top + 3, 2, 2, "O")
        else:
            if back_arm:
                self.rect(x, top + 3, 2, 2, "O")
            if front_arm:
                self.rect(x + 12, top + 3, 2, 2, "O")

        if eyes in ("open", "up", "down") and self.blinking(now):
            eyes = "closed"
        for ex in (SIDE_EYES if side else FRONT_EYES):
            ex = x + mx(ex) + (-look if flip else look)
            if eyes == "open":
                self.rect(ex, top + 2, 1, 2, "K")
            elif eyes == "up":
                self.rect(ex, top + 1, 1, 2, "K")
            elif eyes == "down":
                self.rect(ex, top + 3, 1, 2, "K")
            elif eyes == "closed":
                self.rect(ex - 1, top + 3, 2, 1, "K")
            elif eyes == "happy":
                self.px(ex - 1, top + 3, "K"); self.px(ex, top + 2, "K"); self.px(ex + 1, top + 3, "K")

    # ---- scenes -------------------------------------------------------
    def scene(self, now):
        s, f = self.state, self.frame
        fx = (LW - 14) // 2              # front crab x
        ftop = GROUND - 8                # front crab top (standing)

        if now < self.jump_until:
            k = (self.jump_until - now) / 0.5
            self.crab(fx, ftop - round(math.sin(k * math.pi) * 4), eyes="happy", ground=None)
            return

        if s == "idle":
            if now > self.look_until:
                self.look = random.choice((0, 0, 0, -1, 1))
                self.look_until = now + random.uniform(1.5, 3.5)
            breath = 1 if (now % 2.4) > 1.2 else 0
            self.crab(fx, ftop + breath, look=self.look)

        elif s == "thinking":
            look = 1 if (now % 4) < 2 else -1
            self.crab(fx, ftop, eyes="up", look=look)
            self.px(fx + 14, ftop - 1, "T")
            self.rect(fx + 15, ftop - 4, 2, 2, "T")
            cx, cy = fx + 16, ftop - 11
            self.blit(CLOUD, cx, cy)
            for i in range(int(now * 2.5) % 4):
                self.px(cx + 3 + i * 2, cy + 3, "K")

        elif s == "coding":
            # profile: cozy coder — compact laptop with a straight lid, mug of coffee
            cx, top = 9, GROUND - 7
            bob = 1 if f % 8 < 4 else 0
            self.crab(cx, top + bob, side=True, sit=True, arms=(True, False))
            x0 = cx + 14
            hx = x0 + 9                                   # hinge
            self.rect(x0, GROUND - 1, 10, 1, "D")
            self.rect(x0, GROUND, 11, 1, "d")
            self.rect(hx, GROUND - 10, 1, 9, "B")         # screen face
            self.rect(hx + 1, GROUND - 10, 1, 10, "L")    # lid back
            self.px(hx, GROUND - 10, "L"); self.px(hx, GROUND - 2, "L")
            mx = hx + 5                                   # mug + steam
            self.rect(mx, GROUND - 3, 3, 4, "P"); self.rect(mx, GROUND - 3, 3, 1, "p")
            self.px(mx + 3, GROUND - 2, "P"); self.px(mx + 3, GROUND - 1, "P")
            st = int(now * 3) % 2
            self.px(mx + 1 + st, GROUND - 5, "M"); self.px(mx + 2 - st, GROUND - 6, "M")
            tap = (f // 2) % 2                            # typing claw
            key = x0 + 1 + ((f // 2) * 5 % 3) * 2
            self.rect(cx + 12, top + bob + 3, 2, 2, "O")
            self.rect(cx + 14, GROUND - 3, key - cx - 14, 1, "O")
            self.rect(key, GROUND - 3 + tap, 2, 1, "O")

        elif s == "writing":
            # profile: sheet lying in front, pencil scribbling along it
            cx, top = 10, GROUND - 7
            self.crab(cx, top, side=True, sit=True, eyes="down", arms=(True, False))
            x0, x1 = cx + 15, cx + 27
            self.rect(x0 + 1, GROUND - 2, x1 - x0, 1, "P")
            self.rect(x0, GROUND - 1, x1 - x0, 1, "P")
            self.rect(x0, GROUND, x1 - x0, 1, "p")
            span = x1 - x0 - 2
            prog = int(now * 8) % (span * 2 + 8)
            tip = (x0 + 1, GROUND - 2)
            for row in range(2):
                n = max(0, min(span, prog - row * span))
                y = GROUND - 2 + row
                for k in range(n):
                    if (k * 7 + row * 3) % 5:
                        self.px(x0 + 1 + row + k, y, "k")
                if 0 < n < span:
                    tip = (x0 + 1 + row + n, y)
            lift = 1 if (prog % 5) == 0 else 0
            ox, oy = tip[0] - 5, tip[1] - 6 - lift
            self.blit(PENCIL, ox, oy)
            self.line(cx + 12, top + 3, ox, oy + 1, "O", thick=2)
            self.rect(ox, oy + 1, 2, 2, "O")                 # claw gripping the pencil

        elif s == "reading":
            # small open book held up at face level, pages turning
            cx, top = 11, GROUND - 7
            scan = 1 if (now % 1.6) > 0.8 else 0
            self.crab(cx, top, side=True, sit=True, eyes="down", look=scan - 1, arms=(True, False))
            bx, by = cx + 14, top + 1
            self.blit(BOOK, bx, by)
            t = now % 3.0
            if t < 0.6:                              # page lifts from the right, lands on the left
                x0, w, lift = [(5, 4, 1), (5, 2, 2), (4, 1, 3), (2, 2, 2), (0, 4, 1)][int(t / 0.12)]
                self.rect(bx + x0, by - lift, w, 5, "W")
            self.rect(cx + 12, top + 3, 2, 2, "O")
            self.rect(bx - 1, by + 3, 2, 2, "O")

        elif s == "running":
            if f % 2 == 0:
                self.walk_x += self.dir
                if self.walk_x < 2 or self.walk_x > LW - 16:
                    self.dir *= -1
            step = (f // 3) % 2
            lifts = (1, 0, 1, 0) if step else (0, 1, 0, 1)
            x = int(self.walk_x)
            self.crab(x, ftop - step, side=True, flip=self.dir < 0, lifts=lifts)
            if f % 4 == 0:
                self.dust.append([x + (1 if self.dir > 0 else 12), GROUND, 6])
            for d in self.dust:
                self.px(d[0], d[1] - (6 - d[2]) // 3, "g" if d[2] > 2 else "k")
                d[2] -= 1
            self.dust = [d for d in self.dust if d[2] > 0]

        elif s == "waiting":
            t = now % 1.6
            hop = [0, 1, 2, 2, 1, 0][int(t / 0.08)] if t < 0.48 else 0
            top = ftop - hop
            self.crab(fx, top, arms=(True, False), ground=None if hop else GROUND)
            if (f // 4) % 2:
                self.rect(fx + 12, top + 1, 2, 2, "O"); self.rect(fx + 13, top - 1, 2, 2, "O")
            else:
                self.rect(fx + 12, top + 2, 2, 2, "O"); self.rect(fx + 14, top + 1, 2, 2, "O")
            self.rect(fx + 6, top - 7, 2, 3, "G")
            self.rect(fx + 6, top - 3, 2, 1, "G")

        elif s == "done":
            hop = round(abs(math.sin(now * 4)) * 3)
            self.crab(fx, ftop - hop, eyes="happy", ground=None if hop else GROUND)
            for i, (sx, sy) in enumerate(((-5, 2), (18, 1), (-2, -5), (16, -6), (7, -9))):
                ph = (now * 1.5 + i * 0.37) % 1
                size = [0, 1, 2, 1, 0, -1][int(ph * 6)]
                if size >= 0:
                    self.plus(fx + sx, ftop + sy, size, "G")

        elif s == "sleeping":
            breath = 1 if (now % 3) > 1.5 else 0
            self.crab(fx, GROUND - 6 + breath, eyes="closed", sit=True, lifts=(9, 9, 9, 9))
            for i in range(3):
                t = (now * 0.35 + i / 3) % 1
                z = ZZ[0] if t > 0.4 else ZZ[1]
                zx, zy = fx + 13 + int(t * 14), GROUND - 7 - int(t * 18)
                for ry, row in enumerate(z):
                    for rx, ch in enumerate(row):
                        if ch != ".":
                            self.px(zx + rx, zy + ry, "T" if t < 0.75 else "M")

    # ---- status card (Codex-style) -----------------------------------------
    def round_rect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        self.c.create_polygon(pts, smooth=True, **kw)

    def spark(self, cx, cy, r, color, rot=0.0, width=2):
        for i in range(6):
            a = rot + i * math.pi / 3
            self.c.create_line(cx, cy, cx + r * math.cos(a), cy + r * math.sin(a),
                               fill=color, width=width, capstyle="round")

    def fit(self, text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    def draw_card(self, now):
        s = self.state
        if s not in STATUS:
            return
        k = min(1.0, (now - self.state_t) / 0.2)
        dy = int((1 - k) * 6)
        x0, y0, x1, y1 = 4, 4 + dy, W - 4, 58 + dy
        self.round_rect(x0, y0, x1, y1, 16, fill=CARD, outline=CARD_EDGE)
        color = GOLD_HEX if s == "waiting" else BODY_HEX
        self.spark(x0 + 22, (y0 + y1) // 2, 8, color, rot=now * 1.5 if s in ACTIVE else 0, width=3)
        tx = x0 + 42
        title = self.fit(self.prompt or "Claude Code", self.title_font, x1 - tx - 14)
        self.c.create_text(tx, y0 + 19, text=title, anchor="w", font=self.title_font, fill=TEXT)
        sub = STATUS[s]
        if self.detail and s in ACTIVE and s != "thinking":
            sub += f" · {self.detail}"
        if s in ACTIVE:
            sub += "." * (1 + int(now * 3) % 3)
        self.c.create_text(tx, y0 + 38, text=self.fit(sub, self.sub_font, x1 - tx - 14),
                           anchor="w", font=self.sub_font, fill=GOLD_HEX if s == "waiting" else MUTED)

    # ---- loop --------------------------------------------------------------
    def tick(self):
        now = time.time()
        if now > self.blink_at + 0.15:
            self.blink_at = now + random.uniform(2.5, 5.5)
        self.poll()
        self.frame += 1
        self.img = Image.new("RGB", (LW, LH), rgb(KEY))
        self.scene(now)
        self.photo = ImageTk.PhotoImage(self.img.resize((LW * S, LH * S), Image.NEAREST))
        self.c.delete("all")
        self.c.create_image((W - LW * S) // 2, CARD_H + 2, image=self.photo, anchor="nw")
        self.draw_card(now)
        self.root.after(70, self.tick)


if __name__ == "__main__":
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
    except OSError:
        sys.exit(0)                 # already running
    Pet().root.mainloop()
