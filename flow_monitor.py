import os
import sys
import time
import math
from collections import deque

# ── Display driver setup (must happen before importing pygame) ────────────────
IS_PI = os.path.exists('/proc/device-tree/model')

if IS_PI and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    uid = os.getuid()
    os.environ.setdefault('XDG_RUNTIME_DIR', f'/run/user/{uid}')
    os.environ.setdefault('WAYLAND_DISPLAY', 'wayland-0')
    os.environ['SDL_VIDEODRIVER'] = 'wayland'

import pygame

# ── Config ────────────────────────────────────────────────────────────────────
CALIBRATION_FACTOR = 330   # pulses per liter — Digiten FL-608
GPIO_PIN           = 17
HISTORY_LEN        = 120   # seconds of graph history

# ── GPIO setup ────────────────────────────────────────────────────────────────
pulse_count  = 0
total_liters = 0.0

try:
    from gpiozero import Button
    if not IS_PI:
        from gpiozero.pins.mock import MockFactory
        from gpiozero import Device
        Device.pin_factory = MockFactory()
        print('Running in mock mode (not a Pi)')

    sensor = Button(GPIO_PIN, pull_up=True)

    def _on_pulse():
        global pulse_count
        pulse_count += 1

    sensor.when_pressed = _on_pulse

except Exception as exc:
    print(f'GPIO not available: {exc}')

# ── Pygame init ───────────────────────────────────────────────────────────────
pygame.init()
screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
W, H   = screen.get_size()
pygame.mouse.set_visible(False)
pygame.display.set_caption('Flow Monitor')

# ── Colors ────────────────────────────────────────────────────────────────────
BG        = ( 10,  10,  10)
CYAN      = (  0, 229, 255)
CYAN_DIM  = (  0,  55,  65)
CYAN_GLOW = (  0, 180, 210)
MUTED     = ( 68,  68,  68)
UNIT_C    = (  0, 110, 125)
TOTAL_C   = ( 60,  60,  60)
LINE_C    = ( 30,  30,  30)
HINT_C    = ( 38,  38,  38)
GRID_C    = ( 20,  20,  20)
AXIS_C    = ( 55,  55,  55)
PANEL_C   = ( 16,  16,  16)

