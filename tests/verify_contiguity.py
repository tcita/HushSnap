"""Verify that [:, :, :3] on a (H, W, 4) array is NOT C-contiguous,
and that .copy() makes it contiguous."""

import numpy as np

# Simulate the real pipeline: a flat RGBA buffer
H, W = 1080, 1920
buf = np.arange(H * W * 4, dtype=np.uint8)

# ── Step 1: frombuffer + reshape ──────────────────────────
view_4ch = buf.reshape((H, W, 4))
print(f"reshape((H, W, 4)):  shape={view_4ch.shape}  strides={view_4ch.strides}  C_CONTIGUOUS={view_4ch.flags['C_CONTIGUOUS']}")

# ── Step 2: [:, :, :3] without .copy() ────────────────────
sliced = view_4ch[:, :, :3]
print(f"[:, :, :3]:           shape={sliced.shape}  strides={sliced.strides}  C_CONTIGUOUS={sliced.flags['C_CONTIGUOUS']}")

# ── Step 3: [:, :, :3] with .copy() ───────────────────────
copied = view_4ch[:, :, :3].copy()
print(f"[:, :, :3].copy():   shape={copied.shape}  strides={copied.strides}  C_CONTIGUOUS={copied.flags['C_CONTIGUOUS']}")

# ── Sanity check: data equivalence ────────────────────────
assert np.array_equal(sliced, copied), "Data mismatch between sliced and copied!"
print("\n✅ sliced == copied  (data identical)")

# ── Verdict ───────────────────────────────────────────────
if sliced.flags["C_CONTIGUOUS"]:
    print("\n❌ VERDICT: .copy() IS redundant — sliced array IS C-contiguous")
else:
    print("\n✅ VERDICT: .copy() is NOT redundant — sliced array is NON-contiguous; .copy() fixes it")

# ── Bonus: check if it's even a view ──────────────────────
print(f"\nBonus: sliced OWNDATA={sliced.flags['OWNDATA']}  (view of original buffer)")
print(f"Bonus: copied OWNDATA={copied.flags['OWNDATA']}  (owns its memory)")
