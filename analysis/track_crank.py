"""Track the IC8 crank angle in a video and emit ω(t).

Approach: the crank arms are saturated red against a mostly grey/black bike,
so HSV-threshold red, mask to a ring around the bottom-bracket centre (drops
the rider's shoe at the pedal end), then PCA on the masked pixels gives the
principal axis of the crank. PCA returns an undirected line, so the angle is
mod π — we unwrap frame-to-frame, which is safe at 30 fps for any realistic
crank speed (max |Δθ| << π/2 between frames).

Usage:
    python analysis/track_crank.py video.mov --output crank_video.csv
        # interactive: shows frame 0, click BB centre, then click crank tip

Or fully non-interactive once you've calibrated once:
    python analysis/track_crank.py video.mov --output crank_video.csv \
        --bb-x 612 --bb-y 502 --crank-len-px 230 --no-gui

Output columns:
    frame_idx, t_video_s, angle_mod_pi_rad, angle_unwrapped_rad, omega_rad_s
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def pick_bb_and_tip(frame: np.ndarray) -> tuple[tuple[int, int], int]:
    """Show frame, let user click BB centre then crank tip. Returns (BB, len_px)."""
    h, w = frame.shape[:2]
    scale = min(1.0, 1280 / max(h, w))
    disp = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame.copy()
    pts: list[tuple[int, int]] = []

    def cb(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            pts.append((x, y))
            cv2.circle(disp, (x, y), 6, (0, 255, 0), 2)
            cv2.imshow("calib", disp)

    print("Click 1: bottom-bracket centre. Click 2: crank tip (pedal axle).")
    cv2.namedWindow("calib")
    cv2.setMouseCallback("calib", cb)
    cv2.imshow("calib", disp)
    while len(pts) < 2:
        if cv2.waitKey(20) & 0xFF == 27:
            cv2.destroyAllWindows()
            sys.exit("aborted")
    cv2.destroyAllWindows()
    (x1, y1), (x2, y2) = pts
    bb = (int(x1 / scale), int(y1 / scale))
    tip = (int(x2 / scale), int(y2 / scale))
    length = int(math.hypot(tip[0] - bb[0], tip[1] - bb[1]))
    print(f"BB=({bb[0]}, {bb[1]})  crank length={length}px")
    return bb, length


def red_mask(bgr: np.ndarray) -> np.ndarray:
    """HSV mask for the bright red crank. Hue wraps, so two ranges."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, (0, 110, 60), (10, 255, 255))
    m2 = cv2.inRange(hsv, (170, 110, 60), (180, 255, 255))
    return cv2.bitwise_or(m1, m2)


def ring_mask(shape_hw: tuple[int, int], bb: tuple[int, int],
              r_in: int, r_out: int) -> np.ndarray:
    h, w = shape_hw
    mask = np.zeros((h, w), np.uint8)
    cv2.circle(mask, bb, r_out, 255, -1)
    cv2.circle(mask, bb, r_in, 0, -1)
    return mask


def pca_angle(mask: np.ndarray, bb: tuple[int, int]) -> float | None:
    """Principal-axis angle of mask pixels (mod π), measured CCW from +x.

    Recentre on the bottom bracket so the axis passes through the pivot; this
    is more accurate than centring on the pixel centroid when one arm is
    occluded. Returns None if too few pixels survive.
    """
    ys, xs = np.where(mask > 0)
    if xs.size < 200:
        return None
    dx = xs.astype(np.float64) - bb[0]
    dy = ys.astype(np.float64) - bb[1]
    # 2x2 second-moment matrix about BB.
    sxx = float(np.mean(dx * dx))
    syy = float(np.mean(dy * dy))
    sxy = float(np.mean(dx * dy))
    # Largest eigenvector of [[sxx, sxy], [sxy, syy]].
    tr = sxx + syy
    det = sxx * syy - sxy * sxy
    disc = max(0.0, (tr * tr) / 4 - det)
    lam = tr / 2 + math.sqrt(disc)
    # Eigenvector for lam: ((lam - syy), sxy) (or (sxy, lam - sxx)).
    vx = lam - syy
    vy = sxy
    if abs(vx) < 1e-9 and abs(vy) < 1e-9:
        vx, vy = sxy, lam - sxx
    # Image y-axis is downward; flip so angle is in math convention (y up).
    return math.atan2(-vy, vx) % math.pi


def unwrap_mod_pi(prev: float | None, cur: float) -> float:
    """Continuous unwrap of a mod-π signal. Returns absolute angle."""
    if prev is None:
        return cur
    # Bring cur into the half-open interval centred on prev.
    delta = cur - (prev % math.pi)
    if delta > math.pi / 2:
        delta -= math.pi
    elif delta < -math.pi / 2:
        delta += math.pi
    return prev + delta


