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
CALIBRATION_FACTOR = 330     # pulses per liter — Digiten FL-608
FLOW_GPIO_PIN      = 17      # flow sensor pulse input
HISTORY_LEN        = 120     # seconds of graph history

# ── Pump / PWM control config ──────────────────────────────────────────────────
MAX_FLOW    = 37.0    # L/min — slider maximum (pump's rated max)
DUTY_MAX    = 95.0    # hard cap on duty cycle (%) — never exceeded
PWM_HZ      = 1000    # PWM frequency (Hz) — change if the pump prefers another
PWM_CHANNEL = 0       # hardware PWM channel 0 -> GPIO18 (pwm-2chan overlay, Pi 4)
PWM_CHIP    = 0       # 0 on Pi 4 and earlier, 2 on Pi 5

# Feedforward: the duty (%) that produces roughly MAX_FLOW. Take this from your
# duty sweep. If 37 L/min actually needs ~80% duty, set this to 80 for a tighter,
# faster loop. It only needs to be in the ballpark — the PI term cleans up the rest.
FF_DUTY_AT_MAX = 95.0

# PI gains — sensible starting point, then tune (see notes at the bottom).
# KP is in units of duty-% per (L/min) of error.
KP = 1.2
KI = 0.4

# ── Flow sensor GPIO setup ─────────────────────────────────────────────────────
pulse_count  = 0
total_liters = 0.0

try:
    from gpiozero import Button
    if not IS_PI:
        from gpiozero.pins.mock import MockFactory
        from gpiozero import Device
        Device.pin_factory = MockFactory()
        print('Running in mock mode (not a Pi)')

    sensor = Button(FLOW_GPIO_PIN, pull_up=True)

    def _on_pulse():
        global pulse_count
        pulse_count += 1

    sensor.when_pressed = _on_pulse

except Exception as exc:
    print(f'GPIO not available: {exc}')

# ── Pump PWM setup ──────────────────────────────────────────────────────────────
class _DummyPWM:
    """Stand-in so the UI still runs off-Pi or without the PWM library."""
    def start(self, duty): pass
    def stop(self): pass
    def change_duty_cycle(self, duty): pass
    def change_frequency(self, hz): pass


try:
    from rpi_hardware_pwm import HardwarePWM
    pwm = HardwarePWM(pwm_channel=PWM_CHANNEL, hz=PWM_HZ, chip=PWM_CHIP)
    pwm.start(0)
except Exception as exc:
    print(f'PWM not available: {exc}')
    pwm = _DummyPWM()


def set_duty(duty):
    """Clamp to [0, DUTY_MAX] and apply. Returns the value actually set."""
    duty = max(0.0, min(DUTY_MAX, duty))
    pwm.change_duty_cycle(duty)
    return duty


# ── Closed-loop flow controller (feedforward + PI, with anti-windup) ────────────
class FlowPID:
    def __init__(self, kp, ki, out_min, out_max):
        self.kp = kp
        self.ki = ki
        self.out_min = out_min
        self.out_max = out_max
        self.integral = 0.0

    def reset(self):
        self.integral = 0.0

    def update(self, setpoint, measured, dt, feedforward):
        error = setpoint - measured
        self.integral += error * dt
        raw = feedforward + self.kp * error + self.ki * self.integral
        out = max(self.out_min, min(self.out_max, raw))
        # Back-calculation anti-windup: undo integral that only caused saturation
        if self.ki != 0 and raw != out:
            self.integral += (out - raw) / self.ki
        return out


pid = FlowPID(KP, KI, 0.0, DUTY_MAX)

# ── Pygame init ───────────────────────────────────────────────────────────────
pygame.init()
screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
W, H   = screen.get_size()
pygame.mouse.set_visible(False)
pygame.display.set_caption('Flow Control')

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
TRACK_C   = ( 30,  30,  30)
ON_C      = (  0, 229, 255)
OFF_C     = ( 45,  16,  16)
OFF_TXT   = (210,  80,  80)
DUTY_C    = (  0, 140, 160)