# ── Fonts ─────────────────────────────────────────────────────────────────────
F_BIG    = pygame.font.SysFont(None, max(80,  H // 5))
F_LABEL  = pygame.font.SysFont(None, max(18,  H // 32))
F_UNIT   = pygame.font.SysFont(None, max(26,  H // 18))
F_TOTAL  = pygame.font.SysFont(None, max(20,  H // 26))
F_HINT   = pygame.font.SysFont(None, max(15,  H // 50))
F_GTITLE = pygame.font.SysFont(None, max(20,  H // 30))
F_GAXIS  = pygame.font.SysFont(None, max(15,  H // 44))

# ── Layout ────────────────────────────────────────────────────────────────────
cx  = W // 2
cy  = H // 2
GAP = H // 16

# ── State ─────────────────────────────────────────────────────────────────────
flow_history = deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN)
show_graph   = False
flow_lpm     = 0.0
has_flow     = False

# ── Helpers ───────────────────────────────────────────────────────────────────
def nice_max(val):
    """Round up to a clean axis maximum."""
    if val <= 0:
        return 1.0
    mag  = 10 ** math.floor(math.log10(val))
    return math.ceil(val / mag) * mag


def draw_glow_text(surface, font, text, color, pos, layers=3, spread=2):
    """Render text with a subtle glow behind it."""
    glow_col = tuple(min(255, int(c * 0.35)) for c in color)
    cx_t, cy_t = pos
    for r in range(layers, 0, -1):
        s = font.render(text, True, glow_col)
        for dx, dy in [(-r*spread, 0), (r*spread, 0), (0, -r*spread), (0, r*spread)]:
            surface.blit(s, (cx_t + dx - s.get_width()//2,
                             cy_t + dy - s.get_height()//2))
    s = font.render(text, True, color)
    surface.blit(s, (cx_t - s.get_width()//2, cy_t - s.get_height()//2))


def draw_main_screen():
    screen.fill(BG)

    # Subtle top accent line
    pygame.draw.line(screen, (0, 60, 70), (0, 0), (W, 0), 2)

    # Label
    lbl = F_LABEL.render('F L O W   R A T E', True, MUTED)
    screen.blit(lbl, (cx - lbl.get_width()//2, cy - GAP * 3 - 10))

    # Main number with glow
    color = CYAN if has_flow else CYAN_DIM
    draw_glow_text(screen, F_BIG, f'{flow_lpm:.2f}', color, (cx, cy - GAP))

    # Unit
    unit = F_UNIT.render('L / min', True, UNIT_C)
    screen.blit(unit, (cx - unit.get_width()//2, cy + GAP + 8))

    # Divider
    dw = 80
    pygame.draw.line(screen, LINE_C, (cx - dw, cy + GAP * 2 + 4),
                     (cx + dw, cy + GAP * 2 + 4), 1)

    # Total
    total = F_TOTAL.render(f'total   {total_liters:.3f} L', True, TOTAL_C)
    screen.blit(total, (cx - total.get_width()//2, cy + GAP * 2 + 14))

    # "tap for graph" pill hint
    hint_txt = 'tap for graph'
    hint = F_HINT.render(hint_txt, True, HINT_C)
    hw, hh = hint.get_width() + 20, hint.get_height() + 10
    hx = W - hw - 16
    hy = H - hh - 14
    pygame.draw.rect(screen, (18, 18, 18), (hx, hy, hw, hh), border_radius=8)
    screen.blit(hint, (hx + 10, hy + 5))


def draw_graph_screen():
    screen.fill(BG)

    # Subtle top accent
    pygame.draw.line(screen, (0, 60, 70), (0, 0), (W, 0), 2)

    # ── Graph area ───────────────────────────────────────────────────────────
    PAD_L, PAD_R = 68, 28
    PAD_T, PAD_B = 52, 54
    gx = PAD_L
    gy = PAD_T
    gw = W - PAD_L - PAD_R
    gh = H - PAD_T - PAD_B

    history = list(flow_history)
    max_val = nice_max(max(history) if history else 0)

    # Background panel
    pygame.draw.rect(screen, PANEL_C, (gx, gy, gw, gh))

    # ── Grid ─────────────────────────────────────────────────────────────────
    NUM_H = 4   # horizontal grid lines
    NUM_V = 6   # vertical grid lines

    for i in range(NUM_H + 1):
        y   = gy + gh - int(i / NUM_H * gh)
        val = i / NUM_H * max_val
        pygame.draw.line(screen, GRID_C, (gx, y), (gx + gw, y), 1)
        lbl = F_GAXIS.render(f'{val:.1f}', True, AXIS_C)
        screen.blit(lbl, (gx - lbl.get_width() - 6, y - lbl.get_height()//2))

    for i in range(NUM_V + 1):
        x       = gx + int(i / NUM_V * gw)
        secs    = int((1 - i / NUM_V) * HISTORY_LEN)
        label   = 'now' if secs == 0 else f'-{secs}s'
        pygame.draw.line(screen, GRID_C, (x, gy), (x, gy + gh), 1)
        lbl = F_GAXIS.render(label, True, AXIS_C)
        screen.blit(lbl, (x - lbl.get_width()//2, gy + gh + 7))

    # ── Axes ─────────────────────────────────────────────────────────────────
    pygame.draw.line(screen, AXIS_C, (gx, gy),      (gx, gy + gh), 1)
    pygame.draw.line(screen, AXIS_C, (gx, gy + gh), (gx + gw, gy + gh), 1)

    # Y axis unit
    unit_lbl = F_GAXIS.render('L/min', True, AXIS_C)
    screen.blit(unit_lbl, (4, gy - 2))

    # ── Plot ─────────────────────────────────────────────────────────────────
    if len(history) >= 2:
        def to_pt(i, val):
            px = gx + int(i / (HISTORY_LEN - 1) * gw)
            py = gy + gh - int((val / max_val) * gh)
            return (px, max(gy, min(gy + gh, py)))

        pts = [to_pt(i, v) for i, v in enumerate(history)]

        # Filled area with alpha — layered for a glow-fade effect
        fill_poly = [(gx, gy + gh)] + pts + [(gx + gw, gy + gh)]
        for alpha, shrink in [(12, 0), (8, 4), (5, 8)]:
            surf = pygame.Surface((W, H), pygame.SRCALPHA)
            squeezed = [(px, min(gy + gh, py + shrink)) for px, py in fill_poly]
            pygame.draw.polygon(surf, (0, 229, 255, alpha), squeezed)
            screen.blit(surf, (0, 0))

        # Line
        pygame.draw.lines(screen, CYAN_GLOW, False, pts, 1)
        pygame.draw.lines(screen, CYAN,      False, pts, 2)

        # Dot at latest point
        tip = pts[-1]
        pygame.draw.circle(screen, CYAN, tip, 6)
        pygame.draw.circle(screen, BG,   tip, 3)

    # ── Title + live value ────────────────────────────────────────────────────
    title = F_GTITLE.render('F L O W   H I S T O R Y', True, MUTED)
    screen.blit(title, (gx, 14))

    color = CYAN if has_flow else CYAN_DIM
    cur   = F_UNIT.render(f'{flow_lpm:.2f} L/min', True, color)
    screen.blit(cur, (W - cur.get_width() - PAD_R, 12))

    # ── Total ─────────────────────────────────────────────────────────────────
    total = F_TOTAL.render(f'total   {total_liters:.3f} L', True, TOTAL_C)
    screen.blit(total, (gx, H - PAD_B + 28))

    # ── Back hint ─────────────────────────────────────────────────────────────
    hint_txt = 'tap to go back'
    hint  = F_HINT.render(hint_txt, True, HINT_C)
    hw, hh = hint.get_width() + 20, hint.get_height() + 10
    hx = W - hw - 16
    hy = H - hh - 14
    pygame.draw.rect(screen, (18, 18, 18), (hx, hy, hw, hh), border_radius=8)
    screen.blit(hint, (hx + 10, hy + 5))


# ── Main loop ─────────────────────────────────────────────────────────────────
clock     = pygame.time.Clock()
last_tick = time.time()
running   = True

while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False
            else:
                show_graph = not show_graph
        elif event.type == pygame.MOUSEBUTTONDOWN:
            show_graph = not show_graph

    now = time.time()
    if now - last_tick >= 1.0:
        count         = pulse_count
        pulse_count   = 0
        flow_lps      = count / CALIBRATION_FACTOR
        flow_lpm      = flow_lps * 60
        total_liters += flow_lps
        has_flow      = count > 0
        flow_history.append(flow_lpm)
        last_tick     = now

    if show_graph:
        draw_graph_screen()
    else:
        draw_main_screen()

    pygame.display.flip()
    clock.tick(30)

pygame.quit()
sys.exit()