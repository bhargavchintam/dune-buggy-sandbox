"""
Dune Buggy Sandbox  (Panda3D + Bullet)
======================================

A drivable dune-buggy sandbox built on the Bullet vehicle physics that ship
inside Panda3D. Rolling heightfield dunes, a chase camera, tunable suspension,
four jump ramps and three big loop rings to fly through, dust, and an engine
note that tracks speed.

    pip install panda3d        # only if Panda3D itself is not installed
    python dune_buggy_sandbox.py

Controls
    W / Up      accelerate          S / Down   brake, then reverse
    A / D       steer left/right    R          flip the buggy upright
    (Arrows mirror WASD)            Esc        quit

Almost everything (terrain, ramps, loops, wheels, dust) is generated in code, so
the game runs on its own. The buggy body is an optional external model (a CC0
Kenney car kit); without it the buggy falls back to a simple placeholder box.
"""

import os
import sys
import math
import random

from panda3d.core import (
    Vec3, Vec4, Point3, LColor,
    AmbientLight, DirectionalLight,
    GeoMipTerrain, PNMImage, Filename,
    TransformState, TransparencyAttrib, CardMaker,
    GeomVertexFormat, GeomVertexData, GeomVertexWriter,
    Geom, GeomTriangles, GeomNode, NodePath,
    SamplerState, ClockObject, loadPrcFileData,
)
from panda3d.bullet import (
    BulletWorld, BulletRigidBodyNode, BulletBoxShape, BulletVehicle,
    BulletHeightfieldShape, BulletTriangleMesh, BulletTriangleMeshShape,
    BulletConvexHullShape, ZUp,
)
from direct.showbase.ShowBase import ShowBase
from direct.gui.OnscreenText import OnscreenText
from direct.task import Task

globalClock = ClockObject.getGlobalClock()

# --------------------------------------------------------------------------- #
#  Tuning constants  --  "the feel lives here"                                 #
# --------------------------------------------------------------------------- #
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR    = os.path.join(SCRIPT_DIR, "Assets")

HEIGHTMAP    = 257            # (2^n)+1; bigger sheet -> a roomy sandbox to explore
TERRAIN_H    = 22.0           # tall enough for a containing rim; middle kept gentle below
RIM_PIXELS   = 26             # width of the raised "tall dune" border

CHASSIS_MASS = 800.0
TOP_SPEED    = 90.0           # km/h target top speed (engine eases off near it)
MAX_ENGINE   = 2600.0         # punchy accel -> reaches ramps/loops at speed quickly
MAX_BRAKE    = 130.0

LOOP_RADIUS  = 7.0            # big rings -- the buggy drives/jumps through the opening
LOOP_TUBE    = 0.55          # THIN tube (not thicker) -> large clear opening
RAMP_W       = 3.4           # ramp width
RAMP_L       = 4.5           # shorter ramp -> sharper kick, scrubs less speed
RAMP_H       = 2.0           # ramp launch height (~24deg slope -> reliable launch)
MAX_STEER    = 32.0           # degrees of front-wheel lock (lower -> stable at speed)
STEER_SPEED  = 5.0            # how fast steering eases toward target
WHEEL_RADIUS = 0.30

FRONT_SLIP   = 4.2            # front grip
REAR_SLIP    = 2.2            # looser rear -> a visible, controlled drift that recovers cleanly

# The tractor.egg ships lying on its side (its length runs along Z, Panda's up
# axis), so it must be stood upright. P=90 lifts it onto its wheels; H=180 turns
# the nose to +Y so it drives forward and the chase camera sees its rear.
BUGGY_HPR    = (180, 90, 0)
BUGGY_SCALE  = 1.05
BUGGY_Z      = -0.35          # drop so the model's own wheels rest on the ground

CHASE_BACK   = 9.0            # distance camera trails behind
CHASE_UP     = 4.0           # camera height above the buggy
CHASE_LERP   = 3.5            # camera smoothing (higher = snappier)

