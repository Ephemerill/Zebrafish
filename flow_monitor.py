import os
import sys
import time

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
BG       = (10,  10,  10)
CYAN     = (0,  229, 255)
CYAN_DIM = (0,   55,  65)
MUTED    = (68,  68,  68)
UNIT_C   = (0,  110, 125)
TOTAL_C  = (55,  55,  55)
LINE_C   = (28,  28,  28)
HINT_C   = (28,  28,  28)

# ── Fonts (scale to display size) ─────────────────────────────────────────────
F_BIG   = pygame.font.SysFont(None, max(80, H // 6))
F_LABEL = pygame.font.SysFont(None, max(20, H // 30))
F_UNIT  = pygame.font.SysFont(None, max(28, H // 18))
F_TOTAL = pygame.font.SysFont(None, max(24, H // 22))
F_HINT  = pygame.font.SysFont(None, max(16, H // 48))

# ── Layout anchors ────────────────────────────────────────────────────────────
cx  = W // 2
cy  = H // 2
GAP = H // 16

# ── Main loop ─────────────────────────────────────────────────────────────────
clock       = pygame.time.Clock()
last_tick   = time.time()
flow_lpm    = 0.0
has_flow    = False
running     = True

while running:
    # ── Events ────────────────────────────────────────────────────────────────
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            # Touch tap anywhere exits — remove if you don't want this
            running = False

    # ── Update flow every second ──────────────────────────────────────────────
    now = time.time()
    if now - last_tick >= 1.0:
        count        = pulse_count
        pulse_count  = 0
        flow_lps     = count / CALIBRATION_FACTOR
        flow_lpm     = flow_lps * 60
        total_liters += flow_lps
        has_flow     = count > 0
        last_tick    = now

    # ── Draw ──────────────────────────────────────────────────────────────────
    screen.fill(BG)

    # "FLOW RATE" label
    lbl = F_LABEL.render('FLOW RATE', True, MUTED)
    screen.blit(lbl, (cx - lbl.get_width() // 2, cy - GAP * 3))

    # Big number
    num = F_BIG.render(f'{flow_lpm:.2f}', True, CYAN if has_flow else CYAN_DIM)
    screen.blit(num, (cx - num.get_width() // 2, cy - GAP * 2))

    # Unit
    unit = F_UNIT.render('L / min', True, UNIT_C)
    screen.blit(unit, (cx - unit.get_width() // 2, cy + GAP))

    # Divider line
    pygame.draw.line(screen, LINE_C,
                     (cx - 60, cy + GAP * 2),
                     (cx + 60, cy + GAP * 2), 1)

    # Total
    total = F_TOTAL.render(f'total:  {total_liters:.3f} L', True, TOTAL_C)
    screen.blit(total, (cx - total.get_width() // 2, cy + GAP * 2 + 10))

    # Hint
    hint = F_HINT.render('tap to exit', True, HINT_C)
    screen.blit(hint, (W - hint.get_width() - 20, H - hint.get_height() - 16))

    pygame.display.flip()
    clock.tick(30)

pygame.quit()
sys.exit()