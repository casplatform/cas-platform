#!/usr/bin/env python3
"""
CAS Maneuver Trade Space — Sprint #7 (offline core)
===================================================
NASA CARA MTS-style (lead_time x delta-v) sweep for a single conjunction.

Positioning: DECISION SUPPORT. CAS evaluates maneuver options; the operator
decides and executes. No autonomous execution.

Strangler pattern: cas_engine.py is NOT modified. Physics below is copied
VERBATIM from the live engine (line refs vs the 6469-line build, 2026-06-12)
so results are engine-consistent and traceable.

Honesty gate (IS/IS-NOT): public Space-Track CDMs carry no covariance, so a
true post-maneuver Pc trade space cannot be computed. Primary axis = MISS
DISTANCE. Pc values are assumed-sigma SCREENING numbers and labeled as such.
Full Pc trade space unlocks with operator covariance (G1 gate).

Dynamics:
  pre-burn states : SGP4 (TEME, km->m) at burn epoch / TCA window
  post-burn arc   : RK4 two-body + J2 (engine `propagate` parity)
Both objects evaluated in the same frame at the same instants -> relative
geometry is frame-consistent. Grid deltas are computed against a no-burn
RK4 arc from the SAME burn epoch, so integrator bias cancels differentially.
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sgp4.api import Satrec, jday

def _dsn():
    import os as _o
    v = _o.environ.get("DB_URL")
    if v: return v
    e = {}
    with open("/opt/cas/.env") as f:
        for ln in f:
            if "=" in ln and not ln.startswith("#"):
                k, val = ln.strip().split("=", 1)
                e[k] = val.strip().strip('"').strip("'")
    return e["DB_URL"]


# ── Engine-parity constants (cas_engine.py L1593-1597, L1688) ──────────────
PI  = math.pi
DEG = PI / 180.0
MU  = 3.986004418e14   # m^3/s^2
RE  = 6.3781363e6      # m
J2  = 1.08262668e-3

CATALOG_CACHE_FILE = "/opt/cas/.spacetrack_catalog_cache.json"
_CATALOG_TTL = 6 * 3600

# ── Trade-space defaults ────────────────────────────────────────────────────
DEFAULT_LEADS_H    = [2, 4, 6, 8, 12, 18, 24, 36, 48]
DEFAULT_DVS_MS     = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5]
DEFAULT_DIRECTIONS = ("prograde", "retrograde")
FINE_HALF_WINDOW_S = 120     # 1 s fine sweep around nominal TCA
COARSE_MAX_STEPS   = 600
SAFE_MISS_M        = 1000.0  # parity: risk_level GREEN miss bound
SAFE_PC            = 1e-6    # parity: engine candidate target_Pc

ENGINE_PARITY = [
    "parse_tle: cas_engine.py L1779-1813",
    "mean_to_eccentric: L1816-1824",
    "orbital_to_eci: L1827-1857",
    "propagate (RK4+J2): L1861-1900",
    "collision_probability (isotropic Rician): L1927-1943",
    "_bessel_i0: L1946-1959",
    "risk_level: L1962-1967",
    "compute_post_maneuver_state: L3614-3648",
    "screen_conjunctions: L3653-3758 (verbatim, incl. stubbed fine-pass)",
    "compute_sigma_from_covariance: L4117-4162",
    "catalog loader: adapted from fetch_catalog_tles L3560-3609 (module memo)",
]

# ════════════════════════════════════════════════════════════════════════════
# VERBATIM ENGINE PHYSICS (do not edit — parity with cas_engine.py)
# ════════════════════════════════════════════════════════════════════════════

def parse_tle(name: str, line1: str, line2: str) -> dict:
    """TLE satırlarını orbital elemanlara çevirir."""
    try:
        inc   = float(line2[8:16])
        raan  = float(line2[17:25])
        ecc   = float("0." + line2[26:33].strip())
        aop   = float(line2[34:42])
        ma    = float(line2[43:51])
        mm    = float(line2[52:63])
        norad = line2[2:7].strip()
    except Exception as e:
        raise ValueError(f"TLE parse hatası [{name}]: {e}")

    n = mm * 2 * PI / 86400.0
    a = (MU / n**2) ** (1/3)

    E  = mean_to_eccentric(ma * DEG, ecc)
    nu = 2 * math.atan2(
        math.sqrt(1 + ecc) * math.sin(E / 2),
        math.sqrt(1 - ecc) * math.cos(E / 2)
    )

    return {
        "name":  name.strip(),
        "norad": norad,
        "a":     a,
        "e":     ecc,
        "i":     inc  * DEG,
        "raan":  raan * DEG,
        "aop":   aop  * DEG,
        "nu":    nu,
        "mm":    mm,
        "line1": line1,
        "line2": line2,
    }


def mean_to_eccentric(M: float, e: float, tol: float = 1e-10) -> float:
    """Newton iterasyonu ile Kepler denklemi çözümü."""
    E = M if e < 0.8 else PI
    for _ in range(100):
        dE = (M - E + e * math.sin(E)) / (1 - e * math.cos(E))
        E += dE
        if abs(dE) < tol:
            break
    return E


def orbital_to_eci(orb: dict) -> Tuple[List[float], List[float]]:
    """Orbital elemanlar → ECI konum+hız (metre, m/s)."""
    a, e, i   = orb["a"], orb["e"], orb["i"]
    raan, aop = orb["raan"], orb["aop"]
    nu        = orb["nu"]

    p  = a * (1 - e**2)
    r  = p / (1 + e * math.cos(nu))
    h  = math.sqrt(MU * p)

    rx = r * math.cos(nu)
    ry = r * math.sin(nu)
    vx = -MU / h * math.sin(nu)
    vy =  MU / h * (e + math.cos(nu))

    ci, si = math.cos(i),    math.sin(i)
    cr, sr = math.cos(raan), math.sin(raan)
    cw, sw = math.cos(aop),  math.sin(aop)

    R = [
        [cr*cw - sr*sw*ci,  -cr*sw - sr*cw*ci,  sr*si],
        [sr*cw + cr*sw*ci,  -sr*sw + cr*cw*ci, -cr*si],
        [sw*si,              cw*si,              ci   ],
    ]

    def mv(R, v):
        return [sum(R[row][col]*v[col] for col in range(3)) for row in range(3)]

    pos = mv(R, [rx, ry, 0])
    vel = mv(R, [vx, vy, 0])
    return pos, vel


def propagate(pos: List[float], vel: List[float],
              dt: float, steps: int) -> Tuple[List[List[float]], List[List[float]]]:
    """RK4 integrasyon — J2 pertürbasyonu dahil."""
    def accel(p):
        x, y, z = p
        r2 = x*x + y*y + z*z
        r  = math.sqrt(r2)
        r3 = r2 * r
        r5 = r3 * r2
        fac = -MU / r3
        j2f = 1.5 * J2 * MU * RE**2 / r5
        zr2 = (z/r)**2
        ax = fac*x + j2f*x*(1 - 5*zr2)
        ay = fac*y + j2f*y*(1 - 5*zr2)
        az = fac*z + j2f*z*(3 - 5*zr2)
        return [ax, ay, az]

    def rk4(p, v, h):
        def f(p, v): return v, accel(p)
        k1p, k1v = f(p, v)
        p2 = [p[j]+0.5*h*k1p[j] for j in range(3)]
        v2 = [v[j]+0.5*h*k1v[j] for j in range(3)]
        k2p, k2v = f(p2, v2)
        p3 = [p[j]+0.5*h*k2p[j] for j in range(3)]
        v3 = [v[j]+0.5*h*k2v[j] for j in range(3)]
        k3p, k3v = f(p3, v3)
        p4 = [p[j]+h*k3p[j] for j in range(3)]
        v4 = [v[j]+h*k3v[j] for j in range(3)]
        np_ = [p[j] + h/6*(k1p[j]+2*k2p[j]+2*k3p[j]+k4p) for j, k4p in enumerate(f(p4,v4)[0])]
        nv_ = [v[j] + h/6*(k1v[j]+2*k2v[j]+2*k3v[j]+k4v) for j, k4v in enumerate(f(p4,v4)[1])]
        return np_, nv_

    positions  = [pos[:]]
    velocities = [vel[:]]
    cp, cv = pos[:], vel[:]
    for _ in range(steps):
        cp, cv = rk4(cp, cv, dt)
        positions.append(cp[:])
        velocities.append(cv[:])
    return positions, velocities


def collision_probability(miss_m: float, sigma: float, hbr: float = 10.0) -> float:
    # Engine parity (cas_engine.py L1927-1943) with log-space accumulation so
    # the exp(exponent)*I0(ux) product does not overflow for large u (big miss /
    # small sigma). Mathematically identical to the engine for small args; for
    # large miss it correctly returns Pc -> 0 instead of raising OverflowError.
    if sigma < 1e-3:
        return 0.0
    u = miss_m / sigma
    s = hbr  / sigma
    N = 200
    total = 0.0
    for k in range(N):
        x = s * k / N
        ux = u * x
        exponent = -0.5*(x*x + u*u)
        if exponent < -700:
            continue
        # log I0(ux): for ux<3.75 use series value; for ux>=3.75 the asymptotic
        # form is exp(ux)/sqrt(ux)*P(3.75/ux), so log I0 = ux - 0.5*ln(ux) + ln P.
        if ux < 3.75:
            i0 = _bessel_i0(ux)
            if i0 <= 0:
                continue
            log_i0 = math.log(i0)
        else:
            y = 3.75 / ux
            poly = (0.39894228 + y*(0.01328592 + y*(0.00225319 + y*(-0.00157565
                    + y*(0.00916281 + y*(-0.02057706 + y*(0.02635537
                    + y*(-0.01647633 + y*0.00392377))))))))
            if poly <= 0:
                continue
            log_i0 = ux - 0.5*math.log(ux) + math.log(poly)
        log_term = exponent + log_i0
        if log_term < -700:
            continue
        total += math.exp(log_term) * x
    total *= s / N
    return min(max(total, 0.0), 1.0)


def _bessel_i0(x: float) -> float:
    if x == 0:
        return 1.0
    ax = abs(x)
    if ax < 3.75:
        y = (x/3.75)**2
        return 1.0 + y*(3.5156229 + y*(3.0899424 + y*(1.2067492
               + y*(0.2659732 + y*(0.0360768 + y*0.0045813)))))
    else:
        y = 3.75/ax
        return (math.exp(ax)/math.sqrt(ax)) * (0.39894228
               + y*(0.01328592 + y*(0.00225319 + y*(-0.00157565
               + y*(0.00916281 + y*(-0.02057706 + y*(0.02635537
               + y*(-0.01647633 + y*0.00392377))))))))


def risk_level(Pc: float, miss_m: float) -> str:
    if Pc > 1e-4 or miss_m < 200:
        return "RED"
    elif Pc > 1e-5 or miss_m < 1000:
        return "YELLOW"
    return "GREEN"


def compute_post_maneuver_state(pos, vel, delta_v_ms, direction="prograde"):
    """
    Apply a maneuver (delta-v) to current state and return new state.

    Args:
        pos: [x, y, z] ECI position in meters
        vel: [vx, vy, vz] ECI velocity in m/s
        delta_v_ms: magnitude of delta-v in m/s
        direction: 'prograde', 'retrograde', or 'radial_out'

    Returns: (new_pos, new_vel) — same format as input
    """
    import math

    # Compute velocity unit vector
    v_mag = math.sqrt(sum(v**2 for v in vel))
    if v_mag < 1e-6:
        return pos, vel

    v_hat = [v / v_mag for v in vel]

    if direction == "prograde":
        dv = [delta_v_ms * v_hat[k] for k in range(3)]
    elif direction == "retrograde":
        dv = [-delta_v_ms * v_hat[k] for k in range(3)]
    elif direction == "radial_out":
        # Radial = position direction (away from Earth)
        r_mag = math.sqrt(sum(p**2 for p in pos))
        r_hat = [p / r_mag for p in pos] if r_mag > 1e-6 else [1, 0, 0]
        dv = [delta_v_ms * r_hat[k] for k in range(3)]
    else:
        dv = [delta_v_ms * v_hat[k] for k in range(3)]

    new_vel = [vel[k] + dv[k] for k in range(3)]
    return list(pos), new_vel


def screen_conjunctions(pos1, vel1, catalog_sats, hours=48, dt_coarse=600, threshold_km=50):
    """
    Screen post-maneuver trajectory against catalog satellites.
    (VERBATIM engine copy incl. stubbed fine-pass & unused rv quirk —
     kept for parity; the trade-space grid uses its own real fine pass.)
    """
    import math

    if not catalog_sats:
        return []

    results = []
    total_steps_coarse = int(hours * 3600 / dt_coarse)

    # Propagate primary satellite trajectory (coarse)
    try:
        traj1_pos, traj1_vel = propagate(pos1, vel1, dt_coarse, total_steps_coarse)
    except Exception:
        return []

    threshold_m = threshold_km * 1000

    for sat in catalog_sats:
        try:
            orb = sat
            s_pos, s_vel = orbital_to_eci(orb)

            # Coarse propagation of catalog object
            traj2_pos, _ = propagate(s_pos, s_vel, dt_coarse, total_steps_coarse)

            # Find minimum distance in coarse pass
            min_dist = float("inf")
            min_idx = 0
            check_len = min(len(traj1_pos), len(traj2_pos))

            for k in range(check_len):
                dx = traj1_pos[k][0] - traj2_pos[k][0]
                dy = traj1_pos[k][1] - traj2_pos[k][1]
                dz = traj1_pos[k][2] - traj2_pos[k][2]
                dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                if dist < min_dist:
                    min_dist = dist
                    min_idx = k

            if min_dist > threshold_m:
                continue

            miss_m = min_dist
            tca_hours = round(min_idx * dt_coarse / 3600, 2)

            # Compute Pc
            sigma = 100.0
            Pc = collision_probability(miss_m, sigma)
            risk = risk_level(Pc, miss_m)

            if miss_m < threshold_m:
                if min_idx < check_len:
                    rv = math.sqrt(
                        sum((traj1_pos[min_idx][k] - traj2_pos[min_idx][k])**2 for k in range(3))
                    )
                else:
                    rv = 0

                results.append({
                    "sat_name": sat.get("name", "UNKNOWN"),
                    "norad": sat.get("norad", "?"),
                    "miss_distance_m": round(miss_m, 1),
                    "miss_distance_km": round(miss_m / 1000, 2),
                    "tca_hours": tca_hours,
                    "Pc": Pc,
                    "Pc_str": f"{Pc:.2e}",
                    "risk": risk,
                    "altitude_km": round(sat.get("altitude_km", 0), 1),
                })

        except Exception:
            continue

    results.sort(key=lambda x: x["miss_distance_m"])
    return results


def compute_sigma_from_covariance(cov_data):
    """RSS sigma from CDM covariance diagonal (engine parity; for G1 gate)."""
    import math

    if not cov_data:
        return None

    cr_r = cov_data.get("cr_r")
    ct_t = cov_data.get("ct_t")
    cn_n = cov_data.get("cn_n")

    if cr_r is None or ct_t is None or cn_n is None:
        csig_r = cov_data.get("csig_r")
        csig_t = cov_data.get("csig_t")
        csig_n = cov_data.get("csig_n")
        if csig_r is not None and csig_t is not None and csig_n is not None:
            sigma = math.sqrt(csig_r**2 + csig_t**2 + csig_n**2) * 1000
            return max(sigma, 1.0)
        return None

    try:
        total_variance = abs(cr_r) + abs(ct_t) + abs(cn_n)
        if total_variance <= 0:
            return None
        sigma = math.sqrt(total_variance) * 1000
        sigma = max(sigma, 1.0)
        sigma = min(sigma, 10000.0)
        return round(sigma, 2)
    except (ValueError, TypeError):
        return None

# ════════════════════════════════════════════════════════════════════════════
# NEW CODE — Sprint #7 (catalog memo, SGP4 layer, trade space)
# ════════════════════════════════════════════════════════════════════════════

try:  # vleo.py for regime / sigma inflation (Phase 1 module)
    sys.path.insert(0, "/opt/cas")
    import vleo as _vleo
except Exception:
    _vleo = None


def _norm(n) -> str:
    s = str(n).strip().lstrip("0")
    return s if s else "0"


_CATALOG_MEMO: Dict = {"ts": 0.0, "sats": None, "index": None}


def _load_catalog() -> Dict:
    now = time.time()
    if _CATALOG_MEMO["sats"] is None or now - _CATALOG_MEMO["ts"] > _CATALOG_TTL:
        sats, idx = [], {}
        try:
            with open(CATALOG_CACHE_FILE, "r") as f:
                cat = json.load(f)
        except Exception:
            cat = {}
        for key in ("debris", "rocket_body", "payload"):
            for o in cat.get(key, []):
                l1, l2 = o.get("l1"), o.get("l2")
                if not (l1 and l2):
                    continue
                idx[_norm(o.get("norad"))] = (l1, l2)
                try:
                    ss = parse_tle(str(o.get("norad", "")), l1, l2)
                    ss["altitude_km"] = (ss["a"] - 6371000) / 1000.0  # engine parity
                    sats.append(ss)
                except Exception:
                    pass
        _CATALOG_MEMO.update(ts=now, sats=sats, index=idx)
    return _CATALOG_MEMO


def catalog_band(altitude_km: float, band_km: float = 60.0, limit: int = 120) -> List[dict]:
    """Altitude-band filter, mirrors fetch_catalog_tles (L3560) filtering."""
    sats = _load_catalog()["sats"] or []
    lo, hi = altitude_km - band_km, altitude_km + band_km
    f = [s for s in sats if lo <= s.get("altitude_km", 0) <= hi]
    if len(f) > limit:
        f.sort(key=lambda s: abs(s.get("altitude_km", 0) - altitude_km))
        f = f[:limit]
    return f


_SATREC_CACHE: Dict[Tuple[str, str], Satrec] = {}


def _satrec(l1: str, l2: str) -> Satrec:
    key = (l1, l2)
    s = _SATREC_CACHE.get(key)
    if s is None:
        s = Satrec.twoline2rv(l1, l2)
        _SATREC_CACHE[key] = s
    return s


def sgp4_state_m(l1: str, l2: str, t_utc: datetime) -> Tuple[List[float], List[float]]:
    """SGP4 state at t_utc — TEME frame, converted km→m, km/s→m/s."""
    s = _satrec(l1, l2)
    sec = t_utc.second + t_utc.microsecond * 1e-6
    jd, fr = jday(t_utc.year, t_utc.month, t_utc.day, t_utc.hour, t_utc.minute, sec)
    e, r, v = s.sgp4(jd, fr)
    if e != 0:
        raise RuntimeError(f"SGP4 error code {e} (NORAD {l1[2:7].strip()})")
    return ([r[0] * 1000.0, r[1] * 1000.0, r[2] * 1000.0],
            [v[0] * 1000.0, v[1] * 1000.0, v[2] * 1000.0])


def parse_tca_utc(raw_tca: Optional[str], db_tca: Optional[datetime]) -> datetime:
    """Authoritative TCA = raw ST CDM TCA string (UTC). DB tca column has a
    known timezone-ingest offset; used only as fallback."""
    if raw_tca:
        s = str(raw_tca).strip().replace("Z", "")
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc)
    if db_tca is not None:
        return db_tca.astimezone(timezone.utc)
    raise ValueError("No TCA available")


def _fine_states(l1: str, l2: str, tca: datetime,
                 w: int = FINE_HALF_WINDOW_S) -> Tuple[List[int], List[List[float]], List[List[float]]]:
    offsets = list(range(-w, w + 1))
    pos, vel = [], []
    for off in offsets:
        p, v = sgp4_state_m(l1, l2, tca + timedelta(seconds=off))
        pos.append(p)
        vel.append(v)
    return offsets, pos, vel


# TCA search window around the nominal CDM TCA (mirrors screen_conjunctions'
# coarse-then-fine logic so the true closest approach is captured even when the
# RK4 period differs slightly from SGP4 over a multi-hour lead, or when a large
# burn shifts the encounter epoch by seconds).
TCA_WIN_HALF_S = 180        # +/- 3 min window around nominal TCA
TCA_WIN_STEP_S = 0.5        # 0.5 s sample step (720 samples) + parabolic refine


def _propagate_into_window(pos0: List[float], vel0: List[float], lead_s: float
                          ) -> Tuple[List[List[float]], float, float]:
    """Propagate a burn-epoch state ONCE to the window entry (TCA - half), then
    sample the window with a fixed fine step in a single propagate() call
    (screen_conjunctions parity — no per-sample re-propagation).

    Returns (window_positions, t_entry_off_s, win_step_s) where window_positions[k]
    is the ECI position at offset (t_entry_off_s + k*win_step_s) from nominal TCA.
    """
    half = TCA_WIN_HALF_S
    entry = lead_s - half                 # burn-epoch -> window entry
    # Long arc to the window entry (coarse, ~30-60 s steps) — done ONCE.
    dt = max(15.0, min(60.0, entry / COARSE_MAX_STEPS))
    steps = max(1, int(entry // dt))
    Pe, Ve = propagate(list(pos0), list(vel0), dt, steps)
    pe, ve = Pe[-1], Ve[-1]
    rem = entry - steps * dt
    if rem > 1e-6:
        Pe, Ve = propagate(pe, ve, rem, 1)
        pe, ve = Pe[-1], Ve[-1]
    # Now sample the +/- half window with a fine fixed step in one propagate call.
    win_steps = int((2 * half) / TCA_WIN_STEP_S)
    Pw, _ = propagate(pe, ve, TCA_WIN_STEP_S, win_steps)
    # offset of sample 0 (= window entry) relative to nominal TCA is (entry - lead_s)
    t_entry_off = entry - lead_s          # == -half
    return Pw, t_entry_off, TCA_WIN_STEP_S


def _closest_in_windows(pri_win: List[List[float]], sec_win: List[List[float]],
                        t_entry_off: float, step_s: float):
    """Closest approach between two aligned window-sample arrays. Returns
    (miss_m, dtca_s, rel_vec, k_index) where rel_vec = primary - secondary at the
    closest sample (used to anchor the maneuver displacement onto SGP4 truth).
    Both arrays share the SAME RK4 integrator/epoch grid -> bias cancels."""
    n = min(len(pri_win), len(sec_win))
    best_d2, best_k = float("inf"), 0
    for k in range(n):
        a, b = pri_win[k], sec_win[k]
        d2 = (a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2
        if d2 < best_d2:
            best_d2, best_k = d2, k
    best_d = math.sqrt(best_d2)
    best_off = t_entry_off + best_k * step_s
    a, b = pri_win[best_k], sec_win[best_k]
    rel = [a[0]-b[0], a[1]-b[1], a[2]-b[2]]
    if 0 < best_k < n - 1:
        def dist(k):
            a, b = pri_win[k], sec_win[k]
            return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)
        dm, d0, dp = dist(best_k-1), best_d, dist(best_k+1)
        denom = (dm - 2*d0 + dp)
        if abs(denom) > 1e-9:
            frac = 0.5 * (dm - dp) / denom
            frac = max(-1.0, min(1.0, frac))
            best_off += frac * step_s
            best_d = d0 - 0.25 * (dm - dp) * frac
    return best_d, best_off, rel, best_k


def _post_burn_miss(p_burn: List[float], v_burn: List[float],
                    sec_win: List[List[float]], sec_entry_off: float, sec_step: float,
                    lead_s: float, dv_ms: float, direction: str):
    """One trade-space cell. Apply impulsive dv to the primary burn-epoch state,
    propagate the primary ONCE into the TCA window, find closest approach vs the
    precomputed secondary window (same epoch/integrator). Returns
    (miss_m_rk4, dtca_s, rel_vec, k_index). rel_vec is the RK4 relative position
    at closest approach; the caller anchors the maneuver delta onto SGP4 truth."""
    if lead_s <= TCA_WIN_HALF_S + 1:
        raise ValueError("lead time must exceed TCA window")
    pos, vel = compute_post_maneuver_state(p_burn, v_burn, dv_ms, direction)
    pri_win, pri_entry_off, pri_step = _propagate_into_window(pos, vel, lead_s)
    return _closest_in_windows(pri_win, sec_win, pri_entry_off, pri_step)


def build_trade_space(event: Dict, primary: str = "sat1",
                      leads_h: Optional[List[float]] = None,
                      dvs_ms: Optional[List[float]] = None,
                      directions: Tuple[str, ...] = DEFAULT_DIRECTIONS,
                      sigma_m: float = 100.0, hbr_m: float = 10.0,
                      do_cascade: bool = True, cascade_hours: int = 36,
                      catalog_band_km: float = 60.0, catalog_limit: int = 120,
                      now_utc: Optional[datetime] = None) -> Dict:
    t0 = time.time()
    leads_h = list(leads_h or DEFAULT_LEADS_H)
    dvs_ms = list(dvs_ms or DEFAULT_DVS_MS)
    now = now_utc or datetime.now(timezone.utc)

    if primary == "sat2":
        p_l1, p_l2 = event["tle2"]; s_l1, s_l2 = event["tle1"]
        p_name, s_name = event["sat2"], event["sat1"]
        p_norad, s_norad = event["norad2"], event["norad1"]
    else:
        p_l1, p_l2 = event["tle1"]; s_l1, s_l2 = event["tle2"]
        p_name, s_name = event["sat1"], event["sat2"]
        p_norad, s_norad = event["norad1"], event["norad2"]

    tca = event["tca_utc"]
    tta_s = (tca - now).total_seconds()
    if tta_s < 1800:
        return {"error": "TCA_TOO_CLOSE",
                "detail": f"TCA in {tta_s:.0f}s — no actionable lead time", "tta_s": tta_s}

    feasible = [h for h in leads_h if h * 3600.0 <= tta_s - 300.0]
    if not feasible:
        return {"error": "NO_FEASIBLE_LEAD", "tta_s": tta_s, "leads_requested": leads_h}

    # Regime / sigma policy
    p_orb = parse_tle(p_name or "PRIMARY", p_l1, p_l2)
    alt_km = (p_orb["a"] - 6371000) / 1000.0
    if _vleo is not None:
        regime = _vleo.detect_regime(alt_km)
        infl = _vleo.drag_sigma_inflation(alt_km) if regime != "leo" else 1.0
    else:
        regime = "leo" if alt_km >= 450 else "vleo"
        infl = 1.0
    sigma_eff = sigma_m * infl

    # SGP4 baseline geometry at TCA (truth reference, both objects SGP4). This is
    # the no-burn miss by definition — it does NOT depend on lead time. We also
    # keep the baseline relative-position VECTOR to anchor maneuver deltas onto.
    offsets, sec_pos_sgp4, sec_vel_sgp4 = _fine_states(s_l1, s_l2, tca)
    _, pri_pos_sgp4, pri_vel_sgp4 = _fine_states(p_l1, p_l2, tca)
    b_d, b_k = float("inf"), 0
    for k in range(len(offsets)):
        d = math.dist(pri_pos_sgp4[k], sec_pos_sgp4[k])
        if d < b_d:
            b_d, b_k = d, k
    rel_v = math.dist(pri_vel_sgp4[b_k], sec_vel_sgp4[b_k])
    base_rel = [pri_pos_sgp4[b_k][j] - sec_pos_sgp4[b_k][j] for j in range(3)]
    base_miss = b_d  # SGP4 truth no-burn miss (lead-independent)

    # GEOMETRY CONSISTENCY GUARD: the SGP4 no-burn miss (computed from the actual
    # TLEs) must be in the same ballpark as the CDM's stated miss. A large gap
    # means the CDM miss/Pc were not produced by these TLEs — i.e. illustrative/
    # demo data, or a CDM paired with stale/wrong ephemeris. Running a trade space
    # on inconsistent geometry would surface absurd numbers (km-scale "miss" for a
    # CDM that claims tens of metres), so we refuse with a clear, honest message
    # rather than emit misleading output.
    cdm_miss = event.get("cdm_miss_m")
    if cdm_miss is not None and cdm_miss > 0:
        gap_m = abs(base_miss - float(cdm_miss))
        # Tolerance scales a little with distance but caps generously at 50 km:
        # real CDM-vs-SGP4 differences are sub-km to a few km; demo mismatches are
        # hundreds to thousands of km.
        tol_m = max(50000.0, float(cdm_miss) * 5.0)
        if gap_m > tol_m:
            return {
                "error": "GEOMETRY_INCONSISTENT",
                "detail": (f"SGP4 no-burn miss ({base_miss/1000:.1f} km) is inconsistent "
                           f"with the CDM miss ({float(cdm_miss):.0f} m). The CDM was not "
                           f"produced by the current TLEs — likely illustrative/demo data "
                           f"or stale ephemeris. Trade space requires consistent geometry."),
                "sgp4_miss_m": round(base_miss, 1),
                "cdm_miss_m": float(cdm_miss),
                "gap_m": round(gap_m, 1),
                "is_demo_or_stale": True,
            }

    # CRITICAL (parity with cascade screen_conjunctions): evaluate every cell with
    # BOTH objects on the SAME RK4 integrator from a SHARED burn epoch per lead,
    # using a coarse-then-fine closest-approach search around the nominal TCA.
    # SGP4<->RK4 model drift over hours is km-scale and would otherwise swamp the
    # sub-km maneuver signal; same-epoch propagation makes that bias differential
    # (cancels). The TCA search captures the true minimum even when the encounter
    # epoch shifts (RK4 vs SGP4 period, or large-burn phase shift).
    def _burn_states(lead_s: float):
        t_b = tca - timedelta(seconds=lead_s)
        return (sgp4_state_m(p_l1, p_l2, t_b), sgp4_state_m(s_l1, s_l2, t_b))

    burn_by_lead = {h: _burn_states(h * 3600.0) for h in feasible}

    # Precompute the secondary window ONCE per lead; reused across dv x direction.
    sec_win_by_lead = {}
    for h in feasible:
        _, s_b = burn_by_lead[h]
        sp, sv = s_b
        sec_win_by_lead[h] = _propagate_into_window(sp, sv, h * 3600.0)

    # Per-lead RK4 no-burn relative vector at closest approach (dv=0). This is the
    # RK4 self-consistent reference; subtracting it from the burn case isolates the
    # maneuver-induced displacement with propagator bias cancelled. The RK4 no-burn
    # ABSOLUTE miss is biased (SGP4<->RK4 phase drift over the lead) and is NOT
    # reported as the baseline — base_miss (SGP4 truth) is. We expose it only as a
    # diagnostic so the bias is visible/auditable.
    noburn_rk4 = {}
    for h in feasible:
        (p0, v0), _ = burn_by_lead[h]
        sw, soff, sstep = sec_win_by_lead[h]
        m, off, rel0, k0 = _post_burn_miss(p0, v0, sw, soff, sstep, h * 3600.0, 0.0, "prograde")
        noburn_rk4[h] = {"miss_m": round(m, 1), "dtca_s": round(off, 2),
                         "rel": rel0, "k": k0}

    # No-burn baseline is lead-independent SGP4 truth (CARA MTS convention).
    noburn = {h: {"miss_m": round(base_miss, 1), "dtca_s": int(offsets[b_k]),
                  "rk4_diag_m": noburn_rk4[h]["miss_m"]} for h in feasible}

    # Grid sweep. For each cell: maneuver displacement = (RK4 burn rel) - (RK4
    # no-burn rel) at the SAME sample index k0 (differential -> bias cancels).
    # Reported miss = | base_rel (SGP4 truth) + displacement |. This anchors the
    # absolute geometry on truth while the delta carries the correct physics.
    cells = []
    for d in directions:
        for h in feasible:
            (p0, v0), _ = burn_by_lead[h]
            sw_pos, soff, sstep = sec_win_by_lead[h]
            rel0 = noburn_rk4[h]["rel"]; k0 = noburn_rk4[h]["k"]
            for dv in dvs_ms:
                pos, vel = compute_post_maneuver_state(p0, v0, dv, d)
                pri_win, pe_off, pe_step = _propagate_into_window(pos, vel, h * 3600.0)
                # evaluate burn relative vector at the SAME index as no-burn min
                kk = min(k0, len(pri_win) - 1, len(sw_pos) - 1)
                burn_rel = [pri_win[kk][j] - sw_pos[kk][j] for j in range(3)]
                disp = [burn_rel[j] - rel0[j] for j in range(3)]   # maneuver delta
                anchored = [base_rel[j] + disp[j] for j in range(3)]
                miss = math.sqrt(sum(c*c for c in anchored))
                # dTCA: recompute true min only for reporting (cheap parabolic)
                _, off, _, _ = _closest_in_windows(pri_win, sw_pos, pe_off, pe_step)
                pcv = collision_probability(miss, sigma_eff, hbr_m)
                cells.append({
                    "direction": d, "lead_hours": h, "dv_ms": dv,
                    "miss_m": round(miss, 1), "dtca_s": round(off, 2),
                    "disp_m": round(math.sqrt(sum(c*c for c in disp)), 1),
                    "pc_screen": pcv, "pc_screen_str": f"{pcv:.2e}",
                    "risk_screen": risk_level(pcv, miss),
                })

    # Recommendation (min-dv safe) — suppressed in VLEO (Phase 1: Monitor only)
    recommended, note = None, ""
    safe = [c for c in cells if c["miss_m"] >= SAFE_MISS_M and c["pc_screen"] <= SAFE_PC]
    if regime == "vleo":
        note = ("VLEO regime (<450 km): maneuver recommendation deferred to "
                "drag-aware planning (Phase 2). Grid shown for situational "
                "awareness — Monitor only.")
    elif not safe:
        note = ("No option in the evaluated grid meets miss >= 1000 m and "
                "screening Pc <= 1e-6 — extend dv/lead ranges.")
    else:
        # CARA-style selection: among options clearing the safety threshold, pick
        # the minimum delta-v; within that delta-v, the SHORTEST feasible lead
        # (act as late as safely possible — least disruptive to ops, preserves
        # decision time). Tie-break by larger resulting miss for robustness.
        min_safe_dv = min(c["dv_ms"] for c in safe)
        tier = [c for c in safe if c["dv_ms"] == min_safe_dv]
        best = sorted(tier, key=lambda c: (c["lead_hours"], -c["miss_m"]))[0]
        recommended = {**best, "fuel_cost_kg": round(best["dv_ms"] * 0.05, 4),
                       "rationale": ("Minimum delta-v clearing miss >= 1000 m and "
                                     "screening Pc <= 1e-6 (assumed-sigma), applied "
                                     "at the shortest feasible lead to preserve "
                                     "decision time.")}
        note = ("Recommended = minimum delta-v that clears the screening safety "
                "threshold, at the shortest feasible lead; operator decides.")

    # Cascade re-screen of the recommended option (engine-parity screening)
    cascade = None
    if do_cascade and recommended is not None:
        try:
            t_b = tca - timedelta(seconds=recommended["lead_hours"] * 3600.0)
            p0, v0 = sgp4_state_m(p_l1, p_l2, t_b)
            pb, vb = compute_post_maneuver_state(p0, v0, recommended["dv_ms"],
                                                 recommended["direction"])
            cat = catalog_band(alt_km, catalog_band_km, catalog_limit)
            sec = screen_conjunctions(pb, vb, cat, hours=cascade_hours)
            for srk in sec:
                if _norm(srk.get("norad")) == _norm(s_norad):
                    srk["is_event_partner"] = True
            significant = [x for x in sec
                           if x["risk"] in ("RED", "YELLOW") and not x.get("is_event_partner")]
            mx = max((x["Pc"] for x in significant), default=0.0)
            score = 1.0 if mx > 1e-4 else 0.7 if mx > 1e-5 else 0.3 if mx > 1e-6 else 0.0
            cascade = {"screened": True, "hours": cascade_hours,
                       "catalog_objects": len(cat),
                       "secondary_risks": sec[:5],
                       "significant_count": len(significant),
                       "cascade_score": score, "is_safe": score == 0.0}
        except Exception as e:
            cascade = {"screened": False, "error": str(e)}

    return {
        "kind": "maneuver_trade_space", "version": "0.1",
        "event": {
            "id": event.get("id"), "cdm_id": event.get("cdm_id"),
            "primary_name": p_name, "primary_norad": p_norad,
            "secondary_name": s_name, "secondary_norad": s_norad,
            "tca_utc": tca.isoformat(), "tta_s": int(tta_s),
            "cdm_miss_m": event.get("cdm_miss_m"), "cdm_pc": event.get("cdm_pc"),
            "risk": event.get("risk"), "source": event.get("source"),
            "db_tca_offset_s": event.get("db_tca_offset_s"),
        },
        "model": {
            "pre_burn": "SGP4 (TEME, sgp4 2.25)",
            "post_burn": "RK4 two-body + J2 (cas_engine.propagate parity)",
            "fine_window_s": FINE_HALF_WINDOW_S,
            "sigma_assumed_m": round(sigma_eff, 1),
            "sigma_inflation": round(infl, 2),
            "sigma_policy": "isotropic screening sigma — NOT operator covariance",
            "hbr_m": hbr_m, "regime": regime, "primary_alt_km": round(alt_km, 1),
        },
        "baseline": {
            "sgp4_miss_m": round(b_d, 1), "sgp4_dtca_s": int(offsets[b_k]),
            "rel_speed_ms": round(rel_v, 1),
            "cdm_miss_m": event.get("cdm_miss_m"),
            "delta_model_vs_cdm_m": (round(b_d - event["cdm_miss_m"], 1)
                                     if event.get("cdm_miss_m") is not None else None),
            "noburn_rk4_by_lead": noburn,
        },
        "grid": {"lead_hours": feasible, "dv_ms": dvs_ms,
                 "directions": list(directions), "cells": cells},
        "recommended": recommended,
        "recommendation_note": note,
        "cascade": cascade,
        "is_not": {
            "covariance_available": bool(event.get("covariance_available")),
            "note": ("Public Space-Track CDMs carry no covariance: pc_screen is an "
                     "assumed-sigma screening value. Full post-maneuver Pc trade "
                     "space unlocks with operator covariance (G1 gate)."),
        },
        "positioning": ("Decision support — CAS evaluates maneuver options; "
                        "the operator decides and executes."),
        "engine_parity": ENGINE_PARITY,
        "cells_evaluated": len(cells),
        "timing_ms": int((time.time() - t0) * 1000),
    }

# ════════════════════════════════════════════════════════════════════════════
# DB loader + offline validation
# ════════════════════════════════════════════════════════════════════════════

def _resolve_tle(norad, dsn: Optional[str] = None) -> Tuple[str, str]:
    idx = _load_catalog()["index"] or {}
    nn = _norm(norad)
    if nn in idx:
        return idx[nn]
    if dsn:
        try:
            import psycopg2
            conn = psycopg2.connect(dsn); cur = conn.cursor()
            cur.execute("SELECT tle_line1, tle_line2 FROM watchlist "
                        "WHERE norad_id IN (%s, %s) "
                        "ORDER BY last_scan DESC NULLS LAST LIMIT 1",
                        (str(norad), nn))
            row = cur.fetchone(); conn.close()
            if row and row[0] and row[1]:
                return row[0], row[1]
        except Exception:
            pass
    raise KeyError(f"No TLE for NORAD {norad} (catalog cache + watchlist)")


def _event_from_row(r, dsn: Optional[str]) -> Dict:
    (id_, cdm_id, sat1, sat2, norad1, norad2, tca_db, miss_m, pc, risk, raw) = r
    raw = raw or {}
    raw_st = raw.get("_raw_st_cdm") or {}
    tca_utc = parse_tca_utc(raw_st.get("TCA"), tca_db)
    off = None
    if raw_st.get("TCA") and tca_db is not None:
        off = round((tca_db.astimezone(timezone.utc) - tca_utc).total_seconds())
    return {
        "id": id_, "cdm_id": cdm_id, "sat1": sat1, "sat2": sat2,
        "norad1": norad1, "norad2": norad2,
        "tca_utc": tca_utc, "db_tca_offset_s": off,
        "cdm_miss_m": miss_m, "cdm_pc": pc, "risk": risk,
        "source": raw.get("source", "spacetrack_public"),
        "covariance_available": False,
        "tle1": _resolve_tle(norad1, dsn), "tle2": _resolve_tle(norad2, dsn),
    }


def load_event(dsn: Optional[str] = None, event_id: Optional[int] = None,
               cdm_id: Optional[str] = None,
               norad_pair: Optional[Tuple[str, str]] = None) -> Dict:
    import psycopg2
    dsn = dsn or _dsn()
    base = ("SELECT id, cdm_id, sat1, sat2, norad1, norad2, tca, miss_dist_m, "
            "pc, risk, raw_json FROM conjunction_events ")
    if event_id:
        q, params = base + "WHERE id=%s LIMIT 1", (event_id,)
    elif cdm_id:
        q, params = base + "WHERE cdm_id=%s ORDER BY id DESC LIMIT 1", (cdm_id,)
    elif norad_pair and norad_pair[0] and norad_pair[1]:
        q = (base + "WHERE ((norad1=%s AND norad2=%s) OR (norad1=%s AND norad2=%s)) "
             "ORDER BY id DESC LIMIT 5")
        params = (norad_pair[0], norad_pair[1], norad_pair[1], norad_pair[0])
    else:
        q = (base + "WHERE tca > now() AND risk IN ('RED','YELLOW') "
             "ORDER BY pc DESC NULLS LAST, id DESC LIMIT 25")
        params = ()
    conn = psycopg2.connect(dsn); cur = conn.cursor()
    cur.execute(q, params)
    rows = cur.fetchall()
    conn.close()
    if not rows:
        raise RuntimeError("No matching conjunction_events row")
    last_err = None
    for r in rows:
        try:
            return _event_from_row(r, dsn)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"No event with resolvable TLEs: {last_err}")


def _print_validation(res: Dict) -> None:
    if "error" in res:
        print(json.dumps(res, indent=2)); return
    ev, b = res["event"], res["baseline"]
    base_show = b.get("sgp4_miss_m", 0.0)
    print(f"\nEVENT #{ev['id']}  {ev['primary_name']} (NORAD {ev['primary_norad']}, PRIMARY)"
          f"  x  {ev['secondary_name']} (NORAD {ev['secondary_norad']})")
    print(f"TCA(UTC) {ev['tca_utc']}   T-{ev['tta_s']/3600:.1f}h   "
          f"CDM miss {ev['cdm_miss_m']} m   CDM Pc {ev['cdm_pc']}   risk {ev['risk']}")
    if ev.get("db_tca_offset_s"):
        print(f"!! DB tca vs raw ST TCA offset: {ev['db_tca_offset_s']:+d} s "
              f"(tz-ingest bug — RAW UTC used)")
    m = res["model"]
    print(f"MODEL  regime={m['regime']} alt={m['primary_alt_km']}km  "
          f"sigma={m['sigma_assumed_m']}m (x{m['sigma_inflation']})  hbr={m['hbr_m']}m")
    print(f"BASELINE sgp4: miss {b['sgp4_miss_m']:.0f} m  dTCA {b['sgp4_dtca_s']:+d}s  "
          f"relV {b['rel_speed_ms']:.0f} m/s   (model-vs-CDM {b['delta_model_vs_cdm_m']:+.0f} m"
          f" — public-TLE OD vs operator OD; grid is model-consistent)")
    print(f"No-burn baseline (SGP4 truth, lead-independent): {base_show:.0f} m")
    print("Per-lead RK4 no-burn diagnostic (shows SGP4<->RK4 phase drift, NOT baseline):")
    for h, v in b["noburn_rk4_by_lead"].items():
        print(f"   lead {h:>4}h -> RK4 diag {v['miss_m']:>10.0f} m  (dTCA {v['dtca_s']:+.1f}s)")

    leads = res["grid"]["lead_hours"]; dvs = res["grid"]["dv_ms"]
    cell = {(c["direction"], c["lead_hours"], c["dv_ms"]): c for c in res["grid"]["cells"]}
    for d in res["grid"]["directions"]:
        print(f"\n[{d}] new miss (km) — rows: lead_h, cols: dv m/s")
        print("lead\\dv" + "".join(f"{dv:>9}" for dv in dvs))
        for h in leads:
            print(f"{h:>6} " + "".join(f"{cell[(d,h,dv)]['miss_m']/1000:>9.2f}" for dv in dvs))

    # Physics checks
    import statistics
    viol = 0; kaps = []
    for d in res["grid"]["directions"]:
        for h in leads:
            prev = -1.0
            for dv in dvs:
                disp = cell[(d, h, dv)]["disp_m"]  # maneuver-induced displacement
                if disp < prev - max(50.0, 0.05 * prev):
                    viol += 1
                prev = max(prev, disp)
                if disp > 500.0:
                    kaps.append(disp / (dv * h * 3600.0))
    kmed = statistics.median(kaps) if kaps else None
    print(f"\nCHECK displacement monotonic in dv: {viol} violations -> {'PASS' if viol == 0 else 'WARN'}")
    if kmed is not None:
        ok = 1.5 <= kmed <= 4.5
        print(f"CHECK kappa = disp/(dv*dt): median {kmed:.2f} "
              f"(along-track expected ~3) -> {'PASS' if ok else 'WARN'}")
    rec = res["recommended"]
    if rec:
        print(f"\nRECOMMENDED (min-dv safe): {rec['direction']} dv={rec['dv_ms']} m/s "
              f"@ lead {rec['lead_hours']}h -> miss {rec['miss_m']/1000:.2f} km, "
              f"pc_screen {rec['pc_screen_str']}, fuel {rec['fuel_cost_kg']} kg")
    print(f"NOTE: {res['recommendation_note']}")
    c = res["cascade"]
    if c:
        if c.get("screened"):
            print(f"CASCADE: {c['catalog_objects']} objs / {c['hours']}h -> "
                  f"score {c['cascade_score']} (significant: {c['significant_count']}) "
                  f"{'SAFE' if c['is_safe'] else 'REVIEW'}")
            for s_ in c["secondary_risks"]:
                tag = " [event partner]" if s_.get("is_event_partner") else ""
                print(f"   {s_['risk']:<6} {s_['sat_name']:<22} miss {s_['miss_distance_km']:>8} km "
                      f"Pc {s_['Pc_str']}{tag}")
        else:
            print(f"CASCADE: not screened ({c.get('error')})")
    print(f"\ncells={res['cells_evaluated']}  timing={res['timing_ms']} ms")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CAS Maneuver Trade Space — offline validation")
    ap.add_argument("--event-id", type=int)
    ap.add_argument("--cdm-id")
    ap.add_argument("--norad1")
    ap.add_argument("--norad2")
    ap.add_argument("--primary", default="sat1", choices=["sat1", "sat2"])
    ap.add_argument("--dsn", default=None)
    a = ap.parse_args()
    pair = (a.norad1, a.norad2) if (a.norad1 and a.norad2) else None
    ev = load_event(a.dsn, event_id=a.event_id, cdm_id=a.cdm_id, norad_pair=pair)
    res = build_trade_space(ev, primary=a.primary)
    _print_validation(res)
