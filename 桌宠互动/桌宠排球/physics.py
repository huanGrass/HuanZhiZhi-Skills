"""Game rules only. No desktop-pet host imports or native mouse operations."""
from dataclasses import dataclass
from collections import deque
import math
import random


WIDTH, HEIGHT = 960, 560
LEFT, RIGHT, CEILING, FLOOR = 40, 920, 116, 498
NET_X, NET_TOP, NET_HALF = 480, 316, 4
BALL_RADIUS, PADDLE_RADIUS = 12, 27
GRAVITY = 740.0


def clamp(value, low, high):
    return max(low, min(high, value))


@dataclass
class Body:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0


class Match:
    def __init__(self, *, seed=None, ai_limits=(548, 845, 245, 450), head_radius=35,
                 head_radius_x=None):
        self.random = random.Random(seed)
        self.ai_limits = ai_limits
        self.head_radius = head_radius
        self.head_radius_x = head_radius if head_radius_x is None else head_radius_x
        self.player = Body(230, 420)
        self.opponent = Body(clamp(730, *ai_limits[:2]), clamp(420, *ai_limits[2:]))
        self.ball = Body(0, 0)
        self.target = (230, 420)
        self.ai_target = (self.opponent.x, self.opponent.y)
        self.score = [0, 0]
        self.events = deque(maxlen=16)
        self.state = 'ready'
        self._resume_state = 'playing'
        self._serve_in = 1.2
        self.server = 0
        self._place_serve_ball()
        self.message = '移动鼠标准备'
        self.rally = 0
        self.best_rally = 0
        self.contact_flash = 0.0
        self.last_contact = None
        self._think_in = 0.0
        self._accumulator = 0.0
        self._collision_cooldown = 0.0
        self.boundary_flash = {}
        self.head_motion = 'idle'
        self._prepare = 0.0
        self._strike_time = None
        self._strike_origin = (self.opponent.x, self.opponent.y)

    def aim(self, x, y):
        self.target = (clamp(x, LEFT+PADDLE_RADIUS, NET_X-38),
                       clamp(y, CEILING+65, FLOOR-PADDLE_RADIUS-2))

    def _place_serve_ball(self):
        body = self.player if self.server == 0 else self.opponent
        rx = (PADDLE_RADIUS if self.server == 0 else self.head_radius_x)+BALL_RADIUS+1
        ry = (PADDLE_RADIUS if self.server == 0 else self.head_radius)+BALL_RADIUS+1
        # Rest against the upper edge facing the net, using the same anatomy
        # as collision detection, with a small gap to avoid an immediate hit.
        offset = .5 if self.server == 0 else .8
        self.ball.x = body.x+rx*offset*(1 if self.server == 0 else -1)
        self.ball.y = body.y-ry*math.sqrt(1-offset*offset)
        self.ball.vx = self.ball.vy = 0

    def serve(self):
        if self.state != 'ready':
            return
        self.rally = 0
        self.state = 'playing'
        self.message = ''
        self._collision_cooldown = .25
        self._think_in = 0.0
        self._accumulator = 0.0
        self._strike_time = None
        self._prepare = 0.0
        self.head_motion = 'idle'
        self._place_serve_ball()
        self.ball.vx, self.ball.vy = (310 if self.server == 0 else -310), -450
        self.events.append(('serve', self.server))
        if self.server == 1:
            self._strike_origin = (self.opponent.x, self.opponent.y)
            self._strike_time = 0.0
            self.head_motion = 'strike'

    def advance(self, elapsed):
        if self.state == 'paused' or elapsed <= 0:
            return
        # Fixed substeps prevent tunnelling during fast mouse swings or slow paints.
        self._accumulator += min(elapsed, .1)
        while self._accumulator >= 1/120:
            self._accumulator -= 1/120
            self._step(1/120)

    def pause(self):
        if self.state in {'playing', 'ready'}:
            self._resume_state = self.state
            self.state = 'paused'
            self._accumulator = 0.0
        elif self.state == 'paused':
            self.state = self._resume_state

    @staticmethod
    def _move(body, target, speed, dt):
        dx, dy = target[0]-body.x, target[1]-body.y
        length = math.hypot(dx, dy)
        ratio = min(1.0, speed*dt/length) if length else 0.0
        body.vx, body.vy = dx*ratio/dt, dy*ratio/dt
        body.x += body.vx*dt
        body.y += body.vy*dt

    def _step(self, dt):
        self._move(self.player, self.target, 1500, dt)
        self.contact_flash = max(0.0, self.contact_flash-dt)
        self.boundary_flash = {edge: age-dt for edge, age in self.boundary_flash.items() if age > dt}
        if self.state != 'playing':
            if self._strike_time is not None:
                self._move_opponent(dt)
            if self.state == 'ready':
                self._place_serve_ball()
            if self.state == 'ready' and self.server == 1:
                self._serve_in -= dt
                if self._serve_in <= 0:
                    self.serve()
            return
        self._collision_cooldown = max(0.0, self._collision_cooldown-dt)
        self._think_in -= dt
        b = self.ball
        if self._think_in <= 0:
            self._think_in = .17
            low_x, high_x, low_y, high_y = self.ai_limits
            # Stand slightly right of the falling ball to use the left crown.
            # Return direction comes from contact geometry, not the player's side.
            offset = self.head_radius_x*.42
            contact_height = (self.head_radius+BALL_RADIUS)*math.sqrt(1-(offset/(self.head_radius_x+BALL_RADIUS))**2)
            # Intercept the descending arc at net clearance, rather than chase
            # the ball downwards and absorb its rebound at the instant of contact.
            ty = clamp(NET_TOP-52+contact_height, low_y+14, high_y-6)
            contact_y = ty-contact_height
            discriminant = b.vy*b.vy+2*GRAVITY*(contact_y-b.y)
            lead = clamp((-b.vy+math.sqrt(discriminant))/GRAVITY, 0, 1.2) if discriminant >= 0 else .15
            tx = b.x+b.vx*lead+offset+self.random.uniform(-7, 7)
            self.ai_target = (clamp(tx, low_x, high_x), ty)
        self._move_opponent(dt)
        previous_x, previous_y = b.x, b.y
        b.vy += GRAVITY*dt
        b.x += b.vx*dt
        b.y += b.vy*dt
        if b.x < LEFT+BALL_RADIUS:
            b.x, b.vx = LEFT+BALL_RADIUS, abs(b.vx)*.92
            self.boundary_flash['left'] = .22
        elif b.x > RIGHT-BALL_RADIUS:
            b.x, b.vx = RIGHT-BALL_RADIUS, -abs(b.vx)*.92
            self.boundary_flash['right'] = .22
        if b.y < CEILING+BALL_RADIUS:
            b.y, b.vy = CEILING+BALL_RADIUS, abs(b.vy)*.65
            self.boundary_flash['top'] = .22
        self._net_collision(previous_x, previous_y)
        if self._collision_cooldown <= 0:
            for side, paddle in enumerate((self.player, self.opponent)):
                if (b.x < NET_X) == (side == 0) and self._hit(paddle, side):
                    break
        if b.y >= FLOOR-BALL_RADIUS:
            winner = 1 if b.x < NET_X else 0
            self.score[winner] += 1
            self.events.append(('score', winner))
            self.server = 1-winner
            self._serve_in = 1.2
            self._place_serve_ball()
            self.boundary_flash['bottom'] = .35
            self.state = 'ready'
            self.message = (
                '漂亮！你得一分 · 之之发球' if winner == 0 else '之之得一分 · 轮到你发球')

    def _move_opponent(self, dt):
        low_x, high_x, low_y, high_y = self.ai_limits
        if self._strike_time is not None:
            self._strike_time += dt
            age = self._strike_time
            if age < .09:
                offset = -14*math.sin(age/.09*math.pi/2)
                self.head_motion = 'strike'
            else:
                progress = clamp((age-.09)/.24, 0, 1)
                offset = -14*(1-progress*progress*(3-2*progress))
                self.head_motion = 'recover'
            target = (self._strike_origin[0], clamp(self._strike_origin[1]+offset, low_y, high_y))
            if age >= .33:
                self._strike_time = None
                self._prepare = 0.0
                self.head_motion = 'idle'
                self._think_in = 0.0
        else:
            b, head = self.ball, self.opponent
            imminent = (b.vy > 0 and b.x > NET_X and
                        abs(b.x-head.x) < self.head_radius_x+50 and
                        0 < head.y-self.head_radius-b.y < 95)
            desired = 6.0 if imminent else 0.0
            self._prepare += clamp(desired-self._prepare, -40*dt, 40*dt)
            self.head_motion = 'prepare' if self._prepare > .1 else 'idle'
            # Leave room for the small preparatory dip even close to the ground.
            target = (self.ai_target[0], clamp(self.ai_target[1], low_y+14, high_y-6)+self._prepare)
        self._move(self.opponent, target, 330, dt)

    def _hit(self, paddle, side):
        b = self.ball
        dx, dy = b.x-paddle.x, b.y-paddle.y
        rx = (PADDLE_RADIUS if side == 0 else self.head_radius_x)+BALL_RADIUS
        ry = (PADDLE_RADIUS if side == 0 else self.head_radius)+BALL_RADIUS
        distance = math.hypot(dx/rx, dy/ry)
        if distance > 1:
            return False
        sx, sy = (dx/distance, dy/distance) if distance > .001 else (0, -ry)
        normal_length = math.hypot(sx/(rx*rx), sy/(ry*ry))
        nx, ny = sx/(rx*rx)/normal_length, sy/(ry*ry)/normal_length
        if side == 1 and dy >= 0:
            return False  # Only the upper half of the head, never a hand/vehicle.
        relative_x, relative_y = b.vx-paddle.vx, b.vy-paddle.vy
        approach = relative_x*nx+relative_y*ny
        if approach >= 0:
            return False
        b.x, b.y = paddle.x+sx+nx, paddle.y+sy+ny
        # Reflect relative motion along the actual contact normal. A bounded
        # elastic impulse makes soft taps useful without prescribing a direction.
        rebound = clamp(-approach*.82+180, 300, 650)
        b.vx = (relative_x-approach*nx)*.72+nx*rebound+paddle.vx
        b.vy = (relative_y-approach*ny)*.72+ny*rebound+paddle.vy
        if side == 1:
            # Shallow upper-side contacts must lift the ball too.
            b.vy = -abs(b.vy)
        speed = math.hypot(b.vx, b.vy)
        if speed > 780:
            b.vx *= 780/speed
            b.vy *= 780/speed
        if side == 1:
            self._strike_origin = (paddle.x, paddle.y)
            self._strike_time = 0.0
            self.head_motion = 'strike'
        self._collision_cooldown = .10
        self.rally += 1
        self.best_rally = max(self.best_rally, self.rally)
        self.last_contact = (b.x-nx*(BALL_RADIUS+1), b.y-ny*(BALL_RADIUS+1), side)
        self.contact_flash = .28
        self.events.append(('hit', side))
        return True

    def _net_collision(self, previous_x, previous_y):
        b = self.ball
        edge = NET_HALF+BALL_RADIUS
        if abs(b.x-NET_X) >= edge or b.y+BALL_RADIUS < NET_TOP:
            return
        if previous_y+BALL_RADIUS <= NET_TOP and b.vy > 0:
            b.y, b.vy = NET_TOP-BALL_RADIUS, -b.vy*.7
        elif previous_x <= NET_X:
            b.x, b.vx = NET_X-edge, -abs(b.vx)*.75
        else:
            b.x, b.vx = NET_X+edge, abs(b.vx)*.75
        self.events.append(('net', None))