# ── Fonts ─────────────────────────────────────────────────────────────────────
F_BIG    = pygame.font.SysFont(None, max(60,  H // 6))
F_LABEL  = pygame.font.SysFont(None, max(18,  H // 32))
F_UNIT   = pygame.font.SysFont(None, max(26,  H // 22))
F_TOTAL  = pygame.font.SysFont(None, max(20,  H // 30))
F_HINT   = pygame.font.SysFont(None, max(15,  H // 50))
F_GTITLE = pygame.font.SysFont(None, max(20,  H // 30))
F_GAXIS  = pygame.font.SysFont(None, max(15,  H // 44))
F_BTN    = pygame.font.SysFont(None, max(22,  H // 24))
F_SLIDER = pygame.font.SysFont(None, max(20,  H // 28))

# ── Layout ────────────────────────────────────────────────────────────────────
cx  = W // 2
cy  = H // 2

# Slider geometry
SLIDER_X0 = int(W * 0.15)
SLIDER_X1 = int(W * 0.85)
SLIDER_Y  = int(H * 0.66)

# Power button geometry
BTN_W = int(W * 0.34)
BTN_H = int(H * 0.11)
BTN_X = cx - BTN_W // 2
BTN_Y = int(H * 0.82)

# Hint pill rect — filled in at draw time, used for hit-testing
graph_hint_rect = pygame.Rect(0, 0, 0, 0)

# ── State ─────────────────────────────────────────────────────────────────────
flow_history    = deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN)
show_graph      = False
flow_lpm        = 0.0
has_flow        = False
pump_on         = False
target_flow     = 0.0     # L/min, set by the slider
current_duty    = 0.0     # %, what the controller last commanded
dragging_slider = False

# ── Control / UI helpers ────────────────────────────────────────────────────────
def target_to_handle_x(target):
    frac = target / MAX_FLOW if MAX_FLOW else 0.0
    return int(SLIDER_X0 + frac * (SLIDER_X1 - SLIDER_X0))


def x_to_target(px):
    frac = (px - SLIDER_X0) / (SLIDER_X1 - SLIDER_X0)
    frac = max(0.0, min(1.0, frac))
    return frac * MAX_FLOW


def slider_hit_rect():
    pad = int(H * 0.06)
    return pygame.Rect(SLIDER_X0 - 30, SLIDER_Y - pad,
                       (SLIDER_X1 - SLIDER_X0) + 60, pad * 2)


def power_btn_rect():
    return pygame.Rect(BTN_X, BTN_Y, BTN_W, BTN_H)


def set_pump(on):
    """Turn the pump on/off. Off forces duty straight to 0 and clears the loop."""
    global pump_on, current_duty
    pump_on = on
    if not on:
        pid.reset()
        current_duty = set_duty(0)


# ── Drawing helpers ─────────────────────────────────────────────────────────────
def nice_max(val):
    """Round up to a clean axis maximum."""
    if val <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(val))
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


def draw_hint(text):
    """Bottom-right pill. Records its rect globally for hit-testing."""
    global graph_hint_rect
    hint = F_HINT.render(text, True, HINT_C)
    hw, hh = hint.get_width() + 20, hint.get_height() + 10
    hx = W - hw - 16
    hy = H - hh - 14
    graph_hint_rect = pygame.Rect(hx, hy, hw, hh)
    pygame.draw.rect(screen, (18, 18, 18), graph_hint_rect, border_radius=8)
    screen.blit(hint, (hx + 10, hy + 5))


def draw_slider():
    label_y = SLIDER_Y - int(H * 0.05)

    lbl = F_LABEL.render('T A R G E T   F L O W', True, MUTED)
    screen.blit(lbl, (SLIDER_X0, label_y - lbl.get_height()))

    val_col = CYAN if pump_on else CYAN_DIM
    val = F_SLIDER.render(f'{target_flow:.1f} L/min', True, val_col)
    screen.blit(val, (SLIDER_X1 - val.get_width(), label_y - val.get_height()))

    # Track
    pygame.draw.line(screen, TRACK_C, (SLIDER_X0, SLIDER_Y), (SLIDER_X1, SLIDER_Y), 6)
    # Filled portion + handle
    hx = target_to_handle_x(target_flow)
    fill_col = CYAN if pump_on else CYAN_DIM
    pygame.draw.line(screen, fill_col, (SLIDER_X0, SLIDER_Y), (hx, SLIDER_Y), 6)
    pygame.draw.circle(screen, fill_col, (hx, SLIDER_Y), int(H * 0.024))
    pygame.draw.circle(screen, BG,       (hx, SLIDER_Y), int(H * 0.012))


def draw_power_button():
    r = power_btn_rect()
    if pump_on:
        pygame.draw.rect(screen, ON_C, r, border_radius=12)
        txt = F_BTN.render('PUMP ON', True, BG)
    else:
        pygame.draw.rect(screen, OFF_C, r, border_radius=12)
        pygame.draw.rect(screen, OFF_TXT, r, width=2, border_radius=12)
        txt = F_BTN.render('PUMP OFF', True, OFF_TXT)
    screen.blit(txt, (r.centerx - txt.get_width()//2, r.centery - txt.get_height()//2))

    # Duty readout under the button
    duty_txt = F_SLIDER.render(f'duty  {current_duty:4.0f}%', True, DUTY_C)
    screen.blit(duty_txt, (cx - duty_txt.get_width()//2, r.bottom + int(H * 0.02)))


def draw_main_screen():
    screen.fill(BG)
    pygame.draw.line(screen, (0, 60, 70), (0, 0), (W, 0), 2)

    # ── Live reading (top) ──
    read_cy = int(H * 0.24)
    lbl = F_LABEL.render('F L O W   R A T E', True, MUTED)
    screen.blit(lbl, (cx - lbl.get_width()//2, int(H * 0.07)))

    color = CYAN if has_flow else CYAN_DIM
    draw_glow_text(screen, F_BIG, f'{flow_lpm:.2f}', color, (cx, read_cy))

    unit = F_UNIT.render('L / min', True, UNIT_C)
    screen.blit(unit, (cx - unit.get_width()//2, read_cy + int(H * 0.11)))

    total = F_TOTAL.render(f'total   {total_liters:.3f} L', True, TOTAL_C)
    screen.blit(total, (cx - total.get_width()//2, read_cy + int(H * 0.17)))

    # ── Controls (lower half) ──
    draw_slider()
    draw_power_button()

    draw_hint('tap for graph')


def draw_graph_screen():
    screen.fill(BG)
    pygame.draw.line(screen, (0, 60, 70), (0, 0), (W, 0), 2)

    PAD_L, PAD_R = 68, 28
    PAD_T, PAD_B = 52, 54
    gx, gy = PAD_L, PAD_T
    gw = W - PAD_L - PAD_R
    gh = H - PAD_T - PAD_B

    history = list(flow_history)
    max_val = nice_max(max(history) if history else 0)

    pygame.draw.rect(screen, PANEL_C, (gx, gy, gw, gh))

    NUM_H, NUM_V = 4, 6
    for i in range(NUM_H + 1):
        y   = gy + gh - int(i / NUM_H * gh)
        val = i / NUM_H * max_val
        pygame.draw.line(screen, GRID_C, (gx, y), (gx + gw, y), 1)
        lbl = F_GAXIS.render(f'{val:.1f}', True, AXIS_C)
        screen.blit(lbl, (gx - lbl.get_width() - 6, y - lbl.get_height()//2))

    for i in range(NUM_V + 1):
        x     = gx + int(i / NUM_V * gw)
        secs  = int((1 - i / NUM_V) * HISTORY_LEN)
        label = 'now' if secs == 0 else f'-{secs}s'
        pygame.draw.line(screen, GRID_C, (x, gy), (x, gy + gh), 1)
        lbl = F_GAXIS.render(label, True, AXIS_C)
        screen.blit(lbl, (x - lbl.get_width()//2, gy + gh + 7))

    pygame.draw.line(screen, AXIS_C, (gx, gy),      (gx, gy + gh), 1)
    pygame.draw.line(screen, AXIS_C, (gx, gy + gh), (gx + gw, gy + gh), 1)

    unit_lbl = F_GAXIS.render('L/min', True, AXIS_C)
    screen.blit(unit_lbl, (4, gy - 2))

    if len(history) >= 2:
        def to_pt(i, val):
            px = gx + int(i / (HISTORY_LEN - 1) * gw)
            py = gy + gh - int((val / max_val) * gh)
            return (px, max(gy, min(gy + gh, py)))

        pts = [to_pt(i, v) for i, v in enumerate(history)]

        fill_poly = [(gx, gy + gh)] + pts + [(gx + gw, gy + gh)]
        for alpha, shrink in [(12, 0), (8, 4), (5, 8)]:
            surf = pygame.Surface((W, H), pygame.SRCALPHA)
            squeezed = [(px, min(gy + gh, py + shrink)) for px, py in fill_poly]
            pygame.draw.polygon(surf, (0, 229, 255, alpha), squeezed)
            screen.blit(surf, (0, 0))

        pygame.draw.lines(screen, CYAN_GLOW, False, pts, 1)
        pygame.draw.lines(screen, CYAN,      False, pts, 2)

        tip = pts[-1]
        pygame.draw.circle(screen, CYAN, tip, 6)
        pygame.draw.circle(screen, BG,   tip, 3)

    # Title
    title = F_GTITLE.render('F L O W   H I S T O R Y', True, MUTED)
    screen.blit(title, (gx, 14))

    # Live value + pump status (top right)
    color = CYAN if has_flow else CYAN_DIM
    cur = F_UNIT.render(f'{flow_lpm:.2f} L/min', True, color)
    screen.blit(cur, (W - cur.get_width() - PAD_R, 10))

    state = f'pump {"ON" if pump_on else "OFF"}    target {target_flow:.1f}    duty {current_duty:.0f}%'
    st = F_GAXIS.render(state, True, CYAN_DIM if pump_on else MUTED)
    screen.blit(st, (W - st.get_width() - PAD_R, 12 + cur.get_height()))

    # Total
    total = F_TOTAL.render(f'total   {total_liters:.3f} L', True, TOTAL_C)
    screen.blit(total, (gx, H - PAD_B + 28))

    draw_hint('tap to go back')


# ── Main loop ─────────────────────────────────────────────────────────────────
clock     = pygame.time.Clock()
last_tick = time.time()
running   = True

try:
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key in (pygame.K_g, pygame.K_SPACE):
                    show_graph = not show_graph
                elif event.key == pygame.K_o:
                    set_pump(not pump_on)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos
                if show_graph:
                    show_graph = False                      # tap anywhere returns
                elif power_btn_rect().collidepoint(mx, my):
                    set_pump(not pump_on)
                elif slider_hit_rect().collidepoint(mx, my):
                    dragging_slider = True
                    target_flow = x_to_target(mx)
                elif graph_hint_rect.collidepoint(mx, my):
                    show_graph = True

            elif event.type == pygame.MOUSEMOTION:
                if dragging_slider and not show_graph:
                    target_flow = x_to_target(event.pos[0])

            elif event.type == pygame.MOUSEBUTTONUP:
                dragging_slider = False

        # ── 1 Hz: measure flow, then run the control loop ──
        now = time.time()
        if now - last_tick >= 1.0:
            dt            = now - last_tick
            count         = pulse_count
            pulse_count   = 0
            flow_lps      = count / CALIBRATION_FACTOR
            flow_lpm      = flow_lps * 60
            total_liters += flow_lps
            has_flow      = count > 0
            flow_history.append(flow_lpm)

            if pump_on and target_flow > 0:
                feedforward = (target_flow / MAX_FLOW) * FF_DUTY_AT_MAX
                duty = pid.update(target_flow, flow_lpm, dt, feedforward)
            else:
                pid.reset()
                duty = 0.0
            current_duty = set_duty(duty)

            last_tick = now

        if show_graph:
            draw_graph_screen()
        else:
            draw_main_screen()

        pygame.display.flip()
        clock.tick(30)

finally:
    # Always stop the pump on exit or crash — never leave it running.
    set_duty(0)
    try:
        pwm.stop()
    except Exception:
        pass
    pygame.quit()

sys.exit()