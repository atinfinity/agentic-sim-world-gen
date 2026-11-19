"""The name-based reach (`_scale_reach` / `_dims_on_eye`) checked against the fit's own Jacobian.

A width / depth rests on the camera height when moving eye_height moves it in proportion: by the implicit
function theorem at the fit's optimum, dx/d(eye) = -(J^T J)^-1 J^T dr/d(eye), and the elasticity
(eye / W) dW/d(eye) is about 1 for such a dimension and about 0 for one something else holds. The fits of
the reach tests' scenes are run again here with `_scale_evidence` recording, and every dimension `on_eye`
names must have an elasticity near 1, every other measured one near 0. A new kind of landmark the name-based reach does
not know shows up here as a disagreement.

The Jacobian does not replace the reach: `reaches` / `holds` ask whether a door top is joined to the photos
measuring a dimension, not how much of the scale it carries (with the ceiling seen too, a joined door top
carries ~0 of it), and the warnings name the photos that measure the dimension."""

import inspect
import sys

import pytest
import test_depth
import test_photo_fit as tpf

from agentic_sim_world_gen import photo_fit as pf

ON_EYE = 0.9  # elasticities seen: 1.00 for the dimensions on_eye names, <= 0.75 for the others


def _eye_elasticity(m: pf._Model, req: pf.FitRequest, p: dict) -> dict[str, float]:
    names = list(m.names)
    x = [p[n] for n in names]
    r0 = m.residuals(x)
    J = []
    for i in range(len(x)):
        h = 1e-6 * max(m.scale[i], abs(x[i]))
        xi = x[:]
        xi[i] += h
        J.append([(a - b) / h for a, b in zip(m.residuals(xi), r0)])
    N = [[sum(a * b for a, b in zip(J[i], J[j])) + (1e-9 * (1 + sum(c * c for c in J[i])) if i == j else 0.0)
          for j in range(len(x))] for i in range(len(x))]
    d = 1e-3 * req.eye_height
    m2 = pf._Model(m.req.model_copy(update={"eye_height": req.eye_height + d}))  # m.req: the pairs the fit kept
    m2.size_priors = m.size_priors
    r1 = m2.residuals(x)
    assert list(m2.names) == names and len(r1) == len(r0)
    dr = [(a - b) / d for a, b in zip(r1, r0)]
    dx = pf._solve(N, [-sum(a * b for a, b in zip(J[i], dr)) for i in range(len(x))])
    assert dx is not None, "singular normal matrix"
    return {k: dx[m.index[k]] * req.eye_height / p[k] for k in ("width", "depth")}


def _called_by_fit() -> bool:
    f = sys._getframe(2)  # record's caller
    return f.f_code is pf._fit.__code__


SCENES = [tpf.test_scale_from_eye_height_without_ceiling, tpf.test_door_top_holds_only_its_photos,
          tpf.test_ceiling_seen_no_dimension_rests_on_the_camera_height, tpf.test_a_shared_corner_does_not_tie_the_scale,
          test_depth.test_depth_sets_the_scale_without_ceiling]


# (left out: the scenes whose fits report nothing per dimension - test_the_scale_spreads_through_the_room,
# test_no_dimension_measured_keeps_the_prior_used - and those that reach their rests by calling _scale_evidence
# at a start, not through a fit - test_one_wall_each_is_not_held_by_one_side)
RESTS = [tpf.test_scale_from_eye_height_without_ceiling, tpf.test_door_top_holds_only_its_photos,
         tpf.test_a_shared_corner_does_not_tie_the_scale, test_depth.test_depth_sets_the_scale_without_ceiling]


@pytest.mark.parametrize("scene", SCENES, ids=lambda f: f.__name__)
def test_on_eye_agrees_with_the_jacobian(scene, monkeypatch):
    seen = []
    orig = pf._scale_evidence

    def record(m, req, p, cams, H):
        out = orig(m, req, p, cams, H)
        # only the fits' own reports: a test calling _scale_evidence directly passes a start, not an optimum;
        # a fit holding cameras beyond a wall in place (_hold, compare's second fit) is held in metres
        if _called_by_fit() and not req._hold:
            metric, _, on_eye, measured = pf._dims_on_eye(m, req)
            dims = {d for d, v in pf._scale_reach(m, req)[1].items() if v[0]}  # the measured ones
            if metric and measured:
                seen.append((set(on_eye), {k: v for k, v in _eye_elasticity(m, req, p).items() if k in dims}))
        return out

    monkeypatch.setattr(pf, "_scale_evidence", record)
    args = inspect.signature(scene).parameters
    scene(**({"monkeypatch": monkeypatch} if "monkeypatch" in args else {}))
    assert seen, "no fit was checked"
    for on_eye, el in seen:
        assert all(-0.1 < v < 1.5 for v in el.values()), el  # outside: the derivative is broken
        assert {k for k, v in el.items() if v > ON_EYE} == on_eye, (on_eye, el)
    if scene in RESTS:  # the check is not empty: these scenes have dimensions on the camera height
        assert any(on_eye for on_eye, _ in seen), seen
