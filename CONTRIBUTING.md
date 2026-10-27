# Contributing

Thanks for helping with Dune Buggy Sandbox. Small, focused pull requests are
the easiest to review. For a bigger change, please open an issue first so we
can agree on the idea.

## Run the game and the tests

```bash
pip install -r requirements.txt
python dune_buggy_sandbox.py      # play
python -m pytest                  # unit tests
python dune_buggy_sandbox.py --frames 5   # headless check
```

`--frames N` runs the game for N frames with no window and no audio, then
exits. It is a quick way to check that the game still starts. CI
(`.github/workflows/tests.yml`) runs the unit tests and `--frames 5` on every
push to `main` and on every pull request.

## Add a ramp, loop or dune (the sandbox's "levels")

The whole game is one open map, built in code in `dune_buggy_sandbox.py`:

- Ramps: the `layout` list in `_setup_ramps()`, one `(x, y, heading)` per ramp.
- Loop rings: the `layout` list in `_setup_loops()`, one `(x, y, heading)` per ring.
- Dune mounds: the `dunes` list in `_setup_terrain()`, one `(x, y, height, radius)` per mound.

The map is centred on `(0, 0)` and is about 256 units wide, with a raised rim
at the edge. Keep new pieces inside the rim and drive the map once to check
the approach is clear.

## Add a feature

- Driving feel, sizes and colours are the tuning constants at the top of the file.
- Per-frame logic runs from `_update()` (vehicle control, camera, effects, sound, HUD).
- New procedural shapes go next to `make_box`, `make_wedge` and `make_torus`.
  Please add a test for them in `tests/`.

## Add an asset

The game runs without any external files. A few optional files are loaded
from an `Assets/` folder, which is not in this repo: the buggy model
(`Assets/CarKit/tractor.egg` with `Assets/CarKit/Textures/colormap.png`) and
two sounds. If one is missing, the game uses a placeholder box or no sound.

If you add an asset, load it the same way: when the file is missing, the game
should still start (and CI should still pass).

## Licence and assets

- Assets must be CC0 or your own work. Please say where each asset comes from
  in your pull request.
- The code is MIT licensed (see `LICENSE`). By sending a contribution, you
  agree that it is released under the same MIT licence.