SAND_COLOR   = (0.85, 0.74, 0.50, 1.0)
LOOP_COLOR   = (0.62, 0.63, 0.66, 1.0)
RAMP_COLOR   = (0.92, 0.52, 0.18, 1.0)


# --------------------------------------------------------------------------- #
#  Small procedural-geometry helpers (no external models needed for these)    #
# --------------------------------------------------------------------------- #
def make_box(hx, hy, hz, color):
    """Axis-aligned box centered on origin, with per-vertex normals + color."""
    fmt = GeomVertexFormat.getV3n3c4()
    vdata = GeomVertexData("box", fmt, Geom.UHStatic)
    vw = GeomVertexWriter(vdata, "vertex")
    nw = GeomVertexWriter(vdata, "normal")
    cw = GeomVertexWriter(vdata, "color")
    faces = [
        ((1, 0, 0),  [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]),
        ((-1, 0, 0), [(-hx, hy, -hz), (-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz)]),
        ((0, 1, 0),  [(hx, hy, -hz), (-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz)]),
        ((0, -1, 0), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
        ((0, 0, 1),  [(hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz), (-hx, -hy, hz)]),
        ((0, 0, -1), [(-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz), (hx, -hy, -hz)]),
    ]
    tris = GeomTriangles(Geom.UHStatic)
    vi = 0
    for n, quad in faces:
        for p in quad:
            vw.addData3(*p)
            nw.addData3(*n)
            cw.addData4(*color)
        tris.addVertices(vi, vi + 1, vi + 2)
        tris.addVertices(vi, vi + 2, vi + 3)
        vi += 4
    geom = Geom(vdata)
    geom.addPrimitive(tris)
    node = GeomNode("box")
    node.addGeom(geom)
    np = NodePath(node)
    np.setTwoSided(True)
    return np


def make_wedge(width, length, height, color=RAMP_COLOR):
    """A proper jump ramp: flat base, a thin leading edge at z=0 that sits on the
    sand, and a slope rising to `height` at the far (back) end. Returns the visual
    NodePath plus the 6 corner points (for a Bullet convex-hull collision shape).

    Local frame: width along X, length along Y. Thin/leading edge at y=-length/2,
    the slope climbs to the tall back wall at y=+length/2.
    """
    hw, hl = width / 2.0, length / 2.0
    # left side (x=-hw): A leading-bottom, B back-bottom, C back-top
    A0, B0, C0 = (-hw, -hl, 0.0), (-hw, hl, 0.0), (-hw, hl, height)
    A1, B1, C1 = ( hw, -hl, 0.0), ( hw, hl, 0.0), ( hw, hl, height)
    slen = math.hypot(length, height)
    sn = (0.0, -height / slen, length / slen)          # outward slope normal (up + toward approach)
    faces = [
        (sn,          [A0, C0, C1, A1]),               # slope -- the drive surface
        ((0, 0, -1),  [A0, A1, B1, B0]),               # flat base on the sand
        ((0, 1, 0),   [B0, B1, C1, C0]),               # vertical back wall
        ((-1, 0, 0),  [A0, B0, C0]),                   # left triangular side
        ((1, 0, 0),   [A1, C1, B1]),                   # right triangular side
    ]
    fmt = GeomVertexFormat.getV3n3c4()
    vdata = GeomVertexData("wedge", fmt, Geom.UHStatic)
    vw = GeomVertexWriter(vdata, "vertex")
    nw = GeomVertexWriter(vdata, "normal")
    cw = GeomVertexWriter(vdata, "color")
    tris = GeomTriangles(Geom.UHStatic)
    vi = 0
    for n, quad in faces:
        for p in quad:
            vw.addData3(*p)
            nw.addData3(*n)
            cw.addData4(*color)
        tris.addVertices(vi, vi + 1, vi + 2)
        if len(quad) == 4:
            tris.addVertices(vi, vi + 2, vi + 3)
        vi += len(quad)
    geom = Geom(vdata)
    geom.addPrimitive(tris)
    node = GeomNode("wedge")
    node.addGeom(geom)
    np = NodePath(node)
    np.setTwoSided(True)
    return np, [A0, B0, C0, A1, B1, C1]


def make_torus(R, r, seg_major=56, seg_minor=16, color=LOOP_COLOR):
    """Upright ring (symmetry axis = X) returned as (visual NodePath, BulletTriangleMesh)."""
    fmt = GeomVertexFormat.getV3n3c4()
    vdata = GeomVertexData("torus", fmt, Geom.UHStatic)
    vw = GeomVertexWriter(vdata, "vertex")
    nw = GeomVertexWriter(vdata, "normal")
    cw = GeomVertexWriter(vdata, "color")
    verts = []
    for i in range(seg_major + 1):
        u = (i / seg_major) * 2.0 * math.pi
        cu, su = math.cos(u), math.sin(u)
        for j in range(seg_minor + 1):
            v = (j / seg_minor) * 2.0 * math.pi
            cv, sv = math.cos(v), math.sin(v)
            px = r * sv
            py = (R + r * cv) * cu
            pz = (R + r * cv) * su
            vw.addData3(px, py, pz)
            nw.addData3(sv, cv * cu, cv * su)
            cw.addData4(*color)
            verts.append((px, py, pz))
    stride = seg_minor + 1
    tris = GeomTriangles(Geom.UHStatic)
    mesh = BulletTriangleMesh()

    def idx(i, j):
        return i * stride + j

    for i in range(seg_major):
        for j in range(seg_minor):
            a, b = idx(i, j), idx(i + 1, j)
            c, d = idx(i + 1, j + 1), idx(i, j + 1)
            tris.addVertices(a, b, c)
            tris.addVertices(a, c, d)
            mesh.addTriangle(Point3(*verts[a]), Point3(*verts[b]), Point3(*verts[c]))
            mesh.addTriangle(Point3(*verts[a]), Point3(*verts[c]), Point3(*verts[d]))
    geom = Geom(vdata)
    geom.addPrimitive(tris)
    node = GeomNode("torus")
    node.addGeom(geom)
    np = NodePath(node)
    np.setTwoSided(True)
    return np, mesh


# --------------------------------------------------------------------------- #
#  Game                                                                        #
# --------------------------------------------------------------------------- #
class DuneBuggy(ShowBase):
    def __init__(self):
        ShowBase.__init__(self)
        if self.camera is None:            # window-type none (headless): no window means
            self.camera = self.render.attachNewNode("camera")  # ShowBase never made one
        self.disableMouse()
        self.setBackgroundColor(0.53, 0.81, 0.95)          # plain blue sky (reference look)

        self._setup_lighting()
        self._setup_physics()
        self._setup_terrain()
        self._setup_walls()
        self._setup_buggy()
        self._setup_ramps()
        self._setup_loops()
        self._setup_sound()
        self._setup_hud()
        self._setup_input()

        # runtime state
        self.steering = 0.0
        self.airborne = False
        self.dust = []                # list of [card, age, life]
        self._dust_timer = 0.0

        # start the camera roughly behind the buggy so frame 1 is not jarring
        self.camera.setPos(self.chassis.getPos() + Vec3(0, -CHASE_BACK, CHASE_UP))

        self.taskMgr.add(self._update, "update")

    # ------------------------------------------------------------------ #
    def _setup_lighting(self):
        amb = AmbientLight("amb")
        amb.setColor(Vec4(0.45, 0.45, 0.48, 1))
        self.render.setLight(self.render.attachNewNode(amb))

        sun = DirectionalLight("sun")
        sun.setColor(Vec4(0.9, 0.88, 0.78, 1))
        sun_np = self.render.attachNewNode(sun)
        sun_np.setHpr(-45, -55, 0)
        self.render.setLight(sun_np)

    def _setup_physics(self):
        self.world = BulletWorld()
        self.world.setGravity(Vec3(0, 0, -9.81))

    # ------------------------------------------------------------------ #
    #  Terrain: one heightmap drives both the visual mesh and collision. #
    #  Gentle rolling middle (keeps momentum) + raised rim (soft bound). #
    # ------------------------------------------------------------------ #
    def _setup_terrain(self):
        size = HEIGHTMAP
        off = size / 2.0 - 0.5
        img = PNMImage(size, size, 1)
        # Broad, drivable dune mounds (world x, y, height, radius) scattered in the
        # open areas BETWEEN the obstacle lanes, so there is terrain to roam and
        # crest without blocking any ramp/loop approach.
        dunes = [(-45,  40, 0.13, 32), ( 48, -42, 0.12, 30),
                 ( 52,  82, 0.13, 30), (-58, -58, 0.12, 28),
                 (-90,  90, 0.11, 34), ( 90, -88, 0.11, 34),
                 (  0, -20, 0.06, 46)]
        for y in range(size):
            wy = y - off
            for x in range(size):
                wx = x - off
                nx, ny = x / size, y / size
                rolling = (0.022 * math.sin(nx * 6.2832 * 4.0)
                           + 0.022 * math.cos(ny * 6.2832 * 3.2)
                           + 0.010 * math.sin((nx + ny) * 6.2832 * 7.0))
                mounds = 0.0
                for cx, cy, amp, rad in dunes:
                    d2 = (wx - cx) ** 2 + (wy - cy) ** 2
                    mounds += amp * math.exp(-d2 / (2.0 * rad * rad))
                base = 0.205 + rolling + mounds            # gentle dunes -> keeps momentum
                edge = min(x, y, size - 1 - x, size - 1 - y)
                rim = max(0.0, (RIM_PIXELS - edge) / RIM_PIXELS)   # 1 at border -> 0 inside
                rim = rim * rim * rim                       # steep, tall rim that turns the buggy back
                val = base + rim * (0.985 - base)
                img.setGray(x, y, max(0.0, min(1.0, val)))

        # save once so visual + collision read byte-identical data (self-contained)
        self.hm_path = os.path.join(SCRIPT_DIR, "heightmap.png")
        img.write(Filename.fromOsSpecific(self.hm_path))

        self.hm_img = PNMImage(Filename.fromOsSpecific(self.hm_path))
        self.hm_size = size
        self.hm_offset = size / 2.0 - 0.5                  # heightfield is centered on origin

        shape = BulletHeightfieldShape(self.hm_img, TERRAIN_H, ZUp)
        shape.setUseDiamondSubdivision(True)
        node = BulletRigidBodyNode("Terrain")
        node.addShape(shape)
        node.setFriction(0.95)
        self.terrain_np = self.render.attachNewNode(node)
        self.world.attachRigidBody(node)

        self.terrain = GeoMipTerrain("terrain")
        self.terrain.setHeightfield(Filename.fromOsSpecific(self.hm_path))
        self.terrain.setBlockSize(32)
        self.terrain.setMinLevel(2)
        self.terrain.getRoot().reparentTo(self.render)
        self.terrain.getRoot().setSz(TERRAIN_H)
        self.terrain.getRoot().setPos(-self.hm_offset, -self.hm_offset, -TERRAIN_H / 2.0)
        self.terrain.getRoot().setColor(*SAND_COLOR)
        self.terrain.generate()

    def _setup_walls(self):
        # Invisible backstop just inside the heightfield edge. The visible rim dune
        # still does the soft turn-back; this guarantees a fast buggy can never
        # crest the rim and fall off the world into the void.
        b = self.hm_offset - 1.0
        h = 30.0
        specs = [((0, b, 0), (b, 0.5, h)), ((0, -b, 0), (b, 0.5, h)),
                 ((b, 0, 0), (0.5, b, h)), ((-b, 0, 0), (0.5, b, h))]
        for pos, he in specs:
            node = BulletRigidBodyNode("Wall")
            node.addShape(BulletBoxShape(Vec3(*he)))
            np = self.render.attachNewNode(node)
            np.setPos(*pos)
            self.world.attachRigidBody(node)

    def ground_z(self, wx, wy):
        """World-space terrain height under (wx, wy), using the same heightmap."""
        px = int(round(wx + self.hm_offset))
        py = int(round(wy + self.hm_offset))
        px = max(0, min(self.hm_size - 1, px))
        py = max(0, min(self.hm_size - 1, py))
        return self.hm_img.getGray(px, py) * TERRAIN_H - TERRAIN_H / 2.0

    # ------------------------------------------------------------------ #
    def _setup_buggy(self):
        shape = BulletBoxShape(Vec3(0.7, 1.4, 0.5))
        ts = TransformState.makePos(Point3(0, 0, 0.5))
        node = BulletRigidBodyNode("Buggy")
        node.addShape(shape, ts)
        node.setMass(CHASSIS_MASS)
        node.setDeactivationEnabled(False)
        self.chassis = self.render.attachNewNode(node)

        gz = self.ground_z(0, 0) + 1.8
        self.chassis.setPos(0, 0, gz)
        self.world.attachRigidBody(node)

        # buggy art rides the physics body. The model already includes its own
        # round wheels (small front, big rear), so we stand it upright and let
        # those be the visible wheels -- the Bullet vehicle wheels stay invisible.
        try:
            model = self.loader.loadModel(
                Filename.fromOsSpecific(os.path.join(ASSET_DIR, "CarKit", "tractor.egg")))
            # The egg ships with UVs but NO texture binding (lost in obj->egg
            # conversion), so it renders flat white. Apply the CarKit palette
            # atlas the UVs point into; nearest filtering keeps the colour cells
            # crisp and stops them bleeding into one another.
            tex = self.loader.loadTexture(
                Filename.fromOsSpecific(os.path.join(ASSET_DIR, "CarKit", "Textures", "colormap.png")))
            tex.setMagfilter(SamplerState.FT_nearest)
            tex.setMinfilter(SamplerState.FT_nearest)
            model.setTexture(tex, 1)   # priority 1 overrides any default appearance
            model.reparentTo(self.chassis)
            model.setHpr(*BUGGY_HPR)   # stand it upright, nose forward
            model.setScale(BUGGY_SCALE)
            model.setPos(0, 0, BUGGY_Z)
            self.buggy_model = model
        except Exception as exc:
            print("Could not load tractor.egg (%s) -- using a placeholder box." % exc)
            box = make_box(0.7, 1.4, 0.5, (0.85, 0.25, 0.2, 1))
            box.reparentTo(self.chassis)

        # ---- Bullet vehicle: wheels, suspension, steering, grip for free ----
        self.vehicle = BulletVehicle(self.world, node)
        self.vehicle.setCoordinateSystem(ZUp)
        self.world.attachVehicle(self.vehicle)

        self.wheel_nps = []
        cp = 0.30   # suspension connection height on the chassis
        self._add_wheel(Point3( 0.75,  1.05, cp), True,  FRONT_SLIP)   # 0 front-L
        self._add_wheel(Point3(-0.75,  1.05, cp), True,  FRONT_SLIP)   # 1 front-R
        self._add_wheel(Point3( 0.75, -1.05, cp), False, REAR_SLIP)    # 2 rear-L
        self._add_wheel(Point3(-0.75, -1.05, cp), False, REAR_SLIP)    # 3 rear-R
        self.rear_wheels = [self.wheel_nps[2], self.wheel_nps[3]]

    def _add_wheel(self, pos, is_front, friction):
        # Invisible physics wheel -- the tractor model supplies the visible wheels.
        # We keep the node so suspension/contact positions are available for dust.
        np = self.render.attachNewNode("wheel")
        self.wheel_nps.append(np)

        w = self.vehicle.createWheel()
        w.setNode(np.node())
        w.setChassisConnectionPointCs(pos)
        w.setFrontWheel(is_front)
        w.setWheelDirectionCs(Vec3(0, 0, -1))
        w.setWheelAxleCs(Vec3(1, 0, 0))
        w.setWheelRadius(WHEEL_RADIUS)
        w.setMaxSuspensionTravelCm(45.0)
        w.setSuspensionStiffness(35.0)
        w.setWheelsDampingCompression(4.4)
        w.setWheelsDampingRelaxation(2.3)
        w.setFrictionSlip(friction)        # rear < front -> the back slides on sand
        w.setRollInfluence(0.1)

    # ------------------------------------------------------------------ #
    def _make_static(self, name, shape, pos, hpr=(0, 0, 0)):
        node = BulletRigidBodyNode(name)
        node.addShape(shape)
        node.setFriction(0.9)
        np = self.render.attachNewNode(node)
        np.setPos(*pos)
        np.setHpr(*hpr)
        self.world.attachRigidBody(node)
        return np

    def _setup_ramps(self):
        # 4 wedge ramps spread to the four corners of the sandbox. Three are the
        # launch half of a ramp->loop COMBO (drive up, jump, shoot the loop just
        # beyond); the fourth is a standalone jump. Each faces +Y with a long, clear
        # southern runway. Seated so the slope rises out of the sand, no lip.
        layout = [(-78, -12, 0),    # combo W   (loop ~14 past, fly through it)
                  ( -8,  52, 0),    # combo N   (loop ~15 past)
                  ( 78,   4, 0),    # combo E   (loop ~14 past)
                  (-10, -75, 270)]  # standalone jump in the south (drive +X, for variety)
        for (x, y, h) in layout:
            vis, pts = make_wedge(RAMP_W, RAMP_L, RAMP_H)
            shape = BulletConvexHullShape()
            for p in pts:
                shape.addPoint(Point3(*p))
            node = BulletRigidBodyNode("Ramp")
            node.addShape(shape)
            node.setFriction(0.9)
            np = self.render.attachNewNode(node)
            np.setPosHpr(x, y, 0, h, 0, 0)
            # Seat by the LOWEST ground under the leading edge and the approach just
            # in front of it, then embed a little, so the slope always rises out of
            # the sand with no lip to catch on -- climbable from any approach.
            lead = self.render.getRelativePoint(np, Point3(0, -RAMP_L / 2.0, 0))
            ahead = self.render.getRelativePoint(np, Point3(0, -RAMP_L / 2.0 - 2.0, 0))
            base = min(self.ground_z(lead.x, lead.y), self.ground_z(ahead.x, ahead.y))
            np.setZ(base - 0.35)
            self.world.attachRigidBody(node)
            vis.reparentTo(np)

    def _setup_loops(self):
        # 3 big, thin upright rings standing ACROSS the approach so the buggy drives
        # (or jumps) straight through the opening, which reaches down to the sand.
        # h=90 turns each ring's hole to face +Y (the approach direction). Each loop
        # is the second half of a combo: ~22 units past its ramp, so a committed run
        # jumps the ramp and shoots straight through the ring.
        R, r = LOOP_RADIUS, LOOP_TUBE
        layout = [(-78, 2, 90), (-8, 67, 90), (78, 18, 90)]
        for (x, y, h) in layout:
            vis, mesh = make_torus(R, r)
            shape = BulletTriangleMeshShape(mesh, dynamic=False)
            # Plant the ring slightly into the sand: the bottom tube sits just below
            # ground (no lip across the path) leaving a wide, clean ground-level
            # opening the buggy drives through.
            gz = self.ground_z(x, y) + R - r - 0.7
            np = self._make_static("Loop", shape, (x, y, gz), (h, 0, 0))
            vis.reparentTo(np)

    # ------------------------------------------------------------------ #
    def _setup_sound(self):
        self.engine_snd = None
        self.whoosh_snd = None
        try:
            self.engine_snd = self.loader.loadSfx(
                Filename.fromOsSpecific(os.path.join(ASSET_DIR, "242740__marlonhj__engine.wav")))
            self.engine_snd.setLoop(True)
            self.engine_snd.setVolume(0.35)
            self.engine_snd.play()
        except Exception as exc:
            print("Engine sound unavailable:", exc)
        try:
            self.whoosh_snd = self.loader.loadSfx(
                Filename.fromOsSpecific(os.path.join(ASSET_DIR, "389590__jofae__swing-woosh.wav")))
            self.whoosh_snd.setVolume(0.6)
        except Exception as exc:
            print("Whoosh sound unavailable:", exc)

    def _setup_hud(self):
        self.speed_text = OnscreenText(
            text="Speed: 0", pos=(-1.25, 0.88), scale=0.08,
            fg=(1, 1, 1, 1), shadow=(0, 0, 0, 0.6), align=0, mayChange=True)
        OnscreenText(
            text="Arrows / WASD drive     R to reset",
            pos=(0, -0.92), scale=0.06,
            fg=(1, 1, 1, 1), shadow=(0, 0, 0, 0.6), align=2, mayChange=False)

    def _setup_input(self):
        self.keys = {"fwd": False, "back": False, "left": False, "right": False}
        mapping = {
            "w": "fwd", "arrow_up": "fwd",
            "s": "back", "arrow_down": "back",
            "a": "left", "arrow_left": "left",
            "d": "right", "arrow_right": "right",
        }
        for key, act in mapping.items():
            self.accept(key, self._set_key, [act, True])
            self.accept(key + "-up", self._set_key, [act, False])
        self.accept("r", self._reset)
        self.accept("escape", sys.exit)

    def _set_key(self, act, val):
        self.keys[act] = val

    # ------------------------------------------------------------------ #
    #  R: set the buggy back on its wheels at its current x,y            #
    # ------------------------------------------------------------------ #
    def _reset(self):
        pos = self.chassis.getPos()
        gz = self.ground_z(pos.x, pos.y) + 1.8
        self.chassis.setPos(pos.x, pos.y, gz)
        self.chassis.setHpr(self.chassis.getH(), 0, 0)     # level it, keep heading
        node = self.chassis.node()
        node.setLinearVelocity(Vec3(0))
        node.setAngularVelocity(Vec3(0))
        self.steering = 0.0

    # ------------------------------------------------------------------ #
    #  Per-frame                                                          #
    # ------------------------------------------------------------------ #
    def _update(self, task):
        dt = globalClock.getDt()
        dt = min(dt, 1.0 / 30.0)        # clamp big hitches so physics stays stable

        self._control_vehicle(dt)
        self.world.doPhysics(dt, 10, 1.0 / 180.0)
        self._update_camera(dt)
        self._update_effects(dt)
        self._update_sound()
        self._update_hud()
        return Task.cont

    def _control_vehicle(self, dt):
        spd = self.vehicle.getCurrentSpeedKmHour()

        # --- speed-sensitive steering: full lock when slow (tight, maneuverable),
        #     much less lock at speed so a hard turn slides instead of spinning out ---
        speed_frac = min(abs(spd), TOP_SPEED) / TOP_SPEED
        steer_limit = MAX_STEER * (1.0 - 0.6 * speed_frac)
        target = (1.0 if self.keys["left"] else 0.0) - (1.0 if self.keys["right"] else 0.0)
        target *= steer_limit
        self.steering += (target - self.steering) * min(1.0, STEER_SPEED * dt)
        self.vehicle.setSteeringValue(self.steering, 0)
        self.vehicle.setSteeringValue(self.steering, 1)

        # --- engine / brake / reverse ---
        engine, brake = 0.0, 0.0
        if self.keys["fwd"]:
            if spd < TOP_SPEED:                       # ease off as we near top speed
                engine = MAX_ENGINE * (1.0 - max(0.0, spd) / TOP_SPEED)
        elif self.keys["back"]:
            if spd > 1.0:                             # still rolling forward -> brake
                brake = MAX_BRAKE
            else:                                     # stopped -> reverse
                engine = -MAX_ENGINE * 0.5
        else:
            brake = 6.0                               # gentle idle drag

        # ease power when cornering hard so a floored turn drifts in a clean arc
        # instead of snapping the rear loose into a wobbly donut
        if engine > 0:
            engine *= 1.0 - 0.4 * (abs(self.steering) / MAX_STEER)

        self.vehicle.applyEngineForce(engine, 2)      # rear-wheel drive
        self.vehicle.applyEngineForce(engine, 3)
        for i in range(4):
            self.vehicle.setBrake(brake, i)

    def _update_camera(self, dt):
        h = math.radians(self.chassis.getH())         # yaw only -> stable on loops/jumps
        back = Vec3(math.sin(h), -math.cos(h), 0.0)   # world-space "behind the buggy"
        bpos = self.chassis.getPos()
        target = bpos + back * CHASE_BACK + Vec3(0, 0, CHASE_UP)

        alpha = min(1.0, CHASE_LERP * dt)             # smooth trail; swings behind in turns
        self.camera.setPos(self.camera.getPos() * (1 - alpha) + target * alpha)
        self.camera.lookAt(bpos + Vec3(0, 0, 1.0))    # buggy low in frame, ground ahead

    def _update_effects(self, dt):
        bpos = self.chassis.getPos()
        clearance = bpos.z - self.ground_z(bpos.x, bpos.y)

        # --- airborne edge detection (drives the whoosh + a landing dust burst) ---
        now_air = clearance > 2.6
        if now_air and not self.airborne:
            self.airborne = True
            self.just_launched = True
        elif not now_air and self.airborne:
            self.airborne = False
            for wheel in self.rear_wheels:            # kick up dust on touchdown
                for _ in range(5):
                    self._spawn_dust(wheel.getPos(self.render))

        # --- continuous dust trail while driving on the ground ---
        spd = abs(self.vehicle.getCurrentSpeedKmHour())
        if not self.airborne and spd > 14.0:
            self._dust_timer -= dt
            if self._dust_timer <= 0.0:
                self._dust_timer = 0.05
                self._spawn_dust(random.choice(self.rear_wheels).getPos(self.render))

        # --- age existing dust puffs ---
        alive = []
        for d in self.dust:
            d[1] += dt
            t = d[1] / d[2]
            if t >= 1.0:
                d[0].removeNode()
                continue
            d[0].setScale(0.3 + t * 1.3)
            d[0].setColor(1, 1, 1, 0.8 * (1.0 - t))
            alive.append(d)
        self.dust = alive

    def _spawn_dust(self, pos):
        cm = CardMaker("dust")
        cm.setFrame(-0.3, 0.3, -0.3, 0.3)
        card = self.render.attachNewNode(cm.generate())
        card.setBillboardPointEye()
        card.setTransparency(TransparencyAttrib.MAlpha)
        card.setLightOff()
        card.setColor(1, 1, 1, 0.8)
        card.setPos(pos + Vec3(random.uniform(-0.2, 0.2), random.uniform(-0.2, 0.2), 0.1))
        self.dust.append([card, 0.0, random.uniform(0.4, 0.7)])

    def _update_sound(self):
        spd = abs(self.vehicle.getCurrentSpeedKmHour())
        f = min(spd, TOP_SPEED) / TOP_SPEED
        if self.engine_snd:
            self.engine_snd.setPlayRate(0.6 + f * 1.4)    # note rises/falls with speed
            self.engine_snd.setVolume(0.3 + 0.5 * f)
        if self.whoosh_snd and getattr(self, "just_launched", False):
            self.whoosh_snd.play()
        self.just_launched = False

    def _update_hud(self):
        self.speed_text.setText("Speed: %d" % int(abs(self.vehicle.getCurrentSpeedKmHour())))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=None,
                         help="run headless for N task-manager frames and exit, instead of opening a window")
    args = parser.parse_args()

    if args.frames is not None:
        loadPrcFileData("", "window-type none")
        loadPrcFileData("", "audio-library-name null")
        app = DuneBuggy()
        for _ in range(args.frames):
            app.taskMgr.step()
    else:
        DuneBuggy().run()
