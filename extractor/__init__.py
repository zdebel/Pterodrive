"""Asset extraction, level decoding, and audio synthesis tools for UGH!"""
from __future__ import annotations

from .extract_assets import (
    BANKS,
    LEVEL_PARA,
    PAL_PARA,
    SCREEN_PARAS,
    SPRITE_TABLE_LEN,
    SPRITE_TABLE_PARA,
    extract_all,
    ice_unpack,
    load_image,
    vga6to8,
    write_png,
)
from .decode_levels import Game
from .render_levels import load_sprites


def export_all_audio(exe: str, out_dir: str, quiet: bool = False, on_progress=None):
    """Render music and effects to WAV (see extractor.export_audio).
    on_progress(fraction, detail) is called while rendering and may raise to abort."""
    from .export_audio import export_all
    export_all(exe, out_dir, quiet=quiet, on_progress=on_progress)


__all__ = [
    'BANKS',
    'LEVEL_PARA',
    'PAL_PARA',
    'SCREEN_PARAS',
    'SPRITE_TABLE_LEN',
    'SPRITE_TABLE_PARA',
    'extract_all',
    'ice_unpack',
    'load_image',
    'vga6to8',
    'write_png',
    'Game',
    'load_sprites',
    'export_all_audio',
]