def run(args):
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"could not open {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"video: {n_frames} frames @ {fps:.3f} fps")

    if args.bb_x is not None and args.bb_y is not None and args.crank_len_px is not None:
        bb = (args.bb_x, args.bb_y)
        crank_len = args.crank_len_px
    else:
        ok, frame0 = cap.read()
        if not ok:
            sys.exit("could not read frame 0")
        if args.no_gui:
            sys.exit("--no-gui requires --bb-x --bb-y --crank-len-px")
        bb, crank_len = pick_bb_and_tip(frame0)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    r_in = max(8, int(crank_len * args.r_in_frac))
    r_out = int(crank_len * args.r_out_frac)
    print(f"ring mask: r_in={r_in} r_out={r_out}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    f_csv = out_path.open("w", newline="")
    w = csv.writer(f_csv)
    w.writerow(["frame_idx", "t_video_s", "angle_mod_pi_rad",
                "angle_unwrapped_rad", "omega_rad_s"])

    debug_dir: Path | None = None
    if args.debug_every:
        debug_dir = out_path.with_suffix("").parent / (out_path.stem + "_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        print(f"writing debug overlays every {args.debug_every} frames to {debug_dir}")

    prev_unwrapped: float | None = None
    prev_t: float | None = None
    rmask: np.ndarray | None = None
    n_failed = 0
    t_start = time.time()

    for idx in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            break
        if rmask is None:
            rmask = ring_mask(frame.shape[:2], bb, r_in, r_out)
        red = red_mask(frame)
        m = cv2.bitwise_and(red, rmask)
        ang = pca_angle(m, bb)
        t = idx / fps
        if ang is None:
            n_failed += 1
            w.writerow([idx, f"{t:.6f}", "", "", ""])
            continue
        unwrapped = unwrap_mod_pi(prev_unwrapped, ang)
        if prev_unwrapped is not None and prev_t is not None:
            omega = (unwrapped - prev_unwrapped) / (t - prev_t)
        else:
            omega = ""
        w.writerow([idx, f"{t:.6f}", f"{ang:.6f}", f"{unwrapped:.6f}",
                    f"{omega:.6f}" if omega != "" else ""])
        prev_unwrapped = unwrapped
        prev_t = t

        if debug_dir is not None and idx % args.debug_every == 0:
            ovl = frame.copy()
            cv2.circle(ovl, bb, r_in, (0, 255, 255), 2)
            cv2.circle(ovl, bb, r_out, (0, 255, 255), 2)
            ovl[m > 0] = (0, 255, 0)
            L = int(r_out * 1.05)
            x2 = int(bb[0] + L * math.cos(ang))
            y2 = int(bb[1] - L * math.sin(ang))  # math y flip
            cv2.line(ovl, (bb[0] - (x2 - bb[0]), bb[1] - (y2 - bb[1])),
                     (x2, y2), (0, 0, 255), 2)
            label = f"f={idx} t={t:.2f}s ang={math.degrees(ang):.1f}deg"
            if isinstance(omega, float):
                label += f" w={omega:.2f}rad/s"
            cv2.putText(ovl, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imwrite(str(debug_dir / f"f{idx:06d}.jpg"), ovl,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
        if idx and idx % 1000 == 0:
            elapsed = time.time() - t_start
            rate = idx / elapsed
            eta = (n_frames - idx) / rate
            print(f"  {idx}/{n_frames}  ({rate:.0f} fps, eta {eta:.0f}s, "
                  f"{n_failed} failed)")

    f_csv.close()
    cap.release()
    print(f"wrote {out_path} ({n_failed} frames with too few red pixels)")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video")
    p.add_argument("--output", "-o", default="crank_video.csv")
    p.add_argument("--bb-x", type=int)
    p.add_argument("--bb-y", type=int)
    p.add_argument("--crank-len-px", type=int)
    p.add_argument("--r-in-frac", type=float, default=0.18,
                   help="Inner ring radius as fraction of crank length.")
    p.add_argument("--r-out-frac", type=float, default=0.85,
                   help="Outer ring radius as fraction of crank length "
                        "(< 1 to drop the shoe at pedal tip).")
    p.add_argument("--no-gui", action="store_true",
                   help="Refuse to open windows; requires --bb-x --bb-y --crank-len-px.")
    p.add_argument("--debug-every", type=int, default=0,
                   help="If >0, save an annotated overlay every N frames "
                        "(BB, ring, mask, detected axis) for sanity-checking.")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
