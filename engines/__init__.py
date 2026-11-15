"""Engine registry. Local models load lazily on first use and are evicted
(least recently used first) when GPU memory runs short."""
from .anadil import AnadilEngine
from .base import Engine
from .chatterbox_engine import ChatterboxEngine
from .chatterbox_ml import ChatterboxMultilingualEngine
from .edge import EdgeEngine
from .f5 import F5Engine
from .mms import MMSEngine
from .navoiy import NavoiyEngine
from .qwen import Qwen3UzbekEngine, SayroEngine
from .omnivoice_engine import OmniVoiceEngine

ENGINES: dict[str, Engine] = {
    e.id: e
    for e in (EdgeEngine(), NavoiyEngine(), OmniVoiceEngine(), Qwen3UzbekEngine(), SayroEngine(), ChatterboxEngine(), ChatterboxMultilingualEngine(), AnadilEngine(), F5Engine(), MMSEngine())
}


def get(engine_id: str) -> Engine | None:
    return ENGINES.get(engine_id)


def make_room_for(engine: Engine, headroom_gb: float = 1.5):
    """Unload other local models until `engine` should fit in free GPU memory."""
    try:
        import torch

        if not torch.cuda.is_available():
            return
        loaded = sorted(
            (e for e in ENGINES.values() if e.kind == "local" and e.loaded and e is not engine),
            key=lambda e: e.last_used,
        )
        for victim in loaded:
            free, _ = torch.cuda.mem_get_info()
            if free / 2**30 >= engine.vram_gb + headroom_gb:
                break
            victim.unload()
    except ImportError:
        pass
