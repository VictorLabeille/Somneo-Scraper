"""État vif du collecteur, partagé entre les tâches de fond et l'API (même processus).

Ne porte que ce qui n'est pas déjà dans la base : l'instant de démarrage et le palier de cadence
en cours. La disponibilité, l'écart d'horloge, la liaison cloud se lisent, eux, dans la base.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RuntimeState:
    started_at: float = field(default_factory=time.time)
    cadence_wusrd_s: float = 60.0     # palier courant de wusrd (60 → 30 → 15 s)
    reveil_host: str | None = None    # dernière adresse connue du réveil
