"""First-run setup window: shows asset extraction progress in a pygame window
instead of the terminal.

  ui = SetupScreen('UGH.EXE')
  ui.update(stage, fraction, detail)      # draws, keeps the window responsive
  ui.close()

update() raises SetupAborted when the window is closed or Esc is pressed.
"""
from __future__ import annotations

import sys
import time

import pygame

STAGES = ('Graphics, levels and sprites', 'Music and sound effects', 'Copying files')
WEIGHTS = (0.05, 0.90, 0.05)                 # share of the overall bar per stage
W, H = 640, 360

BG, PANEL, TEXT, DIM = (14, 18, 28), (28, 34, 50), (232, 236, 244), (140, 150, 172)
ACCENT, DONE, ERROR = (255, 176, 46), (96, 200, 120), (240, 96, 96)


class SetupAborted(Exception):
    """The user closed the setup window."""


def hide_own_console():
    """Windows: hide the console window if this process owns it (started by
    double-click), never one the user launched us from."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        procs = (ctypes.c_uint * 4)()
        if kernel32.GetConsoleProcessList(procs, 4) == 1:
            hwnd = kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


class SetupScreen:
    def __init__(self, exe_name: str):
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((W, H))
        pygame.display.set_caption('UGH! - first-time setup')
        self.big = pygame.font.Font(None, 40)
        self.mid = pygame.font.Font(None, 26)
        self.small = pygame.font.Font(None, 22)
        self.exe_name = exe_name
        self.start = time.monotonic()
        self.stage, self.frac, self.detail = 0, 0.0, ''
        hide_own_console()
        self.update(0, 0.0)

    # ------------------------------------------------------------ drawing
    def _text(self, font, text, color, pos):
        self.screen.blit(font.render(text, True, color), pos)

    def _bar(self, x, y, w, h, frac, color):
        pygame.draw.rect(self.screen, PANEL, (x, y, w, h), border_radius=5)
        if frac > 0:
            pygame.draw.rect(self.screen, color, (x, y, max(h, int(w * frac)), h), border_radius=5)

    def _draw(self):
        s = self.screen
        s.fill(BG)
        self._text(self.big, 'UGH!', ACCENT, (40, 30))
        self._text(self.mid, 'First-time setup: unpacking the game from your original files',
                   TEXT, (40, 78))
        self._text(self.small, f'Source: {self.exe_name}. This only happens once.', DIM, (40, 106))
        y = 150
        for i, name in enumerate(STAGES):
            mark, color = ('done', DONE) if i < self.stage else (('...', ACCENT) if i == self.stage else ('', DIM))
            self._text(self.mid, name, TEXT if i <= self.stage else DIM, (40, y))
            self._text(self.mid, mark, color, (W - 40 - self.mid.size(mark)[0], y))
            y += 30
        overall = sum(WEIGHTS[:self.stage]) + WEIGHTS[min(self.stage, 2)] * self.frac
        self._bar(40, 262, W - 80 - 56, 22, min(overall, 1.0), ACCENT)
        self._text(self.mid, f'{min(overall, 1.0):.0%}', TEXT, (W - 40 - 48, 264))
        detail = self.detail
        while detail and self.small.size(detail)[0] > W - 80:
            detail = detail[:-1]
        self._text(self.small, detail, DIM, (40, 292))
        self._text(self.small, f'{time.monotonic() - self.start:.0f} s', DIM, (40, 320))

    def update(self, stage: int, frac: float, detail: str = ''):
        self.stage, self.frac, self.detail = stage, frac, detail
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                raise SetupAborted()
        self._draw()
        pygame.display.flip()

    def error(self, message: str):
        """Show a failure and wait for a key press or closing the window."""
        self._draw()
        pygame.draw.rect(self.screen, BG, (0, 140, W, 200))
        self._text(self.mid, 'Setup failed', ERROR, (40, 150))
        for i, line in enumerate(_wrap(message, 70)[:6]):
            self._text(self.small, line, TEXT, (40, 190 + 22 * i))
        self._text(self.small, 'Press any key to close.', DIM, (40, 330))
        pygame.display.flip()
        while True:
            for ev in pygame.event.wait(), :
                if ev.type in (pygame.QUIT, pygame.KEYDOWN):
                    return

    def close(self):
        pygame.display.quit()


def _wrap(text: str, width: int) -> list[str]:
    lines = []
    for para in text.splitlines() or ['']:
        while len(para) > width:
            cut = para.rfind(' ', 0, width) or width
            lines.append(para[:cut])
            para = para[cut:].lstrip()
        lines.append(para)
    return lines
