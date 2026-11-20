# UGH! — Python Port

A Python port of the 1992 DOS game **UGH!** (PlayByte / Bones Park).

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)

The game data is not included: you need your own copy of the original `UGH.EXE`.
On the first start the port finds it (next to the program or in the current folder,
otherwise it asks you to pick the file) and extracts everything it needs, which takes
a few seconds with the native core, about a minute without (see below). Progress is shown in a
small window. Later starts are instant.

---

## Running

### Ready-made build

Download the archive for your system from the
[Releases](https://github.com/zdebel/Pterodrive/releases) page, unpack it, put `UGH.EXE`
next to `Pterodrive` (optional) and run `Pterodrive`.

The builds are not code-signed: on macOS right-click the file and choose *Open* the first
time; Windows SmartScreen may ask you to confirm as well.

### From source

Requires Python 3.10 or newer.

```bash
git clone https://github.com/zdebel/Pterodrive.git
cd Pterodrive
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 main.py
```

**Faster first start (optional).** Extraction renders the music with an OPL2 chip emulator.
A small C core makes that about 10x faster (about 60 s to 6 s) with bit-identical output;
the ready-made builds already include it. From source, build it with any C compiler:

```bash
python3 -m extractor.build_native
```

Without it the emulator falls back to numpy automatically; `PTERODRIVE_NO_NATIVE=1` forces
that path.

---

## Controls

### In game

| Action | Player 1 | Player 2 |
| :--- | :--- | :--- |
| Fly up | Up arrow / Numpad 8 | W |
| Fly down | Down arrow / Numpad 2 | Z |
| Left | Left arrow / Numpad 4 | A |
| Right | Right arrow / Numpad 6 | S |
| Drop stone | Right Ctrl / Numpad 0 | Left Ctrl / Numpad Enter |

* `P` pause (any key resumes)
* `Esc` end the game
* `F11` toggle fullscreen

### Menu

| Key | Action |
| :--- | :--- |
| `F1` | Start game |
| `F2` | Enter a level password |
| `F3` | Difficulty (Novice / Pro / Ace) |
| `F4` | 1 or 2 players |
| `F5` | Controller setup |
| `C` | Back to the intro |
| `Esc` / `Q` | Quit |

Gamepads and joysticks are supported.

---

## Command-line options

| Option | Meaning |
| :--- | :--- |
| `--exe PATH` | Path to the original `UGH.EXE` (first start only) |
| `--fullscreen` | Start in fullscreen |
| `--scale N` | Window size, 320×N by 240×N (default 3) |
| `--mode 1\|2` | Number of players |
| `--difficulty 0\|1\|2` | Novice, Pro or Ace |
| `--level N` | Start at level N (with `--skip-frontend`) |
| `--skip-frontend` | Skip the title screen and menu |
| `--scene NAME` | Play a cutscene and quit: `title`, `second_intro`, `game_intro`, `ending` |
| `--no-sound` | Disable sound |
| `--no-joystick` | Ignore gamepads |
| `--text-setup` | Show the first-start extraction in the terminal instead of a window |
| `--version` | Show the version |

Example: `python3 main.py --mode 2 --difficulty 2`

---

## License

MIT, see [LICENSE](LICENSE). UGH! and its data are © 1992 PlayByte / Bones Park and are not part of this project.
